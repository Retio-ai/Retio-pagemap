"""Structured metadata extraction from pre-parsed HtmlChunks.

Cascade priority: JSON-LD > itemprop > OG meta > h1 fallback.
Zero regex, zero lxml, zero I/O -- json.loads + dict lookups only.
"""

from __future__ import annotations

import json
from typing import Any

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.sanitizer import sanitize_text

# --- Helpers ---


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        s = str(v).replace(",", "").strip()
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


# --- JSON-LD parsing ---


def _find_product_in_jsonld(data: Any) -> dict | None:
    """Find a Product-type object in JSON-LD data."""
    if isinstance(data, list):
        for item in data:
            found = _find_product_in_jsonld(item)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _find_product_in_jsonld(data["@graph"])
    schema_type = data.get("@type", "")
    if isinstance(schema_type, list):
        if any(t in ("Product", "IndividualProduct") for t in schema_type):
            return data
    elif schema_type in ("Product", "IndividualProduct"):
        return data
    return None


def _extract_price_from_offers(offers: Any) -> dict[str, Any]:
    """Handle offers polymorphism: Offer, [Offer], AggregateOffer."""
    result: dict[str, Any] = {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return result

    offer_type = offers.get("@type", "")

    if offer_type == "AggregateOffer":
        result["price"] = _to_float(offers.get("lowPrice") or offers.get("price"))
        inner = offers.get("offers")
        if isinstance(inner, list) and inner:
            result["price"] = _to_float(inner[0].get("price")) or result.get("price")
    else:
        result["price"] = _to_float(offers.get("price"))

    result["currency"] = offers.get("priceCurrency")
    return {k: v for k, v in result.items() if v is not None}


def _parse_json_ld_product(meta_chunks: list[HtmlChunk]) -> dict[str, Any]:
    """Extract Product fields from application/ld+json META chunks."""
    for chunk in meta_chunks:
        if chunk.attrs.get("type") != "application/ld+json":
            continue
        try:
            data = json.loads(chunk.text)
        except (json.JSONDecodeError, TypeError):
            continue

        product = _find_product_in_jsonld(data)
        if not product:
            continue

        result: dict[str, Any] = {}

        if product.get("name"):
            result["name"] = sanitize_text(str(product["name"]).strip())

        offers = product.get("offers", {})
        result.update(_extract_price_from_offers(offers))

        brand = product.get("brand")
        if isinstance(brand, dict):
            result["brand"] = sanitize_text(brand.get("name", ""))
        elif isinstance(brand, str):
            result["brand"] = sanitize_text(brand)

        agg = product.get("aggregateRating", {})
        if isinstance(agg, dict):
            if agg.get("ratingValue"):
                result["rating"] = _to_float(agg["ratingValue"])
            if agg.get("reviewCount"):
                result["review_count"] = _to_int(agg["reviewCount"])

        img = product.get("image")
        if isinstance(img, list):
            result["image_url"] = img[0] if img else None
        elif isinstance(img, str):
            result["image_url"] = img

        return result
    return {}


# --- JSON-LD ItemList parsing ---


def _find_itemlist_in_jsonld(data: Any) -> dict | None:
    """Find an ItemList-type object in JSON-LD data."""
    if isinstance(data, list):
        for item in data:
            found = _find_itemlist_in_jsonld(item)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _find_itemlist_in_jsonld(data["@graph"])
    schema_type = data.get("@type", "")
    if isinstance(schema_type, list):
        if "ItemList" in schema_type:
            return data
    elif schema_type == "ItemList":
        return data
    return None


def _parse_json_ld_itemlist(meta_chunks: list[HtmlChunk]) -> list[dict[str, Any]]:
    """Extract product items from JSON-LD ItemList.

    Returns a list of dicts with keys: name, price, brand, url, position.
    """
    for chunk in meta_chunks:
        if chunk.attrs.get("type") != "application/ld+json":
            continue
        try:
            data = json.loads(chunk.text)
        except (json.JSONDecodeError, TypeError):
            continue

        itemlist = _find_itemlist_in_jsonld(data)
        if not itemlist:
            continue

        elements = itemlist.get("itemListElement", [])
        if not isinstance(elements, list):
            continue

        items: list[dict[str, Any]] = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            item: dict[str, Any] = {}

            # Position
            pos = el.get("position")
            if pos is not None:
                item["position"] = _to_int(pos)

            # The actual product can be nested under "item" or be the element itself
            product = el.get("item", el)
            if not isinstance(product, dict):
                continue

            if product.get("name"):
                item["name"] = sanitize_text(str(product["name"]).strip())
            elif product.get("headline"):
                item["name"] = sanitize_text(str(product["headline"]).strip())

            # Price from offers
            offers = product.get("offers", {})
            if offers:
                price_info = _extract_price_from_offers(offers)
                item.update(price_info)

            # Direct price field
            if "price" not in item and product.get("price"):
                item["price"] = _to_float(product["price"])

            # Brand
            brand = product.get("brand")
            if isinstance(brand, dict):
                item["brand"] = sanitize_text(brand.get("name", ""))
            elif isinstance(brand, str):
                item["brand"] = sanitize_text(brand)

            # URL
            url = product.get("url") or el.get("url")
            if url:
                item["url"] = str(url)

            if item.get("name"):
                items.append(item)

        if items:
            return items
    return []


# --- itemprop extraction ---

_ITEMPROP_FIELD_MAP: dict[str, dict[str, str]] = {
    "Product": {
        "name": "name",
        "price": "price",
        "priceCurrency": "currency",
        "brand": "brand",
        "ratingValue": "rating",
        "reviewCount": "review_count",
    },
    "NewsArticle": {
        "headline": "headline",
        "author": "author",
        "datePublished": "date_published",
    },
}


def _parse_itemprop(heading_chunks: list[HtmlChunk], schema_name: str) -> dict[str, Any]:
    """Extract fields from HtmlChunk.attrs['itemprop']."""
    field_map = _ITEMPROP_FIELD_MAP.get(schema_name, {})
    result: dict[str, Any] = {}
    for chunk in heading_chunks:
        prop = chunk.attrs.get("itemprop")
        if not prop or prop not in field_map:
            continue
        field_name = field_map[prop]
        value = chunk.attrs.get("content") or chunk.text.strip()
        if value and field_name not in result:
            if field_name in ("price", "original_price"):
                result[field_name] = _to_float(value)
            elif field_name == "review_count":
                result[field_name] = _to_int(value)
            elif field_name == "rating":
                result[field_name] = _to_float(value)
            else:
                result[field_name] = sanitize_text(value, max_len=200)
    return result


# --- OG meta extraction ---

_OG_FIELD_MAP: dict[str, dict[str, str]] = {
    "Product": {
        "og:title": "name",
        "og:image": "image_url",
        "og:price:amount": "price",
        "og:price:currency": "currency",
        "product:price:amount": "price",
        "product:price:currency": "currency",
    },
    "NewsArticle": {
        "og:title": "headline",
        "article:published_time": "date_published",
        "article:author": "author",
        "og:site_name": "publisher",
    },
}


def _parse_og_meta(meta_chunks: list[HtmlChunk], schema_name: str) -> dict[str, Any]:
    """Extract fields from OG meta attributes."""
    og_map = _OG_FIELD_MAP.get(schema_name, {})
    result: dict[str, Any] = {}
    for chunk in meta_chunks:
        if chunk.chunk_type != ChunkType.META:
            continue
        for og_key, field_name in og_map.items():
            if og_key in chunk.attrs and field_name not in result:
                value = chunk.attrs[og_key]
                if field_name == "price":
                    result[field_name] = _to_float(value)
                else:
                    result[field_name] = sanitize_text(str(value), max_len=200)
    return result


# --- h1 fallback ---


def _parse_h1(heading_chunks: list[HtmlChunk]) -> str | None:
    """Extract first valid h1 text."""
    for chunk in heading_chunks:
        if chunk.tag == "h1" and chunk.text:
            text = chunk.text.strip()
            if 3 < len(text) < 300:
                return text
    return None


# --- Public API ---


def extract_metadata(
    meta_chunks: list[HtmlChunk],
    heading_chunks: list[HtmlChunk],
    schema_name: str,
) -> dict[str, Any]:
    """Extract structured metadata from pre-parsed HtmlChunks.

    Priority: JSON-LD > itemprop > OG meta > h1 fallback.
    """
    sources = [
        _parse_json_ld_product(meta_chunks) if schema_name == "Product" else {},
        _parse_itemprop(heading_chunks, schema_name),
        _parse_og_meta(meta_chunks, schema_name),
    ]

    result: dict[str, Any] = {}
    for fields in sources:
        for k, v in fields.items():
            if k not in result and v is not None:
                result[k] = v

    # h1 fallback for name/headline
    name_key = "headline" if schema_name == "NewsArticle" else "name"
    if name_key not in result:
        h1 = _parse_h1(heading_chunks)
        if h1:
            result[name_key] = h1

    # ItemList extraction for listing/search_results pages
    if schema_name == "Product":
        itemlist_items = _parse_json_ld_itemlist(meta_chunks)
        if itemlist_items:
            result["items"] = itemlist_items

    return result
