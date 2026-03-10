# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Listing Engine — Layer 1 analyzer for listing (category) pages.

Extracts product cards, breadcrumbs, category name, and filter refs.
Never raises — returns empty ListingResult on failure.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .. import Interactable

from ..sanitizer import sanitize_text
from . import ListingResult
from ._card_extractor import _JSONLD_RE, extract_cards, find_filter_refs, find_pagination_refs

logger = logging.getLogger(__name__)


def _extract_breadcrumbs(raw_html: str) -> tuple[str, ...]:
    """Extract breadcrumbs from JSON-LD BreadcrumbList."""
    try:
        for m in _JSONLD_RE.finditer(raw_html):
            try:
                data = json.loads(m.group(1))
            except (json.JSONDecodeError, TypeError):
                continue

            items = _find_breadcrumb_items(data)
            if items:
                crumbs: list[str] = []
                for item in sorted(items, key=lambda x: x.get("position", 0)):
                    name = item.get("name") or ""
                    if isinstance(item.get("item"), dict):
                        name = name or item["item"].get("name", "")
                    if name:
                        crumbs.append(sanitize_text(str(name), max_len=100))
                if crumbs:
                    return tuple(crumbs)
    except Exception:  # nosec B110
        pass
    return ()


def _find_breadcrumb_items(data: Any) -> list[dict[str, Any]]:
    """Find BreadcrumbList itemListElement in JSON-LD."""
    if isinstance(data, list):
        for item in data:
            result = _find_breadcrumb_items(item)
            if result:
                return result
        return []

    if not isinstance(data, dict):
        return []

    if "@graph" in data:
        return _find_breadcrumb_items(data["@graph"])

    t = data.get("@type", "")
    types = t if isinstance(t, list) else [t]

    if "BreadcrumbList" in types:
        elements = data.get("itemListElement", [])
        if isinstance(elements, list):
            return [e for e in elements if isinstance(e, dict)]

    return []


def _extract_category(breadcrumbs: tuple[str, ...]) -> str | None:
    """Extract category name from breadcrumbs (last non-empty entry)."""
    if breadcrumbs:
        return breadcrumbs[-1]
    return None


def analyze_listing(
    *,
    raw_html: str,
    html_lower: str,
    interactables: list[Interactable],
    metadata: dict[str, Any],
    page_url: str,
    navigation_hints: dict[str, Any],
) -> ListingResult:
    """Analyze a listing (category) page. Never raises."""
    try:
        cards = extract_cards(raw_html, html_lower, metadata, page_url)
        breadcrumbs = _extract_breadcrumbs(raw_html)
        category = _extract_category(breadcrumbs)
        filter_refs = find_filter_refs(interactables)

        # Extract total products from metadata
        total_products = metadata.get("total_products") or metadata.get("totalProducts")
        if total_products is not None:
            total_products = sanitize_text(str(total_products), max_len=50)

        # Pagination refs
        pag = find_pagination_refs(interactables)

        return ListingResult(
            cards=cards,
            category=category,
            breadcrumbs=breadcrumbs,
            total_products=total_products,
            filter_refs=filter_refs,
            next_ref=pag.next_ref,
            prev_ref=pag.prev_ref,
            load_more_ref=pag.load_more_ref,
        )

    except Exception as e:
        logger.debug("Listing engine error: %s", e)
        return ListingResult()
