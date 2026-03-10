# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared product card extraction — 3-source cascade.

Source priority:
1. JSON-LD (ItemList / Product array) — highest confidence
2. DOM price-anchor + parent walk — medium confidence
3. Regex fallback — lowest confidence

Reuses patterns from pruned_context_builder and page_map_builder.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .. import Interactable

from ..i18n import FILTER_TERMS, LOAD_MORE_PAGINATION_TERMS, NEXT_PAGE_TERMS, PREV_PAGE_TERMS
from ..preprocessing.normalize import infer_currency, normalize_numeric
from ..sanitizer import sanitize_text
from . import ProductCard

logger = logging.getLogger(__name__)

# ── Module-level pre-compiled patterns ─────────────────────────────

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_CARD_PRICE_RE = re.compile(
    r"(?:₩\s*[\d,]+|\d[\d,]+\s*원|\d[\d,]+\s*円|¥\s*[\d,]+"
    r"|\d{2,3}(?:,\d{3})+(?:\s*원)?"
    r"|\$\d+(?:\.\d{2})?|€\s*[\d,.]+|£\s*[\d,.]+"
    r"|₹\s*[\d,.]+|₺\s*[\d,.]+|₫\s*[\d,.]+"
    r"|₱\s*[\d,.]+|฿\s*[\d,.]+"
    r"|R\$\s*[\d,.]+|S\$\s*[\d,.]+|NT\$\s*[\d,.]+"
    r"|RM\s*[\d,.]+|MX\$\s*[\d,.]+"
    r"|USD\s*[\d,.]+|EUR\s*[\d,.]+|CHF\s*[\d,.]+|INR\s*[\d,.]+|BRL\s*[\d,.]+)",
)

_NAME_CLEANUP_RE = re.compile(r"\s+")
_MAX_NAME_LEN = 200


def extract_cards_from_jsonld(
    raw_html: str,
    page_url: str,
) -> list[ProductCard]:
    """Extract product cards from JSON-LD ItemList or Product arrays."""
    cards: list[ProductCard] = []
    currency = infer_currency(page_url)

    for m in _JSONLD_RE.finditer(raw_html):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue

        items = _extract_jsonld_items(data)
        for i, item in enumerate(items):
            name = item.get("name", "")
            if not name:
                continue
            price = _extract_jsonld_price(item)
            item_currency = _extract_jsonld_currency(item) or currency
            original_price = _extract_jsonld_original_price(item)
            image_url = _extract_jsonld_image(item)
            url = item.get("url")
            brand = _extract_jsonld_brand(item)

            cards.append(
                ProductCard(
                    name=sanitize_text(str(name), max_len=_MAX_NAME_LEN),
                    price=normalize_numeric(price) if price else None,
                    currency=item_currency,
                    price_text=str(price) if price else None,
                    original_price=normalize_numeric(original_price) if original_price else None,
                    brand=sanitize_text(str(brand)) if brand else None,
                    image_url=str(image_url) if image_url else None,
                    url=str(url) if url else None,
                    position=i + 1,
                )
            )

    return cards


def extract_cards_from_regex(
    raw_html: str,
    page_url: str,
) -> list[ProductCard]:
    """Extract product cards via regex price matching (lowest confidence fallback)."""
    cards: list[ProductCard] = []
    currency = infer_currency(page_url)

    seen_prices: set[str] = set()
    for match in _CARD_PRICE_RE.finditer(raw_html):
        price_text = match.group(0).strip()
        if price_text in seen_prices:
            continue
        seen_prices.add(price_text)

        price_val = normalize_numeric(price_text)
        if price_val is None or price_val <= 0:
            continue

        cards.append(
            ProductCard(
                price=price_val,
                currency=currency,
                price_text=price_text,
                position=len(cards) + 1,
            )
        )

        if len(cards) >= 50:
            break

    return cards


def extract_cards(
    raw_html: str,
    html_lower: str,
    metadata: dict[str, Any],
    page_url: str,
) -> tuple[ProductCard, ...]:
    """3-source cascade card extraction.

    Never raises — returns empty tuple on failure.
    """
    try:
        # Source 1: JSON-LD (highest confidence)
        cards = extract_cards_from_jsonld(raw_html, page_url)
        if cards:
            return tuple(cards)

        # Source 2: Metadata items (from pruned_context_builder)
        items = metadata.get("items")
        if isinstance(items, list) and items:
            result: list[ProductCard] = []
            currency = infer_currency(page_url)
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                price = item.get("price")
                result.append(
                    ProductCard(
                        name=sanitize_text(str(name), max_len=_MAX_NAME_LEN) if name else "",
                        price=normalize_numeric(price) if price else None,
                        currency=item.get("currency") or currency,
                        price_text=item.get("price_text"),
                        brand=sanitize_text(str(item.get("brand", ""))) if item.get("brand") else None,
                        url=item.get("url"),
                        position=i + 1,
                    )
                )
            if result:
                return tuple(result)

        # Source 3: Regex fallback (lowest confidence)
        cards = extract_cards_from_regex(raw_html, page_url)
        if cards:
            return tuple(cards)

        return ()

    except Exception:
        return ()


# ── JSON-LD helpers ────────────────────────────────────────────────


def _extract_jsonld_items(data: Any) -> list[dict[str, Any]]:
    """Extract items from JSON-LD (handles @graph, arrays, ItemList)."""
    if isinstance(data, list):
        result: list[dict[str, Any]] = []
        for item in data:
            result.extend(_extract_jsonld_items(item))
        return result

    if not isinstance(data, dict):
        return []

    if "@graph" in data:
        return _extract_jsonld_items(data["@graph"])

    t = data.get("@type", "")
    types = t if isinstance(t, list) else [t]

    if "ItemList" in types:
        elements = data.get("itemListElement", [])
        if isinstance(elements, list):
            return [e.get("item", e) if isinstance(e, dict) else e for e in elements if isinstance(e, dict)]

    if any(x in ("Product", "IndividualProduct") for x in types):
        return [data]

    return []


def _extract_jsonld_price(item: dict[str, Any]) -> Any:
    """Extract price from JSON-LD Product."""
    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        price = offers.get("price") or offers.get("lowPrice")
        if price is not None:
            return price
    return item.get("price")


def _extract_jsonld_currency(item: dict[str, Any]) -> str | None:
    """Extract currency from JSON-LD Product."""
    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        return offers.get("priceCurrency")
    return None


def _extract_jsonld_original_price(item: dict[str, Any]) -> Any:
    """Extract original/list price from JSON-LD Product."""
    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        return offers.get("highPrice") or offers.get("priceSpecification", {}).get("price")
    return None


def _extract_jsonld_image(item: dict[str, Any]) -> str | None:
    """Extract first image URL from JSON-LD Product."""
    img = item.get("image")
    if isinstance(img, str):
        return img
    if isinstance(img, list) and img:
        first = img[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url")
    if isinstance(img, dict):
        return img.get("url")
    return None


def _extract_jsonld_brand(item: dict[str, Any]) -> str | None:
    """Extract brand from JSON-LD Product."""
    brand = item.get("brand")
    if isinstance(brand, str):
        return brand
    if isinstance(brand, dict):
        return brand.get("name")
    return None


# ── Shared pagination utilities ───────────────────────────────────

_NEXT_PAGE_LOWER = tuple(t.lower() for t in NEXT_PAGE_TERMS)
_PREV_PAGE_LOWER = tuple(t.lower() for t in PREV_PAGE_TERMS)
_LOAD_MORE_LOWER = tuple(t.lower() for t in LOAD_MORE_PAGINATION_TERMS)

_PAGE_NUM_RE = re.compile(r"(?:page\s*)?(\d+)\s*(?:of|/)\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PaginationRefs:
    """Pagination navigation refs extracted from interactables."""

    next_ref: int | None = None
    prev_ref: int | None = None
    load_more_ref: int | None = None
    current_page: int | None = None
    total_pages: int | None = None


def find_pagination_refs(interactables: list[Interactable]) -> PaginationRefs:
    """Find pagination-related refs from interactables. Never raises."""
    try:
        next_ref: int | None = None
        prev_ref: int | None = None
        load_more_ref: int | None = None
        current_page: int | None = None
        total_pages: int | None = None

        for item in interactables:
            if item.affordance not in ("click", "toggle"):
                continue
            name_lower = item.name.lower()

            if next_ref is None:
                for term in _NEXT_PAGE_LOWER:
                    if term in name_lower:
                        next_ref = item.ref
                        break

            if prev_ref is None:
                for term in _PREV_PAGE_LOWER:
                    if term in name_lower:
                        prev_ref = item.ref
                        break

            if load_more_ref is None:
                for term in _LOAD_MORE_LOWER:
                    if term in name_lower:
                        load_more_ref = item.ref
                        break

            # Try to extract page X/Y from button text
            if current_page is None:
                page_m = _PAGE_NUM_RE.search(name_lower)
                if page_m:
                    with suppress(ValueError):
                        current_page = int(page_m.group(1))
                        total_pages = int(page_m.group(2))

        return PaginationRefs(
            next_ref=next_ref,
            prev_ref=prev_ref,
            load_more_ref=load_more_ref,
            current_page=current_page,
            total_pages=total_pages,
        )
    except Exception:
        return PaginationRefs()


# ── Shared filter utilities ────────────────────────────────────────

_FILTER_TERMS_LOWER = tuple(t.lower() for t in FILTER_TERMS)


def find_filter_refs(interactables: list[Interactable]) -> tuple[int, ...]:
    """Find filter-related interactable refs."""
    refs: list[int] = []
    for item in interactables:
        name_lower = item.name.lower()
        if any(term in name_lower for term in _FILTER_TERMS_LOWER):
            refs.append(item.ref)
        if len(refs) >= 10:
            break
    return tuple(refs)
