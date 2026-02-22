# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk re-merge and HTMLRAG Pass 2 compression.

Combines selected chunks back into a single HTML string and applies
lossless compression (attribute stripping, whitespace normalization).
"""

from __future__ import annotations

import logging
import re

from pagemap.pruning import HtmlChunk

logger = logging.getLogger(__name__)

_EMPTY_TAG_REMOVAL_PASSES = 5

# Pre-compiled patterns for compress_html() (Phase 6.3a)
_EMPTY_TAG_RE = re.compile(
    r"<(div|span|p|section|article|aside|figure|figcaption|details|summary|"
    r"b|i|em|strong|small|sup|sub|a|abbr|cite|code|mark|u|s)\b[^>]*>\s*</\1>",
    re.IGNORECASE,
)
_WRAPPER_DIV_RE = re.compile(
    r"<div\b[^>]*>\s*(<(?:p|h[1-6]|ul|ol|table|article|section|figure)\b[^>]*>.*?</(?:p|h[1-6]|ul|ol|table|article|section|figure)>)\s*</div>",
    re.DOTALL | re.IGNORECASE,
)
_SPAN_WRAPPER_RE = re.compile(r"<span\s*>(.*?)</span>", re.DOTALL)
_HORIZ_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")
_TAG_GAP_RE = re.compile(r">\s+<")

# ---------------------------------------------------------------------------
# XPath document-order sort key
# ---------------------------------------------------------------------------

_XPATH_INDEX_RE = re.compile(r"([^[]+?)(?:\[(\d+)\])?$")


def _xpath_sort_key(xpath: str) -> tuple[tuple[str, int], ...]:
    """Convert XPath string to a numerically-sortable tuple key.

    Lexicographic string sorting breaks for sibling indices >= 10:
    '/body/div[10]' < '/body/div[2]' lexically (wrong).
    This parses bracket indices as integers for correct ordering.

    Example::

        '/html/body/div[10]/p[2]' → (('html',0), ('body',0), ('div',10), ('p',2))
    """
    parts: list[tuple[str, int]] = []
    for step in xpath.split("/"):
        if not step:
            continue
        m = _XPATH_INDEX_RE.match(step)
        if m:
            parts.append((m.group(1), int(m.group(2)) if m.group(2) else 0))
        else:
            parts.append((step, 0))
    return tuple(parts)


# Attributes to preserve during compression
_KEEP_ATTRS = {
    "itemprop",
    "itemtype",
    "itemscope",
    "role",
    "aria-label",
    "aria-labelledby",
    "href",
    "src",
    "alt",
    "title",
    "datetime",
    "content",
    "property",
    "type",
    "name",
    "value",
}

# Attribute patterns to remove (expanded set)
_REMOVE_ATTR_RE = re.compile(
    r"\s+(?:class|id|data-[\w-]+|style|onclick|onload|onsubmit|onchange|"
    r"tabindex|accesskey|draggable|lang|dir|translate|hidden|slot|part|"
    r"xmlns[\w:]*|xml:[\w]+|about|datatype|inlist|prefix|rev|typeof|vocab|"
    r"autocomplete|autofocus|placeholder|spellcheck|contenteditable|"
    r"aria-describedby|aria-expanded|aria-haspopup|aria-controls|aria-selected|"
    r"aria-pressed|aria-checked|aria-disabled|aria-live|aria-atomic|aria-relevant|"
    r"aria-owns|aria-flowto|aria-busy|aria-dropeffect|aria-grabbed|"
    r"aria-colcount|aria-colindex|aria-colspan|aria-rowcount|aria-rowindex|aria-rowspan|"
    r"aria-activedescendant|aria-errormessage|aria-keyshortcuts|aria-modal|"
    r"aria-multiline|aria-multiselectable|aria-orientation|aria-placeholder|"
    r"aria-posinset|aria-readonly|aria-required|aria-roledescription|aria-setsize|"
    r"aria-sort|aria-valuemax|aria-valuemin|aria-valuenow|aria-valuetext|"
    r"width|height|border|cellpadding|cellspacing|bgcolor|align|valign)"
    r'\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
    re.IGNORECASE,
)


def remerge_chunks(chunks: list[HtmlChunk]) -> str:
    """Re-merge selected chunks into a single HTML string.

    Chunks are sorted by xpath for document order, then concatenated
    with a flat structure (no tree reconstruction in Phase 0).
    """
    if not chunks:
        return ""

    # Sort by xpath to approximate document order
    sorted_chunks = sorted(chunks, key=lambda c: _xpath_sort_key(c.xpath))

    parts = [c.html for c in sorted_chunks]
    inner = "\n".join(parts)
    return f"<html><body>\n{inner}\n</body></html>"


def compress_html(html: str) -> str:
    """HTMLRAG Pass 2: lossless compression.

    1. Remove non-semantic attributes (class, id, data-*, style, event handlers, etc.)
    2. Remove empty elements
    3. Collapse single-child wrapper divs
    4. Strip unnecessary closing tags for void elements
    5. Normalize whitespace
    """
    if not html:
        return html

    # NOTE: htmlrag's clean_html() strips <script> and <meta> tags, which
    # destroys JSON-LD and OG metadata that we explicitly preserved.
    # Always use our own compression that keeps these elements.
    result = html

    # Remove non-semantic attributes (expanded set)
    result = _REMOVE_ATTR_RE.sub("", result)

    # Remove empty elements (no text, no children with text)
    # Iteratively remove empty tags (innermost first)
    for _ in range(_EMPTY_TAG_REMOVAL_PASSES):  # more passes for deeply nested empties
        prev = result
        result = _EMPTY_TAG_RE.sub("", result)
        if result == prev:
            break

    # Collapse single-child wrapper divs: <div><p>text</p></div> → <p>text</p>
    result = _WRAPPER_DIV_RE.sub(r"\1", result)

    # Remove redundant span wrappers: <span>text</span> → text (when no attributes)
    result = _SPAN_WRAPPER_RE.sub(r"\1", result)

    # Normalize whitespace
    result = _HORIZ_SPACE_RE.sub(" ", result)
    result = _BLANK_LINES_RE.sub("\n", result)
    result = _TAG_GAP_RE.sub(">\n<", result)

    return result.strip()
