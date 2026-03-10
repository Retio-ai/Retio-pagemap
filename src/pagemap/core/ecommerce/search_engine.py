# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Results Engine — Layer 1 analyzer for search_results pages.

Extracts search query, product cards, sort controls, and filter refs.
Never raises — returns empty SearchResult on failure.
"""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .. import Interactable

from ..i18n import SORT_TERMS, SPONSORED_TERMS
from ..sanitizer import sanitize_text
from . import ProductCard, SearchResult
from ._card_extractor import extract_cards, find_filter_refs, find_pagination_refs

logger = logging.getLogger(__name__)

# Pre-compute lowered terms for matching
_SORT_TERMS_LOWER = tuple(t.lower() for t in SORT_TERMS)
_SPONSORED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t.lower()) for t in SPONSORED_TERMS) + r")\b",
    re.IGNORECASE,
)

# Result count normalization: "1,234 results", "약 1,234개", "1 234 résultats"
_RESULT_COUNT_RE = re.compile(
    r"([\d,.\s]+)\s*(?:results?|건|개|件|résultats?|Ergebnisse|结果|resultados?)", re.IGNORECASE
)


def _extract_search_query(page_url: str) -> str | None:
    """Extract search query from URL parameters."""
    try:
        parsed = urlparse(page_url)
        params = parse_qs(parsed.query)
        # Common search query parameter names
        for key in ("q", "query", "search", "keyword", "s", "k", "searchTerm", "searchWord"):
            values = params.get(key)
            if values and values[0]:
                return sanitize_text(values[0], max_len=200)
        return None
    except Exception:
        return None


def _find_sort_control(
    interactables: list[Interactable],
) -> tuple[int | None, tuple[str, ...]]:
    """Find sort combobox/select and its options."""
    for item in interactables:
        if item.role not in ("combobox", "select"):
            continue
        name_lower = item.name.lower()
        if any(term in name_lower for term in _SORT_TERMS_LOWER):
            options = tuple(sanitize_text(o, max_len=100) for o in item.options[:10])
            return item.ref, options

    return None, ()


def _normalize_result_count(raw: str) -> int | None:
    """Normalize result count string to int. '1,234 results' → 1234."""
    try:
        m = _RESULT_COUNT_RE.search(raw)
        if m:
            digits = re.sub(r"[,.\s]", "", m.group(1))
            with suppress(ValueError):
                return int(digits)
        # Try bare number
        digits = re.sub(r"[,.\s]", "", raw.strip())
        with suppress(ValueError):
            val = int(digits)
            if val > 0:
                return val
        return None
    except Exception:
        return None


def _detect_sponsored(card_html: str) -> bool:
    """Detect if a card is sponsored/ad content."""
    return bool(_SPONSORED_RE.search(card_html))


def _apply_sponsored_to_cards(
    cards: tuple[ProductCard, ...],
    raw_html: str,
) -> tuple[ProductCard, ...]:
    """Mark cards as sponsored if ad markers detected near their name."""
    import dataclasses

    result: list[ProductCard] = []
    for card in cards:
        if card.name and _detect_sponsored(card.name):
            result.append(dataclasses.replace(card, is_sponsored=True))
        else:
            result.append(card)
    return tuple(result)


def analyze_search_results(
    *,
    raw_html: str,
    html_lower: str,
    interactables: list[Interactable],
    metadata: dict[str, Any],
    page_url: str,
    navigation_hints: dict[str, Any],
) -> SearchResult:
    """Analyze a search_results page. Never raises."""
    try:
        query = _extract_search_query(page_url)
        cards = extract_cards(raw_html, html_lower, metadata, page_url)
        sort_ref, sort_options = _find_sort_control(interactables)
        filter_refs = find_filter_refs(interactables)

        # Extract total results from metadata if available
        total_results = metadata.get("total_results") or metadata.get("totalResults")
        if total_results is not None:
            total_results = sanitize_text(str(total_results), max_len=50)

        # Sponsored detection on cards
        cards = _apply_sponsored_to_cards(cards, raw_html)

        # Pagination refs
        pag = find_pagination_refs(interactables)

        return SearchResult(
            cards=cards,
            query=query,
            total_results=total_results,
            sort_ref=sort_ref,
            sort_options=sort_options,
            filter_refs=filter_refs,
            next_ref=pag.next_ref,
            prev_ref=pag.prev_ref,
            load_more_ref=pag.load_more_ref,
            current_page=pag.current_page,
            total_pages=pag.total_pages,
        )

    except Exception as e:
        logger.debug("Search engine error: %s", e)
        return SearchResult()
