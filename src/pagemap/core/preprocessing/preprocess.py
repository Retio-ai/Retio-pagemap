# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""HTML preprocessing for LLM extraction.

Two levels:
  Level 1 (stripped): Remove script/style/svg/noscript, comments, collapse whitespace.
  Level 2 (semantic): Level 1 + strip class/id/data-* attrs, collapse empty wrappers.
"""

from __future__ import annotations

import re

import tiktoken
from bs4 import BeautifulSoup, Comment

_STRIP_TAGS = {"script", "style", "svg", "noscript", "link", "meta", "path", "defs"}

_ZERO_SIZE_RE = re.compile(r"(?:width|height)\s*:\s*0(?:px)?(?:[;\s]|$)")

_enc: tiktoken.Encoding = tiktoken.get_encoding("cl100k_base")

_CJK_APPROX_RE = re.compile(r"[\u3000-\u9fff\uac00-\ud7af]")


def _get_encoder() -> tiktoken.Encoding:
    return _enc


def count_tokens(text: str) -> int:
    """Count tokens using cl100k_base (GPT-4 / Claude tokenizer approximation)."""
    return len(_enc.encode(text))


def count_tokens_approx(text: str) -> int:
    """CJK-aware approximate token count.

    Rough heuristic: ~4 chars/tok English, ~2 chars/tok CJK assumed.
    Note: actual Korean BPE is ~1.1 chars/tok, so this UNDER-estimates
    Korean token counts by ~50%. Safe for metrics; do NOT use for budgets.
    """
    sample = text[:2000]
    if not sample:
        return 0
    cjk_count = len(_CJK_APPROX_RE.findall(sample))
    ratio = cjk_count / len(sample)
    chars_per_token = 4.0 - 2.0 * ratio
    return int(len(text) / chars_per_token)


def _remove_hidden_elements(soup: BeautifulSoup) -> None:
    """Remove elements hidden via CSS or ARIA (potential prompt injection vectors)."""
    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none")):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"visibility\s*:\s*hidden")):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"opacity\s*:\s*0(?:[;\s]|$)")):
        tag.decompose()
    for tag in soup.find_all(style=_ZERO_SIZE_RE):
        tag.decompose()


def strip_html(html: str, max_tokens: int = 25_000) -> str:
    """Level 1: Remove non-content tags, comments, collapse whitespace."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags
    for tag in soup.find_all(list(_STRIP_TAGS)):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Remove hidden elements
    _remove_hidden_elements(soup)

    text = str(soup)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r">\s+<", "> <", text)

    return _truncate(text, max_tokens)


def semantic_html(html: str, max_tokens: int = 25_000) -> str:
    """Level 2: Stripped + remove class/id/data-* attrs, collapse empty wrappers."""
    soup = BeautifulSoup(html, "html.parser")

    # Level 1 operations
    for tag in soup.find_all(list(_STRIP_TAGS)):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    _remove_hidden_elements(soup)

    # Level 2: Strip non-semantic attributes
    _preserve_attrs = {
        "href",
        "src",
        "alt",
        "title",
        "aria-label",
        "role",
        "type",
        "name",
        "value",
        "datetime",
        "content",
        "property",
    }
    for tag in soup.find_all(True):
        attrs_to_remove = [a for a in list(tag.attrs) if a not in _preserve_attrs]
        for attr in attrs_to_remove:
            del tag[attr]

    # Collapse empty wrapper divs (single child, no text)
    for div in soup.find_all("div"):
        if not div.string and len(div.contents) == 1:
            child = div.contents[0]
            if hasattr(child, "name") and child.name:
                div.replace_with(child)

    text = str(soup)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r">\s+<", "> <", text)

    return _truncate(text, max_tokens)


def _truncate(text: str, max_tokens: int) -> str:
    """Truncate text to fit within max_tokens. Keep first 80% + last 20%."""
    tokens = _get_encoder().encode(text)
    if len(tokens) <= max_tokens:
        return text

    head_count = int(max_tokens * 0.8)
    tail_count = max_tokens - head_count
    head = _get_encoder().decode(tokens[:head_count])
    tail = _get_encoder().decode(tokens[-tail_count:])
    return head + "\n<!-- ... truncated ... -->\n" + tail
