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

from . import ChunkType, HtmlChunk, PruningError

logger = logging.getLogger(__name__)

# Tags to remove entirely (after extracting specials)
_REMOVE_TAGS = ("script", "style", "noscript", "svg", "link", "path", "defs", "iframe")

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

# Sibling grouping constants (Phase B)
_SIBLING_GROUP_MAX_CHARS = 700  # ~200 tok — HtmlRAG 128-256 words sweet spot
_SIBLING_SINGLE_MAX_CHARS = 500  # ~143 tok — max single chunk for grouping eligibility
_SIBLING_MIN_MERGE_CHARS = 20  # minimum text length for merge eligibility (prices/ratings stay standalone)


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
    """Check if element is inside a <main> tag or role='main' container."""
    parent = el.getparent()
    while parent is not None:
        tag = parent.tag.lower() if isinstance(parent.tag, str) else ""
        if tag == "main":
            return True
        if isinstance(parent.tag, str) and parent.get("role", "").lower() == "main":
            return True
        parent = parent.getparent()
    return False


_CJK_RE = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")


def _has_cjk(text: str) -> bool:
    """Check if text contains any CJK characters."""
    return bool(_CJK_RE.search(text))


def _group_small_siblings(chunks: list[HtmlChunk], *, alpha: float = 1.0) -> list[HtmlChunk]:
    """Merge consecutive small TEXT_BLOCK chunks that share the same parent_xpath.

    Grouping rules:
      - Only TEXT_BLOCK chunks with len(text) < _SIBLING_SINGLE_MAX_CHARS are eligible.
      - Consecutive eligible chunks sharing the same parent_xpath are merged
        if their combined text < _SIBLING_GROUP_MAX_CHARS.
      - HEADING and atomic chunks (TABLE, LIST, FORM, MEDIA) stay standalone.
      - Cross-script merging prevented (CJK vs non-CJK).
      - Merged chunk inherits: first chunk's xpath, concatenated html/text,
        in_main = any(chunk.in_main), first chunk's depth/tag/attrs.

    Args:
        alpha: A2 grouping alpha (0.4-1.0). Lower = smaller groups = finer
            budget_selection granularity under pressure.
    """
    if not chunks:
        return chunks

    max_chars = int(_SIBLING_GROUP_MAX_CHARS * alpha)
    single_max = int(_SIBLING_SINGLE_MAX_CHARS * alpha)

    grouped: list[HtmlChunk] = []
    buf: list[HtmlChunk] = []
    buf_chars = 0
    buf_parent = ""

    def _flush() -> None:
        nonlocal buf, buf_chars, buf_parent
        if not buf:
            return
        if len(buf) == 1:
            grouped.append(buf[0])
        else:
            merged_html = "\n".join(c.html for c in buf)
            merged_text = " ".join(c.text for c in buf)
            grouped.append(
                HtmlChunk(
                    xpath=buf[0].xpath,
                    html=merged_html,
                    text=merged_text,
                    tag=buf[0].tag,
                    chunk_type=buf[0].chunk_type,
                    attrs=buf[0].attrs,
                    parent_xpath=buf[0].parent_xpath,
                    depth=buf[0].depth,
                    in_main=any(c.in_main for c in buf),
                )
            )
        buf = []
        buf_chars = 0
        buf_parent = ""

    for chunk in chunks:
        is_small = len(chunk.text) < single_max
        is_heading = chunk.chunk_type == ChunkType.HEADING
        is_text_block = chunk.chunk_type == ChunkType.TEXT_BLOCK

        # Only TEXT_BLOCK chunks are eligible for merging.
        # Headings break the group (section boundary) and stay standalone.
        # Atomic types (TABLE, LIST, FORM, MEDIA, META) always standalone.
        if is_heading:
            _flush()
            grouped.append(chunk)
            continue

        if not is_text_block or not is_small or len(chunk.text) < _SIBLING_MIN_MERGE_CHARS:
            _flush()
            grouped.append(chunk)
            continue

        # Check parent continuity
        if buf and chunk.parent_xpath != buf_parent:
            _flush()

        # Check script consistency (prevent CJK + non-CJK merging)
        if buf and _has_cjk(buf[0].text) != _has_cjk(chunk.text):
            _flush()

        # Check budget
        if buf_chars + len(chunk.text) >= max_chars:
            _flush()

        buf.append(chunk)
        buf_chars += len(chunk.text)
        buf_parent = chunk.parent_xpath

    _flush()
    return grouped


def _decompose_element(
    el: lxml.html.HtmlElement,
    tree: etree._ElementTree,
    *,
    depth: int = 0,
    max_depth: int = _MAX_DECOMPOSE_DEPTH,
    enable_sibling_grouping: bool = True,
    grouping_alpha: float = 1.0,
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
                    chunks.extend(
                        _decompose_element(
                            child,
                            tree,
                            depth=depth + 1,
                            max_depth=max_depth,
                            enable_sibling_grouping=enable_sibling_grouping,
                            grouping_alpha=grouping_alpha,
                        )
                    )
            return _group_small_siblings(chunks, alpha=grouping_alpha) if enable_sibling_grouping else chunks
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
                    chunks.extend(
                        _decompose_element(
                            child,
                            tree,
                            depth=depth + 1,
                            max_depth=max_depth,
                            enable_sibling_grouping=enable_sibling_grouping,
                            grouping_alpha=grouping_alpha,
                        )
                    )
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
    json_ld = _extract_json_ld(raw_html)
    og = _extract_og_meta(raw_html)
    rsc = _extract_rsc_data(raw_html)
    meta_chunks.extend(json_ld)
    meta_chunks.extend(og)
    meta_chunks.extend(rsc)

    cleaned = _clean_html_pass1(raw_html)
    if not cleaned:
        raise PruningError("HTML empty after Pass 1 cleaning")

    try:
        from pagemap.telemetry import emit
        from pagemap.telemetry.events import PREPROCESS_COMPLETE

        emit(PREPROCESS_COMPLETE, {"json_ld_count": len(json_ld), "og_count": len(og), "rsc_count": len(rsc)})
    except Exception:  # nosec B110
        pass

    try:
        parser = lxml.html.HTMLParser(recover=True, encoding="utf-8")
        doc = lxml.html.document_fromstring(cleaned.encode("utf-8"), parser=parser)
    except Exception as e:
        raise PruningError(f"lxml parsing failed: {e}") from e

    return meta_chunks, doc
