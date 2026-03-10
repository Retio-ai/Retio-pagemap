# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Infinite scroll result deduplication.

Tracks accumulated results across scroll actions and removes duplicates.
Dedup key priority: card.url > normalize_name + price > normalize_name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import ScrollMergeState

# ── Name normalization ────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_name(name: str) -> str:
    """Normalize product name for dedup comparison."""
    return _WHITESPACE_RE.sub(" ", name.strip().lower())


def _get(card: Any, attr: str, default: Any = None) -> Any:
    """Get attribute from object or dict."""
    if isinstance(card, dict):
        return card.get(attr, default)
    return getattr(card, attr, default)


def _card_key(card: Any) -> str:
    """Generate dedup key for a product card."""
    # Try URL first (most unique)
    url = _get(card, "url")
    if url:
        return f"url:{url}"

    # Fallback: name + price
    name = _get(card, "name", "") or ""
    price = _get(card, "price")
    normalized = _normalize_name(name)

    if normalized and price is not None:
        return f"np:{normalized}:{price}"
    if normalized:
        return f"n:{normalized}"

    return ""


@dataclass(frozen=True, slots=True)
class ScrollMergeResult:
    total_accumulated: int
    new_this_scroll: int
    duplicates_removed: int


def merge_scroll_results(
    *,
    state: ScrollMergeState,
    new_cards: list[Any],
    page_url: str,
    page_type: str,
) -> ScrollMergeResult | None:
    """Merge new scroll results, dedup against accumulated state. Never raises.

    Args:
        state: Mutable scroll merge state (session-level)
        new_cards: New product cards from current scroll
        page_url: Current page URL (reset state if URL changed)
        page_type: Current page type

    Returns:
        ScrollMergeResult or None if no cards to merge.
    """
    try:
        return _merge_impl(
            state=state,
            new_cards=new_cards,
            page_url=page_url,
            page_type=page_type,
        )
    except Exception:
        return None


def _merge_impl(
    *,
    state: ScrollMergeState,
    new_cards: list[Any],
    page_url: str,
    page_type: str,
) -> ScrollMergeResult | None:
    if not new_cards:
        return None

    # Reset if URL changed
    if state.url and state.url != page_url:
        state.reset()
    state.url = page_url

    # Process new cards
    new_count = 0
    dup_count = 0
    for card in new_cards:
        key = _card_key(card)
        if not key:
            new_count += 1
            state.total_seen += 1
            continue
        if key in state.accumulated_keys:
            dup_count += 1
        else:
            state.accumulated_keys.add(key)
            new_count += 1
            state.total_seen += 1

    state.scroll_count += 1
    state.last_new_count = new_count

    return ScrollMergeResult(
        total_accumulated=state.total_seen,
        new_this_scroll=new_count,
        duplicates_removed=dup_count,
    )
