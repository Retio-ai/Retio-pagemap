# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured metadata extraction from pre-parsed HtmlChunks.

Cascade priority: JSON-LD > itemprop > OG meta > h1 fallback.
Cascade priority: JSON-LD > itemprop > OG meta > h1 fallback.
Uses lxml for last-resort price extraction from pruned HTML (Amazon nested spans).
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.sanitizer import sanitize_text

logger = logging.getLogger(__name__)

# --- Helpers ---


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        s = str(v).strip()
        if "." in s and "," in s and s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return round(f) if f is not None else None


def _is_valid_url(url: Any) -> str | None:
    """Validate URL: must be string, <=2048 chars, http(s) or protocol-relative."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    return url if url.startswith(("http://", "https://", "//")) else None


def _extract_image_url(data: dict) -> str | None:
    """Extract and validate image URL from JSON-LD data."""
    img = data.get("image")
    if isinstance(img, dict):
        u = img.get("url")
        return _is_valid_url(u if u is not None else img.get("contentUrl"))
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


def _find_type_in_jsonld(data: Any, type_names: tuple[str, ...], max_depth: int = 5) -> dict | None:
    """Find first object with matching @type in JSON-LD data (handles @graph, arrays, list types)."""
    if max_depth <= 0:
        return None
    if isinstance(data, list):
        for item in data:
            found = _find_type_in_jsonld(item, type_names, max_depth - 1)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _find_type_in_jsonld(data["@graph"], type_names, max_depth - 1)
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
        lp = offers.get("lowPrice")
        result["price"] = _to_float(lp if lp is not None else offers.get("price"))
        inner = offers.get("offers")
        if isinstance(inner, list) and inner:
            inner_price = _to_float(inner[0].get("price"))
            result["price"] = inner_price if inner_price is not None else result.get("price")
    else:
        result["price"] = _to_float(offers.get("price"))

    pc = offers.get("priceCurrency")
    if pc:
        result["currency"] = sanitize_text(str(pc).strip(), max_len=200)
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
            result["date_published"] = sanitize_text(str(article["datePublished"]).strip(), max_len=200)

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


# --- JSON-LD parsing: VideoObject ---

_VIDEO_TYPES = ("VideoObject",)

_INTERACTION_TYPE_MAP: dict[str, str] = {
    "WatchAction": "view_count",
    "LikeAction": "like_count",
    "CommentAction": "comment_count",
    "DislikeAction": "dislike_count",
}


def _parse_interaction_statistics(stats: Any) -> dict[str, int]:
    """Parse interactionStatistic array from VideoObject."""
    result: dict[str, int] = {}
    if not isinstance(stats, list):
        stats = [stats] if isinstance(stats, dict) else []
    for stat in stats:
        if not isinstance(stat, dict):
            continue
        interaction_type = stat.get("interactionType")
        if isinstance(interaction_type, dict):
            interaction_type = interaction_type.get("@type", "")
        elif isinstance(interaction_type, str):
            # Strip schema.org URL prefix if present
            interaction_type = interaction_type.rsplit("/", 1)[-1]
        else:
            continue
        field = _INTERACTION_TYPE_MAP.get(interaction_type)
        if field:
            count = _to_int(stat.get("userInteractionCount"))
            if count is not None:
                result[field] = count
    return result


def _parse_json_ld_video(parsed_data: list[Any]) -> dict[str, Any]:
    """Extract VideoObject fields from pre-parsed JSON-LD data.

    Based on Google Video structured data spec:
    Required: name, thumbnailUrl, uploadDate
    Recommended: description, duration, contentUrl, embedUrl
    """
    for data in parsed_data:
        video = _find_type_in_jsonld(data, _VIDEO_TYPES)
        if not video:
            continue

        result: dict[str, Any] = {}

        if video.get("name"):
            result["name"] = sanitize_text(str(video["name"]).strip(), max_len=256)

        if video.get("description"):
            result["description"] = sanitize_text(str(video["description"]).strip(), max_len=500)

        if video.get("uploadDate"):
            result["upload_date"] = sanitize_text(str(video["uploadDate"]).strip(), max_len=200)

        if video.get("duration"):
            result["duration"] = sanitize_text(str(video["duration"]).strip(), max_len=200)

        # Channel from author (Person or Organization)
        channel = _extract_person_or_org_name(video.get("author"))
        if channel:
            result["channel"] = channel

        # Interaction statistics (views, likes, comments)
        stats = video.get("interactionStatistic")
        if stats:
            result.update(_parse_interaction_statistics(stats))

        # Thumbnail URL
        thumb = video.get("thumbnailUrl")
        if isinstance(thumb, list):
            thumb = thumb[0] if thumb else None
        url = _is_valid_url(thumb)
        if url:
            result["thumbnail_url"] = url

        img_url = _extract_image_url(video)
        if img_url and "thumbnail_url" not in result:
            result["thumbnail_url"] = img_url

        logger.debug(
            "VideoObject JSON-LD: extracted=%s missing=%s",
            sorted(result.keys()),
            sorted({"name", "description", "upload_date", "duration", "channel", "thumbnail_url"} - result.keys()),
        )
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
                    crumb["name"] = sanitize_text(str(item["name"]).strip(), max_len=200)
                url = _is_valid_url(item.get("@id") or item.get("url"))
                if url:
                    crumb["url"] = url
            elif isinstance(item, str):
                url = _is_valid_url(item)
                if url:
                    crumb["url"] = url

            # name can also be at element level
            if "name" not in crumb and el.get("name"):
                crumb["name"] = sanitize_text(str(el["name"]).strip(), max_len=200)

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
            result["start_date"] = sanitize_text(str(event["startDate"]).strip(), max_len=200)
        if event.get("endDate"):
            result["end_date"] = sanitize_text(str(event["endDate"]).strip(), max_len=200)

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
            result["telephone"] = sanitize_text(str(biz["telephone"]).strip(), max_len=200)

        if biz.get("priceRange"):
            result["price_range"] = sanitize_text(str(biz["priceRange"]).strip(), max_len=200)

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
    "VideoObject": {
        "name": "name",
        "author": "channel",
        "uploadDate": "upload_date",
        "duration": "duration",
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
    "VideoObject": {
        "og:title": "name",
        "og:description": "description",
        "og:image": "thumbnail_url",
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
                elif field_name in ("image_url", "thumbnail_url"):
                    validated = _is_valid_url(_html.unescape(str(value)))
                    if validated:
                        result[field_name] = validated
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
                return sanitize_text(text, max_len=300)
    return None


# --- JSON-LD dispatch registry ---

_JSONLD_PARSERS: dict[str, Callable[[list[Any]], dict[str, Any]]] = {
    "Product": _parse_json_ld_product,
    "NewsArticle": _parse_json_ld_news_article,
    "FAQPage": _parse_json_ld_faq_page,
    "Event": _parse_json_ld_event,
    "LocalBusiness": _parse_json_ld_local_business,
    "VideoObject": _parse_json_ld_video,
}


# --- DOM-based price fallback for Product ---

_DOM_PRICE_RE = re.compile(
    r"(?:₩|원|\$|€|£|¥|₩)\s*[\d,]+(?:\.\d{2})?|\d[\d,]+(?:\.\d{2})?\s*(?:원|円)",
)
_SHIPPING_KEYWORDS = re.compile(r"(?:shipping|handling|delivery|배송|운임)", re.IGNORECASE)


def _extract_price_from_html(html: str) -> float | None:
    """Last-resort price extraction from raw/pruned HTML using lxml DOM traversal.

    Handles nested Amazon price structures and aria-label fallbacks.
    Priority: a-offscreen text > price-class text_content > aria-label.
    """
    if not html:
        return None
    try:
        from lxml.html import fromstring

        doc = fromstring(html)
    except Exception:
        return None

    offscreen: list[float] = []
    class_content: list[float] = []
    aria: list[float] = []

    for el in doc.iter():
        cls = (el.get("class") or "").lower()
        if not any(kw in cls for kw in ("a-price", "a-offscreen", "price")):
            continue

        # 1. a-offscreen: Amazon's accessible price (always full text)
        if "a-offscreen" in cls:
            text = (el.text or "").strip()
            if text and not _SHIPPING_KEYWORDS.search(text):
                m = _DOM_PRICE_RE.search(text)
                if m:
                    val = _to_float(re.sub(r"[^\d.,]", "", m.group()))
                    if val is not None and val > 0:
                        offscreen.append(val)
                        continue

        # 2. text_content() for any price-classed element (handles nesting)
        full_text = el.text_content().strip()
        if full_text and not _SHIPPING_KEYWORDS.search(full_text):
            m = _DOM_PRICE_RE.search(full_text)
            if m:
                val = _to_float(re.sub(r"[^\d.,]", "", m.group()))
                if val is not None and val > 0:
                    class_content.append(val)

        # 3. aria-label fallback
        aria_text = (el.get("aria-label") or "").strip()
        if aria_text and not _SHIPPING_KEYWORDS.search(aria_text):
            m = _DOM_PRICE_RE.search(aria_text)
            if m:
                val = _to_float(re.sub(r"[^\d.,]", "", m.group()))
                if val is not None and val > 0:
                    aria.append(val)

    # Priority: a-offscreen > class text_content > aria-label
    for candidates in (offscreen, class_content, aria):
        if candidates:
            return candidates[0]
    return None


# --- DOM-based video metadata fallback ---

_CHANNEL_CLASS_KEYWORDS = ("channel-name", "owner-name", "ytd-channel-name", "uploader")
_VIEW_COUNT_RE = re.compile(
    r"([\d,.]+(?:\s[\d,.]+)*)\s*(?:views?|회\s*조회)|조회수\s*([\d,.]+(?:\s[\d,.]+)*)",
    re.IGNORECASE,
)
_DURATION_CLASS_KEYWORDS = ("ytp-time-duration", "video-time")


def _extract_video_meta_from_dom(heading_chunks: list[HtmlChunk]) -> dict[str, Any]:
    """Best-effort extraction of video metadata from DOM chunks.

    Scans heading_chunks for video-specific class names and text patterns.
    This is a last-resort fallback after the JSON-LD → itemprop → OG cascade.

    Note: heading_chunks only contains h1 elements and elements with itemprop
    attributes (pipeline.py:109). A class-name match only helps when the DOM
    element also carries an itemprop attribute that placed it in heading_chunks.
    """
    result: dict[str, Any] = {}
    for chunk in heading_chunks:
        cls = chunk.attrs.get("class", "").lower()
        text = chunk.text.strip()
        if not text:
            continue

        # Channel: require class-name match (text alone is too ambiguous)
        if "channel" not in result and cls:
            if any(kw in cls for kw in _CHANNEL_CLASS_KEYWORDS):
                sanitized = sanitize_text(text, max_len=200)
                if sanitized:
                    result["channel"] = sanitized

        # View count: class match OR text-only regex "N views" / "조회수 N"
        if "view_count" not in result:
            m = _VIEW_COUNT_RE.search(text)
            if m:
                raw = (m.group(1) or m.group(2) or "").strip()
                count = _to_int(raw)
                if count is not None:
                    result["view_count"] = count

        # Duration: require class-name match (too generic otherwise)
        if "duration" not in result and cls:
            if any(kw in cls for kw in _DURATION_CLASS_KEYWORDS):
                sanitized = sanitize_text(text, max_len=200)
                if sanitized:
                    result["duration"] = sanitized

    return result


def _extract_price_from_dom_chunks(heading_chunks: list[HtmlChunk]) -> float | None:
    """Extract price from DOM chunks using class/text pattern matching.

    Checks for Amazon-style a-price / a-offscreen classes, then falls back
    to price pattern in text content.
    """
    candidates: list[tuple[float, bool]] = []  # (price, is_price_class)
    for chunk in heading_chunks:
        cls = chunk.attrs.get("class", "")
        chunk_id = chunk.attrs.get("id", "")
        text = chunk.text.strip()

        # When text is empty, try alternative sources before skipping
        if not text:
            # aria-label fallback
            text = chunk.attrs.get("aria-label", "").strip()
            if not text:
                # data-* attribute fallback (e.g. data-a-price, data-price)
                for attr_key, attr_val in chunk.attrs.items():
                    if attr_key.startswith("data-") and "price" in attr_key.lower() and attr_val:
                        text = str(attr_val).strip()
                        break
            if not text:
                continue

        # Skip shipping/handling prices
        combined = f"{cls} {chunk_id} {text}"
        if _SHIPPING_KEYWORDS.search(combined):
            continue
        # Priority: elements with price-related class names
        is_price_class = any(kw in cls.lower() or kw in chunk_id.lower() for kw in ("a-price", "a-offscreen", "price"))
        match = _DOM_PRICE_RE.search(text)
        if match:
            val = _to_float(re.sub(r"[^\d.,]", "", match.group()))
            if val is not None and val > 0:
                candidates.append((val, is_price_class))
    # Prefer price-class matches
    price_class_candidates = [p for p, is_cls in candidates if is_cls]
    if price_class_candidates:
        return price_class_candidates[0]
    all_candidates = [p for p, _ in candidates]
    return all_candidates[0] if all_candidates else None


# --- Public API ---


def extract_metadata(
    meta_chunks: list[HtmlChunk],
    heading_chunks: list[HtmlChunk],
    schema_name: str,
    source_hint: str | None = None,
    pruned_html: str | None = None,
) -> dict[str, Any]:
    """Extract structured metadata from pre-parsed HtmlChunks.

    Priority: JSON-LD > itemprop > OG meta > h1 fallback.

    If source_hint is provided (e.g. "json_ld"), tries the hinted source first
    and skips the remaining cascade if it yields sufficient results.

    pruned_html: optional pruned HTML for lxml-based price extraction fallback.
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

        if schema_name == "VideoObject":
            for k, v in _extract_video_meta_from_dom(heading_chunks).items():
                if k not in result:
                    result[k] = v

        if schema_name == "Product":
            itemlist_items = _parse_json_ld_itemlist(parsed_data)
            if itemlist_items:
                result["items"] = itemlist_items

            # DOM/HTML price fallback — JSON-LD Product without price
            if "price" not in result:
                dom_price = _extract_price_from_dom_chunks(heading_chunks)
                if dom_price is not None:
                    result["price"] = dom_price
                elif pruned_html:
                    html_price = _extract_price_from_html(pruned_html)
                    if html_price is not None:
                        result["price"] = html_price

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

    # DOM price fallback for Product schema
    if schema_name == "Product" and "price" not in result:
        dom_price = _extract_price_from_dom_chunks(heading_chunks)
        if dom_price is not None:
            result["price"] = dom_price
        elif pruned_html:
            html_price = _extract_price_from_html(pruned_html)
            if html_price is not None:
                result["price"] = html_price

    # DOM video metadata fallback for VideoObject schema
    if schema_name == "VideoObject":
        for k, v in _extract_video_meta_from_dom(heading_chunks).items():
            if k not in result:
                result[k] = v

    # Attach breadcrumbs if found
    if breadcrumbs:
        result["breadcrumbs"] = breadcrumbs

    return result
