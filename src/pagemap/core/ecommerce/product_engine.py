# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Product Engine — Layer 1 analyzer for product_detail pages.

Extracts product information: name, price, brand, rating, availability, options.
Metadata (JSON-LD) is preferred; DOM regex is fallback.
Never raises — returns empty ProductResult on failure.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .. import Interactable

from ..i18n import AVAILABILITY_TERMS, OPTION_TERMS, SHIPPING_TERMS
from ..preprocessing.normalize import infer_currency, normalize_numeric, normalize_price
from ..sanitizer import sanitize_text
from . import OptionGroup, ProductResult
from ._card_extractor import _JSONLD_RE

logger = logging.getLogger(__name__)

# ── Module-level pre-compiled patterns ─────────────────────────────

_RATING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*/?\s*5?\s*(?:stars?|★|점|つ星|étoile|Stern|颗星)?", re.IGNORECASE)
_REVIEW_COUNT_RE = re.compile(r"(\d[\d,]*)\s*(?:review|리뷰|件|avis|Bewertung|评价|개|건)", re.IGNORECASE)
_DISCOUNT_RE = re.compile(r"(\d{1,3})\s*%\s*(?:off|할인|割引|de réduction|Rabatt|折|descuento|sconto)?", re.IGNORECASE)
_IMAGE_RE = re.compile(
    r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)

# Pre-compute lowered terms
_OPTION_TERMS_LOWER = tuple(t.lower() for t in OPTION_TERMS)
_AVAIL_TERMS_LOWER = tuple(t.lower() for t in AVAILABILITY_TERMS)
_SHIPPING_TERMS_LOWER = tuple(t.lower() for t in SHIPPING_TERMS)

_SIZE_KEYWORDS = ("size", "사이즈", "サイズ", "taille", "größe", "尺码", "尺碼")
_COLOR_KEYWORDS = ("color", "colour", "색상", "カラー", "couleur", "farbe", "颜色", "顏色")

# Availability mapping for Schema.org
_SCHEMA_AVAILABILITY: dict[str, str] = {
    "instock": "in_stock",
    "outofstock": "out_of_stock",
    "limitedavailability": "limited",
    "preorder": "pre_order",
    "presale": "pre_order",
    "backorder": "limited",
    "soldout": "out_of_stock",
    "discontinued": "out_of_stock",
    "instoreonly": "in_stock",
    "onlineonly": "in_stock",
    "madetoorder": "limited",
}


def _extract_product_from_jsonld(raw_html: str) -> dict[str, Any] | None:
    """Extract product data from JSON-LD Product schema."""
    for m in _JSONLD_RE.finditer(raw_html):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        product = _find_product(data)
        if product:
            return product
    return None


def _find_product(data: Any) -> dict[str, Any] | None:
    """Find Product object in JSON-LD (handles @graph, arrays)."""
    if isinstance(data, list):
        for item in data:
            result = _find_product(item)
            if result:
                return result
        return None
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _find_product(data["@graph"])
    t = data.get("@type", "")
    types = t if isinstance(t, list) else [t]
    if any(x in ("Product", "IndividualProduct") for x in types):
        return data
    return None


def _extract_availability(jsonld: dict[str, Any] | None, html_lower: str) -> str | None:
    """Extract availability from JSON-LD offers or HTML."""
    # JSON-LD Schema.org availability
    if jsonld:
        offers = jsonld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            avail = offers.get("availability", "")
            if isinstance(avail, str):
                avail_key = avail.rsplit("/", 1)[-1].lower()
                mapped = _SCHEMA_AVAILABILITY.get(avail_key)
                if mapped:
                    return mapped

    # HTML fallback
    for term in _AVAIL_TERMS_LOWER:
        if term in html_lower:
            if any(kw in html_lower for kw in ("out of stock", "품절", "売り切れ", "rupture", "ausverkauft", "缺货")):
                return "out_of_stock"
            return "in_stock"

    return None


def _extract_shipping(html_lower: str) -> str | None:
    """Extract shipping info from HTML."""
    for term in _SHIPPING_TERMS_LOWER:
        if term in html_lower:
            return sanitize_text(term, max_len=100)
    return None


def _extract_options(interactables: list[Interactable]) -> tuple[OptionGroup, ...]:
    """Extract product options from combobox/select interactables."""
    options: list[OptionGroup] = []
    for item in interactables:
        if item.role not in ("combobox", "select"):
            continue
        name_lower = item.name.lower()

        # Check if this is an option-related control
        is_option = any(term in name_lower for term in _OPTION_TERMS_LOWER)
        if not is_option:
            continue

        # Determine option type
        option_type = "other"
        if any(kw in name_lower for kw in _SIZE_KEYWORDS):
            option_type = "size"
        elif any(kw in name_lower for kw in _COLOR_KEYWORDS):
            option_type = "color"

        values = tuple(sanitize_text(o, max_len=100) for o in item.options[:20])
        selected = sanitize_text(item.value) if item.value else None

        options.append(
            OptionGroup(
                label=sanitize_text(item.name, max_len=100),
                type=option_type,
                values=values,
                ref=item.ref,
                selected=selected,
            )
        )

    return tuple(options)


def _count_images(raw_html: str) -> int:
    """Count product images (rough estimate)."""
    return len(_IMAGE_RE.findall(raw_html))


# ── Gallery / Variant / Review extraction ─────────────────────────

_GALLERY_RE = re.compile(
    r'<(?:div|ul|section)[^>]*class=["\'][^"\']*(?:gallery|slider|carousel|swiper|product-images)[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)


def _extract_gallery_images(
    jsonld: dict[str, Any] | None,
    raw_html: str,
) -> tuple[str, ...]:
    """Extract gallery image URLs from JSON-LD and DOM. Max 10, deduped."""
    try:
        urls: list[str] = []
        seen: set[str] = set()

        # JSON-LD images
        if jsonld:
            img = jsonld.get("image")
            if isinstance(img, str) and img:
                urls.append(img)
                seen.add(img)
            elif isinstance(img, list):
                for item in img:
                    u = item if isinstance(item, str) else (item.get("url") if isinstance(item, dict) else None)
                    if u and u not in seen:
                        urls.append(u)
                        seen.add(u)

        # DOM gallery images (supplement)
        if len(urls) < 10 and _GALLERY_RE.search(raw_html):
            for m in _IMAGE_RE.finditer(raw_html):
                src = m.group(1)
                if src and src not in seen and not src.startswith("data:"):
                    urls.append(src)
                    seen.add(src)
                    if len(urls) >= 10:
                        break

        return tuple(urls[:10])
    except Exception:
        return ()


def _build_selected_variant(options: tuple[OptionGroup, ...]) -> dict[str, str] | None:
    """Build a dict of selected option values. None if nothing selected."""
    try:
        variant: dict[str, str] = {}
        for opt in options:
            if opt.selected:
                key = opt.label or opt.type
                variant[key] = opt.selected
        return variant if variant else None
    except Exception:
        return None


def _extract_review_snippets(jsonld: dict[str, Any] | None) -> tuple[str, ...]:
    """Extract top 3 review snippets from JSON-LD. Max 200 chars each."""
    try:
        if not jsonld:
            return ()
        reviews = jsonld.get("review", [])
        if not isinstance(reviews, list):
            reviews = [reviews]
        snippets: list[str] = []
        for rev in reviews[:3]:
            if isinstance(rev, dict):
                body = rev.get("reviewBody", "")
                if isinstance(body, str) and body.strip():
                    snippets.append(sanitize_text(body.strip(), max_len=200))
        return tuple(snippets)
    except Exception:
        return ()


def analyze_product(
    *,
    raw_html: str,
    html_lower: str,
    interactables: list[Interactable],
    metadata: dict[str, Any],
    page_url: str,
) -> ProductResult:
    """Analyze a product_detail page. Never raises."""
    try:
        currency = infer_currency(page_url)
        jsonld = _extract_product_from_jsonld(raw_html)

        # Name: JSON-LD > metadata > None
        name = None
        if jsonld:
            name = jsonld.get("name")
        if not name:
            name = metadata.get("title") or metadata.get("name")
        if name:
            name = sanitize_text(str(name), max_len=200)

        # Price: JSON-LD > metadata
        price = None
        price_currency = currency
        if jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                price = normalize_numeric(offers.get("price") or offers.get("lowPrice"))
                price_currency = offers.get("priceCurrency") or currency

        if price is None:
            meta_price = metadata.get("price")
            if meta_price is not None:
                parsed = normalize_price(str(meta_price), url_hint=page_url)
                if parsed.prices:
                    price = parsed.prices[0].amount
                    if not price_currency or price_currency == currency:
                        price_currency = parsed.prices[0].currency
                else:
                    price = normalize_numeric(meta_price)

        # Original price
        original_price = None
        if jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                original_price = normalize_numeric(offers.get("highPrice"))
        if original_price is None:
            original_price = normalize_numeric(metadata.get("original_price"))

        # Discount percentage
        discount_pct = None
        if price and original_price and original_price > price:
            discount_pct = round((1 - price / original_price) * 100)
        else:
            dm = _DISCOUNT_RE.search(raw_html)
            if dm:
                with contextlib.suppress(ValueError, TypeError):
                    discount_pct = int(dm.group(1))

        # Brand
        brand = None
        if jsonld:
            b = jsonld.get("brand")
            if isinstance(b, dict):
                brand = b.get("name")
            elif isinstance(b, str):
                brand = b
        if not brand:
            brand = metadata.get("brand")
        if brand:
            brand = sanitize_text(str(brand), max_len=100)

        # Rating
        rating = None
        review_count = None
        if jsonld:
            agg = jsonld.get("aggregateRating", {})
            if isinstance(agg, dict):
                rv = agg.get("ratingValue")
                if rv is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        rating = float(rv)
                rc = agg.get("reviewCount") or agg.get("ratingCount")
                if rc is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        review_count = int(str(rc).replace(",", ""))

        if rating is None:
            rm = _RATING_RE.search(raw_html[:20000])
            if rm:
                with contextlib.suppress(ValueError, TypeError):
                    val = float(rm.group(1))
                    if val <= 5:
                        rating = val

        if review_count is None:
            rcm = _REVIEW_COUNT_RE.search(raw_html[:20000])
            if rcm:
                with contextlib.suppress(ValueError, TypeError):
                    review_count = int(rcm.group(1).replace(",", ""))

        # Availability
        availability = _extract_availability(jsonld, html_lower)

        # Shipping
        shipping = _extract_shipping(html_lower)

        # Options
        options = _extract_options(interactables)

        # Image count
        image_count = _count_images(raw_html)

        # Gallery images
        gallery_images = _extract_gallery_images(jsonld, raw_html)

        # Selected variant
        selected_variant = _build_selected_variant(options)

        # Review snippets
        review_snippets = _extract_review_snippets(jsonld)

        return ProductResult(
            name=name,
            price=price,
            currency=price_currency,
            original_price=original_price,
            discount_pct=discount_pct,
            brand=brand,
            rating=rating,
            review_count=review_count,
            availability=availability,
            shipping=shipping,
            options=options,
            image_count=image_count,
            gallery_images=gallery_images,
            selected_variant=selected_variant,
            review_snippets=review_snippets,
        )

    except Exception as e:
        logger.debug("Product engine error: %s", e)
        return ProductResult()
