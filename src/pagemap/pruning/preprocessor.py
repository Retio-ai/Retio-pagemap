# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""HTMLRAG Pass 1 cleaning + lxml DOM parsing + Atomic chunk decomposition.

Pipeline:
  1. Extract special scripts (JSON-LD, OG meta, RSC payload) before removal
  2. HTMLRAG Pass 1: remove empty tags, collapse single-child wrappers, strip noise
  3. Parse cleaned HTML with lxml
  4. Recursively decompose DOM into atomic HtmlChunk list
"""

from __future__ import annotations

import logging
import re

import lxml.html
from lxml import etree

from pagemap.pruning import ChunkType, HtmlChunk, PruningError

logger = logging.getLogger(__name__)

# Tags to remove entirely (after extracting specials)
_REMOVE_TAGS = {"script", "style", "svg", "noscript", "link", "path", "defs", "iframe"}

# Inline tags that don't form their own chunks
_INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "br",
    "cite",
    "code",
    "data",
    "del",
    "dfn",
    "em",
    "i",
    "ins",
    "kbd",
    "mark",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
    "wbr",
    "img",
    "label",
}

# Tags that are atomic boundaries (whole subtree = 1 chunk)
_ATOMIC_TAGS = {
    "table": ChunkType.TABLE,
    "thead": ChunkType.TABLE,
    "tbody": ChunkType.TABLE,
    "ul": ChunkType.LIST,
    "ol": ChunkType.LIST,
    "dl": ChunkType.LIST,
    "figure": ChunkType.MEDIA,
    "form": ChunkType.FORM,
}

# Heading tags
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Semantic container tags that recurse into children
_CONTAINER_TAGS = {"article", "section", "main", "aside", "nav", "header", "footer", "div", "body", "html"}

# RSC payload pattern
_RSC_PATTERN = re.compile(r"self\.__next_f\.push\(\s*\[.*?\]\s*\)", re.DOTALL)

_RSC_PAYLOAD_TRUNCATE_LEN = 500

_MAX_DECOMPOSE_DEPTH = 100


def _extract_json_ld(html: str) -> list[HtmlChunk]:
    """Extract JSON-LD scripts from raw HTML before stripping."""
    chunks = []
    pattern = re.compile(
        r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for i, m in enumerate(pattern.finditer(html)):
        content = m.group(1).strip()
        if content:
            chunks.append(
                HtmlChunk(
                    xpath=f"/json-ld[{i}]",
                    html=f'<script type="application/ld+json">{content}</script>',
                    text=content,
                    tag="script",
                    chunk_type=ChunkType.META,
                    attrs={"type": "application/ld+json"},
                )
            )
    return chunks


def _extract_og_meta(html: str) -> list[HtmlChunk]:
    """Extract Open Graph meta tags."""
    chunks = []
    pattern = re.compile(
        r'<meta[^>]*property\s*=\s*["\']og:([^"\']*)["\'][^>]*content\s*=\s*["\']([^"\']*)["\'][^>]*/?>',
        re.IGNORECASE,
    )
    # Also handle reversed attribute order
    pattern2 = re.compile(
        r'<meta[^>]*content\s*=\s*["\']([^"\']*)["\'][^>]*property\s*=\s*["\']og:([^"\']*)["\'][^>]*/?>',
        re.IGNORECASE,
    )
    og_data = {}
    for m in pattern.finditer(html):
        og_data[f"og:{m.group(1)}"] = m.group(2)
    for m in pattern2.finditer(html):
        og_data[f"og:{m.group(2)}"] = m.group(1)

    if og_data:
        text_parts = [f"{k}={v}" for k, v in og_data.items()]
        chunks.append(
            HtmlChunk(
                xpath="/og-meta",
                html=" ".join(f'<meta property="{k}" content="{v}"/>' for k, v in og_data.items()),
                text="; ".join(text_parts),
                tag="meta",
                chunk_type=ChunkType.META,
                attrs=og_data,
            )
        )
    return chunks


def _extract_rsc_data(html: str) -> list[HtmlChunk]:
    """Extract Next.js RSC payload data (Naver News date extraction)."""
    chunks = []
    # Find self.__next_f.push() calls
    pattern = re.compile(
        r"<script[^>]*>((?:(?!</script>).)*self\.__next_f\.push\((?:(?!</script>).)*\)(?:(?!</script>).)*)(?:</script>)",
        re.DOTALL | re.IGNORECASE,
    )
    date_pattern = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")

    for i, m in enumerate(pattern.finditer(html)):
        content = m.group(1)
        # Look for date-like data in RSC payloads
        dates = date_pattern.findall(content)
        if dates:
            text = f"RSC dates: {', '.join(dates)}"
            chunks.append(
                HtmlChunk(
                    xpath=f"/rsc-data[{i}]",
                    html=f"<script>{content[:_RSC_PAYLOAD_TRUNCATE_LEN]}</script>",
                    text=text,
                    tag="script",
                    chunk_type=ChunkType.RSC_DATA,
                    attrs={"dates": dates},
                )
            )
    return chunks


def _clean_html_pass1(html: str) -> str:
    """HTMLRAG Pass 1: pre-chunking cleaning.

    - Remove comments
    - Remove empty elements
    - Collapse single-child wrapper divs
    - Normalize whitespace
    """
    # Remove HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

    # Remove tags in _REMOVE_TAGS
    for tag in _REMOVE_TAGS:
        html = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Self-closing
        html = re.sub(rf"<{tag}\b[^>]*/>", "", html, flags=re.IGNORECASE)

    # Collapse consecutive whitespace (but keep single newlines)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n+", "\n", html)

    return html.strip()


def _get_text(el: lxml.html.HtmlElement) -> str:
    """Get all text content from an element."""
    return (el.text_content() or "").strip()


def _get_html(el: lxml.html.HtmlElement) -> str:
    """Serialize element to HTML string."""
    return etree.tostring(el, encoding="unicode", method="html")


def _get_semantic_attrs(el: lxml.html.HtmlElement) -> dict:
    """Extract semantically meaningful attributes."""
    attrs = {}
    for key in (
        "role",
        "aria-label",
        "aria-labelledby",
        "itemprop",
        "itemtype",
        "property",
        "content",
        "datetime",
        "href",
        "src",
        "alt",
        "title",
        "class",
    ):
        val = el.get(key)
        if val:
            attrs[key] = val
    return attrs


def _has_block_children(el: lxml.html.HtmlElement) -> bool:
    """Check if element has any block-level children."""
    for child in el:
        if isinstance(child, lxml.html.HtmlElement):
            tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if tag and tag not in _INLINE_TAGS:
                return True
    return False


def _compute_depth(el: lxml.html.HtmlElement) -> int:
    """Compute depth from root."""
    depth = 0
    parent = el.getparent()
    while parent is not None:
        depth += 1
        parent = parent.getparent()
    return depth


def _is_in_main(el: lxml.html.HtmlElement) -> bool:
    """Check if element is inside a <main> tag."""
    parent = el.getparent()
    while parent is not None:
        tag = parent.tag.lower() if isinstance(parent.tag, str) else ""
        if tag == "main":
            return True
        parent = parent.getparent()
    return False


def _decompose_element(
    el: lxml.html.HtmlElement,
    tree: etree._ElementTree,
    *,
    depth: int = 0,
    max_depth: int = _MAX_DECOMPOSE_DEPTH,
) -> list[HtmlChunk]:
    """Recursively decompose a DOM element into atomic chunks."""
    if depth > max_depth:
        logger.warning(
            "Max decomposition depth %d exceeded at <%s>, skipping subtree",
            max_depth,
            el.tag if isinstance(el.tag, str) else "unknown",
        )
        return []

    if not isinstance(el.tag, str):
        return []

    tag = el.tag.lower()

    # Skip removed tags that survived cleaning
    if tag in _REMOVE_TAGS:
        return []

    text = _get_text(el)

    # Atomic boundary tags — whole subtree = 1 chunk
    if tag in _ATOMIC_TAGS:
        if not text:
            return []
        return [
            HtmlChunk(
                xpath=tree.getpath(el),
                html=_get_html(el),
                text=text,
                tag=tag,
                chunk_type=_ATOMIC_TAGS[tag],
                attrs=_get_semantic_attrs(el),
                parent_xpath=tree.getpath(el.getparent()) if el.getparent() is not None else "",
                depth=_compute_depth(el),
                in_main=_is_in_main(el) or tag == "main",
            )
        ]

    # Headings
    if tag in _HEADING_TAGS:
        if not text:
            return []
        return [
            HtmlChunk(
                xpath=tree.getpath(el),
                html=_get_html(el),
                text=text,
                tag=tag,
                chunk_type=ChunkType.HEADING,
                attrs=_get_semantic_attrs(el),
                parent_xpath=tree.getpath(el.getparent()) if el.getparent() is not None else "",
                depth=_compute_depth(el),
                in_main=_is_in_main(el),
            )
        ]

    # Paragraph — independent text block
    if tag == "p":
        if not text:
            return []
        return [
            HtmlChunk(
                xpath=tree.getpath(el),
                html=_get_html(el),
                text=text,
                tag=tag,
                chunk_type=ChunkType.TEXT_BLOCK,
                attrs=_get_semantic_attrs(el),
                parent_xpath=tree.getpath(el.getparent()) if el.getparent() is not None else "",
                depth=_compute_depth(el),
                in_main=_is_in_main(el),
            )
        ]

    # Container tags and div — recurse into children
    if tag in _CONTAINER_TAGS:
        if _has_block_children(el):
            # Recurse into children
            chunks = []
            for child in el:
                if isinstance(child, lxml.html.HtmlElement):
                    chunks.extend(_decompose_element(child, tree, depth=depth + 1, max_depth=max_depth))
            return chunks
        else:
            # Leaf div with only inline content
            if not text:
                return []
            return [
                HtmlChunk(
                    xpath=tree.getpath(el),
                    html=_get_html(el),
                    text=text,
                    tag=tag,
                    chunk_type=ChunkType.TEXT_BLOCK,
                    attrs=_get_semantic_attrs(el),
                    parent_xpath=tree.getpath(el.getparent()) if el.getparent() is not None else "",
                    depth=_compute_depth(el),
                    in_main=_is_in_main(el),
                )
            ]

    # Any other block-level element
    if tag not in _INLINE_TAGS:
        if _has_block_children(el):
            chunks = []
            for child in el:
                if isinstance(child, lxml.html.HtmlElement):
                    chunks.extend(_decompose_element(child, tree, depth=depth + 1, max_depth=max_depth))
            return chunks
        elif text:
            return [
                HtmlChunk(
                    xpath=tree.getpath(el),
                    html=_get_html(el),
                    text=text,
                    tag=tag,
                    chunk_type=ChunkType.TEXT_BLOCK,
                    attrs=_get_semantic_attrs(el),
                    parent_xpath=tree.getpath(el.getparent()) if el.getparent() is not None else "",
                    depth=_compute_depth(el),
                    in_main=_is_in_main(el),
                )
            ]

    return []


def preprocess(raw_html: str) -> tuple[list[HtmlChunk], lxml.html.HtmlElement]:
    """Preprocess: extract specials → clean → parse. No chunk decomposition.

    Returns:
        (meta_chunks, doc) — extracted meta chunks and lxml DOM root.
    """
    if not raw_html or not raw_html.strip():
        raise PruningError("Empty HTML input")

    meta_chunks: list[HtmlChunk] = []
    meta_chunks.extend(_extract_json_ld(raw_html))
    meta_chunks.extend(_extract_og_meta(raw_html))
    meta_chunks.extend(_extract_rsc_data(raw_html))

    cleaned = _clean_html_pass1(raw_html)
    if not cleaned:
        raise PruningError("HTML empty after Pass 1 cleaning")

    try:
        parser = lxml.html.HTMLParser(recover=True, encoding="utf-8")
        doc = lxml.html.document_fromstring(cleaned.encode("utf-8"), parser=parser)
    except Exception as e:
        raise PruningError(f"lxml parsing failed: {e}") from e

    return meta_chunks, doc
