# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured metadata extraction from pre-parsed HtmlChunks.

Cascade priority: JSON-LD > itemprop > OG meta > h1 fallback.
Zero regex, zero lxml, zero I/O -- json.loads + dict lookups only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
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


def _is_valid_url(url: Any) -> str | None:
    """Validate URL: must be string, <=2048 chars, http(s) or protocol-relative."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    return url if url.startswith(("http://", "https://", "//")) else None


def _extract_image_url(data: dict) -> str | None:
    """Extract and validate image URL from JSON-LD data."""
    img = data.get("image")
    if isinstance(img, list):
        return _is_valid_url(img[0]) if img else None
    return _is_valid_url(img)


def _extract_person_or_org_name(val: Any, max_len: int = 200) -> str | None:
    """Extract name from a Person/Organization object or plain string."""
    if isinstance(val, dict):
        name = val.get("name")
        if name:
            return sanitize_text(str(name).strip(), max_len=max_len)
    elif isinstance(val, str) and val.strip():
        return sanitize_text(val.strip(), max_len=max_len)
    return None


# --- JSON-LD generic type finder ---


def _find_type_in_jsonld(data: Any, type_names: tuple[str, ...]) -> dict | None:
    """Find first object with matching @type in JSON-LD data (handles @graph, arrays, list types)."""
    if isinstance(data, list):
        for item in data:
            found = _find_type_in_jsonld(item, type_names)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _find_type_in_jsonld(data["@graph"], type_names)
    schema_type = data.get("@type", "")
    if isinstance(schema_type, list):
        if any(t in type_names for t in schema_type):
            return data
    elif schema_type in type_names:
        return data
    return None


# --- JSON-LD 1-pass chunk parsing ---


def _parse_jsonld_chunks(meta_chunks: list[HtmlChunk]) -> list[Any]:
    """Parse all application/ld+json chunks once. Returns list of parsed data."""
    parsed = []
    for chunk in meta_chunks:
        if chunk.attrs.get("type") != "application/ld+json":
            continue
        try:
            parsed.append(json.loads(chunk.text))
        except (json.JSONDecodeError, TypeError):
            continue
    return parsed


# --- JSON-LD parsing: Product ---

_PRODUCT_TYPES = ("Product", "IndividualProduct")


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


def _parse_json_ld_product(parsed_data: list[Any]) -> dict[str, Any]:
    """Extract Product fields from pre-parsed JSON-LD data."""
    for data in parsed_data:
        product = _find_type_in_jsonld(data, _PRODUCT_TYPES)
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

        img_url = _extract_image_url(product)
        if img_url:
            result["image_url"] = img_url

        return result
    return {}


# --- JSON-LD ItemList parsing ---


def _parse_json_ld_itemlist(parsed_data: list[Any]) -> list[dict[str, Any]]:
    """Extract product items from JSON-LD ItemList.

    Returns a list of dicts with keys: name, price, brand, url, position.
    """
    for data in parsed_data:
        itemlist = _find_type_in_jsonld(data, ("ItemList",))
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


# --- JSON-LD parsing: NewsArticle ---

_NEWS_ARTICLE_TYPES = ("NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting")


def _parse_json_ld_news_article(parsed_data: list[Any]) -> dict[str, Any]:
    """Extract NewsArticle fields from pre-parsed JSON-LD data."""
    for data in parsed_data:
        article = _find_type_in_jsonld(data, _NEWS_ARTICLE_TYPES)
        if not article:
            continue

        result: dict[str, Any] = {}

        headline = article.get("headline") or article.get("name")
        if headline:
            result["headline"] = sanitize_text(str(headline).strip(), max_len=256)

        author = _extract_person_or_org_name(article.get("author"))
        if author:
            result["author"] = author

        if article.get("datePublished"):
            result["date_published"] = str(article["datePublished"])

        publisher = _extract_person_or_org_name(article.get("publisher"))
        if publisher:
            result["publisher"] = publisher

        if article.get("articleBody"):
            result["article_body"] = sanitize_text(str(article["articleBody"]).strip(), max_len=200)

        img_url = _extract_image_url(article)
        if img_url:
            result["image_url"] = img_url

        return result
    return {}


# --- JSON-LD parsing: BreadcrumbList ---


def _parse_json_ld_breadcrumblist(parsed_data: list[Any]) -> list[dict[str, Any]]:
    """Extract BreadcrumbList items from pre-parsed JSON-LD data.

    Returns sorted list of {name, url, position} dicts. Empty list if none found.
    """
    for data in parsed_data:
        bc = _find_type_in_jsonld(data, ("BreadcrumbList",))
        if not bc:
            continue

        elements = bc.get("itemListElement", [])
        if not isinstance(elements, list):
            continue

        crumbs: list[dict[str, Any]] = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            crumb: dict[str, Any] = {}

            pos = el.get("position")
            if pos is not None:
                crumb["position"] = _to_int(pos)

            item = el.get("item")
            if isinstance(item, dict):
                if item.get("name"):
                    crumb["name"] = str(item["name"])
                url = _is_valid_url(item.get("@id") or item.get("url"))
                if url:
                    crumb["url"] = url
            elif isinstance(item, str):
                url = _is_valid_url(item)
                if url:
                    crumb["url"] = url

            # name can also be at element level
            if "name" not in crumb and el.get("name"):
                crumb["name"] = str(el["name"])

            if crumb.get("name"):
                crumbs.append(crumb)

        if crumbs:
            crumbs.sort(key=lambda c: c.get("position", 0) or 0)
            return crumbs
    return []


# --- JSON-LD parsing: FAQPage ---


def _parse_json_ld_faq_page(parsed_data: list[Any]) -> dict[str, Any]:
    """Extract FAQPage fields from pre-parsed JSON-LD data."""
    for data in parsed_data:
        faq = _find_type_in_jsonld(data, ("FAQPage",))
        if not faq:
            continue

        result: dict[str, Any] = {}

        if faq.get("name"):
            result["name"] = sanitize_text(str(faq["name"]).strip(), max_len=256)

        main_entity = faq.get("mainEntity", [])
        if not isinstance(main_entity, list):
            main_entity = [main_entity] if isinstance(main_entity, dict) else []

        questions: list[dict[str, str]] = []
        for entity in main_entity:
            if not isinstance(entity, dict):
                continue
            if entity.get("@type") != "Question":
                continue

            q_text = entity.get("name")
            if not q_text:
                continue

            qa: dict[str, str] = {
                "question": sanitize_text(str(q_text).strip(), max_len=256),
            }

            # acceptedAnswer → suggestedAnswer fallback
            answer_obj = entity.get("acceptedAnswer") or entity.get("suggestedAnswer")
            if isinstance(answer_obj, dict) and answer_obj.get("text"):
                qa["answer"] = sanitize_text(str(answer_obj["text"]).strip(), max_len=500)

            questions.append(qa)

        if questions:
            result["questions"] = questions

        return result
    return {}


# --- JSON-LD parsing: Event ---

_EVENT_TYPES = (
    "Event",
    "MusicEvent",
    "SportsEvent",
    "TheaterEvent",
    "BusinessEvent",
    "EducationEvent",
    "Festival",
    "ExhibitionEvent",
)

_VALID_EVENT_STATUSES = frozenset(
    {
        "EventScheduled",
        "EventCancelled",
        "EventPostponed",
        "EventRescheduled",
        "EventMovedOnline",
    }
)


def _extract_event_location(loc: Any) -> str | None:
    """Extract location string from Event location field (Place, VirtualLocation, str, list)."""
    if isinstance(loc, str) and loc.strip():
        return sanitize_text(loc.strip(), max_len=200)
    if isinstance(loc, list):
        # Hybrid: multiple locations — join names
        parts = [_extract_event_location(item) for item in loc]
        joined = ", ".join(p for p in parts if p)
        return joined if joined else None
    if not isinstance(loc, dict):
        return None

    loc_type = loc.get("@type", "")
    if loc_type == "VirtualLocation":
        url = _is_valid_url(loc.get("url"))
        if url:
            return url
        name = loc.get("name")
        return sanitize_text(str(name).strip(), max_len=200) if name else None

    # Place or similar
    parts = []
    if loc.get("name"):
        parts.append(str(loc["name"]))
    addr = loc.get("address")
    if isinstance(addr, dict):
        addr_parts = [
            addr.get("streetAddress"),
            addr.get("addressLocality"),
            addr.get("addressRegion"),
            addr.get("postalCode"),
        ]
        addr_str = ", ".join(str(p) for p in addr_parts if p)
        if addr_str:
            parts.append(addr_str)
    elif isinstance(addr, str) and addr.strip():
        parts.append(addr.strip())

    return sanitize_text(", ".join(parts), max_len=200) if parts else None


def _parse_json_ld_event(parsed_data: list[Any]) -> dict[str, Any]:
    """Extract Event fields from pre-parsed JSON-LD data."""
    for data in parsed_data:
        event = _find_type_in_jsonld(data, _EVENT_TYPES)
        if not event:
            continue

        result: dict[str, Any] = {}

        if event.get("name"):
            result["name"] = sanitize_text(str(event["name"]).strip(), max_len=256)

        if event.get("startDate"):
            result["start_date"] = str(event["startDate"])
        if event.get("endDate"):
            result["end_date"] = str(event["endDate"])

        location = _extract_event_location(event.get("location"))
        if location:
            result["location"] = location

        status = event.get("eventStatus", "")
        if isinstance(status, str):
            # Strip schema.org prefix if present
            short = status.rsplit("/", 1)[-1]
            if short in _VALID_EVENT_STATUSES:
                result["event_status"] = short

        performer = _extract_person_or_org_name(event.get("performer"))
        if performer:
            result["performer"] = performer

        organizer = _extract_person_or_org_name(event.get("organizer"))
        if organizer:
            result["organizer"] = organizer

        if event.get("description"):
            result["description"] = sanitize_text(str(event["description"]).strip(), max_len=200)

        # Reuse price extraction from offers
        offers = event.get("offers")
        if offers:
            result.update(_extract_price_from_offers(offers))

        img_url = _extract_image_url(event)
        if img_url:
            result["image_url"] = img_url

        url = _is_valid_url(event.get("url"))
        if url:
            result["url"] = url

        return result
    return {}


# --- JSON-LD parsing: LocalBusiness ---

_LOCAL_BUSINESS_TYPES = (
    "LocalBusiness",
    "Restaurant",
    "Hotel",
    "Store",
    "MedicalClinic",
    "FoodEstablishment",
    "HealthAndBeautyBusiness",
    "AutoRepair",
    "Dentist",
    "RealEstateAgent",
)


def _extract_address(addr: Any) -> str | None:
    """Extract address string from PostalAddress object or plain string."""
    if isinstance(addr, str) and addr.strip():
        return sanitize_text(addr.strip(), max_len=200)
    if not isinstance(addr, dict):
        return None
    parts = [
        addr.get("streetAddress"),
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("postalCode"),
    ]
    result = ", ".join(str(p) for p in parts if p)
    return sanitize_text(result, max_len=200) if result else None


def _extract_geo(geo: Any) -> dict[str, float] | None:
    """Extract latitude/longitude from GeoCoordinates object."""
    if not isinstance(geo, dict):
        return None
    lat = _to_float(geo.get("latitude"))
    lon = _to_float(geo.get("longitude"))
    if lat is not None and lon is not None:
        return {"latitude": lat, "longitude": lon}
    return None


def _extract_opening_hours(biz: dict) -> str | None:
    """Extract opening hours from structured spec or plain string."""
    # Prefer structured openingHoursSpecification
    spec = biz.get("openingHoursSpecification")
    if isinstance(spec, list) and spec:
        parts = []
        for entry in spec:
            if not isinstance(entry, dict):
                continue
            days = entry.get("dayOfWeek", [])
            if isinstance(days, str):
                days = [days]
            opens = entry.get("opens", "")
            closes = entry.get("closes", "")
            if days and opens:
                day_str = ", ".join(str(d) for d in days)
                parts.append(f"{day_str}: {opens}-{closes}")
        if parts:
            return "; ".join(parts)

    # Fallback to openingHours string
    oh = biz.get("openingHours")
    if isinstance(oh, str) and oh.strip():
        return oh.strip()
    if isinstance(oh, list):
        return "; ".join(str(h) for h in oh if h)
    return None


def _parse_json_ld_local_business(parsed_data: list[Any]) -> dict[str, Any]:
    """Extract LocalBusiness fields from pre-parsed JSON-LD data."""
    for data in parsed_data:
        biz = _find_type_in_jsonld(data, _LOCAL_BUSINESS_TYPES)
        if not biz:
            continue

        result: dict[str, Any] = {}

        if biz.get("name"):
            result["name"] = sanitize_text(str(biz["name"]).strip(), max_len=256)

        if biz.get("telephone"):
            result["telephone"] = str(biz["telephone"]).strip()

        if biz.get("priceRange"):
            result["price_range"] = str(biz["priceRange"]).strip()

        url = _is_valid_url(biz.get("url"))
        if url:
            result["url"] = url

        address = _extract_address(biz.get("address"))
        if address:
            result["address"] = address

        geo = _extract_geo(biz.get("geo"))
        if geo:
            result["geo"] = geo

        hours = _extract_opening_hours(biz)
        if hours:
            result["opening_hours"] = hours

        # aggregateRating — reuse Product pattern
        agg = biz.get("aggregateRating", {})
        if isinstance(agg, dict):
            if agg.get("ratingValue"):
                result["rating"] = _to_float(agg["ratingValue"])
            if agg.get("reviewCount"):
                result["review_count"] = _to_int(agg["reviewCount"])

        img_url = _extract_image_url(biz)
        if img_url:
            result["image_url"] = img_url

        return result
    return {}


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
    "Event": {
        "name": "name",
        "startDate": "start_date",
        "endDate": "end_date",
        "location": "location",
    },
    "LocalBusiness": {
        "name": "name",
        "telephone": "telephone",
        "priceRange": "price_range",
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
    "Event": {
        "og:title": "name",
        "og:description": "description",
        "og:image": "image_url",
    },
    "LocalBusiness": {
        "og:title": "name",
        "og:image": "image_url",
    },
    "SaaSPage": {
        "og:title": "name",
        "og:description": "description",
        "og:site_name": "publisher",
    },
    "GovernmentPage": {
        "og:title": "title",
        "og:description": "description",
        "og:site_name": "department",
    },
    "WikiArticle": {
        "og:title": "title",
        "og:description": "summary",
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


# --- JSON-LD dispatch registry ---

_JSONLD_PARSERS: dict[str, Callable[[list[Any]], dict[str, Any]]] = {
    "Product": _parse_json_ld_product,
    "NewsArticle": _parse_json_ld_news_article,
    "FAQPage": _parse_json_ld_faq_page,
    "Event": _parse_json_ld_event,
    "LocalBusiness": _parse_json_ld_local_business,
}


# --- Public API ---


def extract_metadata(
    meta_chunks: list[HtmlChunk],
    heading_chunks: list[HtmlChunk],
    schema_name: str,
    source_hint: str | None = None,
) -> dict[str, Any]:
    """Extract structured metadata from pre-parsed HtmlChunks.

    Priority: JSON-LD > itemprop > OG meta > h1 fallback.

    If source_hint is provided (e.g. "json_ld"), tries the hinted source first
    and skips the remaining cascade if it yields sufficient results.
    """
    # 1. Parse all JSON-LD chunks once
    parsed_data = _parse_jsonld_chunks(meta_chunks)

    # 2. BreadcrumbList — auxiliary extraction (any schema)
    breadcrumbs = _parse_json_ld_breadcrumblist(parsed_data)

    # 3. Primary JSON-LD parser
    jsonld_parser = _JSONLD_PARSERS.get(schema_name)
    jsonld_result = jsonld_parser(parsed_data) if jsonld_parser else {}

    # Optimistic path: if hint says json_ld and parser succeeded, skip cascade
    if source_hint == "json_ld" and jsonld_result:
        result = jsonld_result
        name_key = "headline" if schema_name == "NewsArticle" else "name"
        if name_key not in result:
            h1 = _parse_h1(heading_chunks)
            if h1:
                result[name_key] = h1

        if schema_name == "Product":
            itemlist_items = _parse_json_ld_itemlist(parsed_data)
            if itemlist_items:
                result["items"] = itemlist_items

        if breadcrumbs:
            result["breadcrumbs"] = breadcrumbs

        return result

    sources = [
        jsonld_result,
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
        itemlist_items = _parse_json_ld_itemlist(parsed_data)
        if itemlist_items:
            result["items"] = itemlist_items

    # Attach breadcrumbs if found
    if breadcrumbs:
        result["breadcrumbs"] = breadcrumbs

    return result
