# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Pruned context builder: aggressive HTML compression for PageMap.

Wraps the pruning2 pipeline and applies additional compression to meet
the tight token budget (500-1500 tokens for pruned_context).

Strategy per page type:
- product_detail: product name (h1) + price + rating + review count + options
- search_results: result list items + pagination info
- article: title + first paragraph + date + author
- default: headings + first significant text blocks
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from pagemap.i18n import (
    FILTER_TERMS,
    LISTING_TERMS,
    LOAD_MORE_TERMS,
    NEXT_BUTTON_TERMS,
    OPTION_TERMS,
    PREV_BUTTON_TERMS,
    PRICE_LABEL_TERMS,
    SEARCH_RESULT_TERMS,
    LocaleConfig,
    get_locale,
)
from pagemap.preprocessing.preprocess import count_tokens
from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.pipeline import prune_page

logger = logging.getLogger(__name__)

# Maximum tokens for pruned_context section
DEFAULT_MAX_TOKENS = 1500

# Pre-computed lowered option keywords for _compress_for_product
_option_kw = tuple(t.lower() for t in OPTION_TERMS)

# Patterns for extracting key information (multilingual)
PRICE_PATTERN = re.compile(
    r"(?:₩\s*[\d,]+|\d[\d,]+\s*원"
    r"|\d[\d,]+\s*円|¥\s*[\d,]+"
    r"|£\s*[\d,]+(?:\.\d{2})?"
    r"|€\s*[\d,]+(?:\.\d{2})?"
    r"|\$\d+(?:\.\d{2})?"
    r"|USD\s*[\d,.]+|EUR\s*[\d,.]+|CHF\s*[\d,.]+"
    r"|\d{2,3}(?:,\d{3})+)" + r"|" + "|".join(re.escape(t) for t in PRICE_LABEL_TERMS),
)
RATING_PATTERN = re.compile(
    r"(?:★|⭐|평점|별점|\d+\.\d+\s*[/점]|\d+(?:\.\d+)?점|리뷰\s*\d+"
    r"|評価|レビュー|étoile|Bewertung|Sterne)",
)


# Patterns for image extraction
_IMG_TAG_PATTERN = re.compile(r"<img\b[^>]*?>", re.IGNORECASE | re.DOTALL)
_IMG_ATTR_PATTERNS = [
    re.compile(r'\bsrc=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'\bdata-src=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'\bdata-lazy-src=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'\bdata-original=["\']([^"\']+)["\']', re.IGNORECASE),
]
_SRCSET_PATTERN = re.compile(r'\bsrcset=["\']([^"\']+)["\']', re.IGNORECASE)
_PRODUCT_IMG_HINTS = re.compile(
    r"(product|goods|item|detail|gallery|pdp|zoom|main[-_]?img|swiper|slide|hero|primary)",
    re.IGNORECASE,
)
_EXCLUDE_IMG_PATTERNS = re.compile(
    r"(icon|logo|banner|sprite|ad[_\-]|tracking|pixel|1x1|spacer|blank|svg\+xml|data:image/(?:gif|svg))",
    re.IGNORECASE,
)

# Security: allowed URL schemes for image extraction
_ALLOWED_URL_PREFIXES = ("http://", "https://", "//")
_MAX_URL_LENGTH = 2048


def extract_product_images(raw_html: str, base_url: str = "") -> list[str]:
    """Extract likely product image URLs from HTML.

    Strategy:
    1. Find all <img> tags and extract src/data-src/data-lazy-src/srcset
    2. Filter out icons, logos, banners, tracking pixels, ads
    3. Prioritize images with product-related class/id/alt hints
    4. Resolve relative URLs, deduplicate, limit to 10
    """
    img_tags = _IMG_TAG_PATTERN.findall(raw_html)
    candidates: list[tuple[str, bool]] = []  # (url, has_product_hint)
    seen_urls: set[str] = set()

    for tag in img_tags:
        # Check if this tag has product-related attributes
        has_hint = bool(_PRODUCT_IMG_HINTS.search(tag))

        # Extract URLs from various attributes
        urls: list[str] = []
        for pat in _IMG_ATTR_PATTERNS:
            m = pat.search(tag)
            if m:
                urls.append(m.group(1))

        # Parse srcset (take the largest image)
        srcset_m = _SRCSET_PATTERN.search(tag)
        if srcset_m:
            srcset_parts = srcset_m.group(1).split(",")
            for part in srcset_parts:
                part = part.strip()
                if part:
                    url_part = part.split()[0]
                    if url_part:
                        urls.append(url_part)

        for url in urls:
            url = url.strip()
            if not url:
                continue
            # Allowlist: only http/https/protocol-relative absolute URLs allowed.
            # Relative URLs (/path, path) pass through for urljoin resolution.
            url_lower = url.lower()
            if ":" in url_lower.split("/")[0] and not url_lower.startswith(_ALLOWED_URL_PREFIXES):
                continue
            if len(url) > _MAX_URL_LENGTH:
                continue

            # Filter out non-product images
            if _EXCLUDE_IMG_PATTERNS.search(url):
                continue

            # Resolve relative URLs
            if base_url and not url.startswith(("http://", "https://", "//")):
                url = urljoin(base_url, url)
            elif url.startswith("//"):
                url = "https:" + url

            if url in seen_urls:
                continue
            seen_urls.add(url)

            candidates.append((url, has_hint))

    # Sort: product-hint images first, then order of appearance
    prioritized = sorted(candidates, key=lambda x: (not x[1],))

    return [url for url, _ in prioritized[:10]]


def _extract_text_lines(html: str) -> list[str]:
    """Extract visible text lines from HTML, preserving key structure."""
    # Remove script/style
    cleaned = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "\n", cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines


# ---------------------------------------------------------------------------
# Card detection for listing / search_results pages
# ---------------------------------------------------------------------------

# Price pattern used for card detection (reuse PRICE_PATTERN for line-level)
_CARD_PRICE_RE = re.compile(
    r"(?:₩\s*[\d,]+|\d[\d,]+\s*원|\d[\d,]+\s*円|¥\s*[\d,]+"
    r"|\d{2,3}(?:,\d{3})+(?:\s*원)?"
    r"|\$\d+(?:\.\d{2})?|€\s*[\d,.]+|£\s*[\d,.]+"
    r"|USD\s*[\d,.]+|EUR\s*[\d,.]+|CHF\s*[\d,.]+)",
)


def _detect_cards_from_metadata(metadata: dict | None) -> list[dict[str, Any]]:
    """Extract product cards from JSON-LD ItemList metadata (highest confidence)."""
    if not metadata or "items" not in metadata:
        return []
    items = metadata["items"]
    if not isinstance(items, list):
        return []
    cards: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        cards.append(item)
    return cards


def _detect_cards_from_chunks(chunks: list[HtmlChunk]) -> list[dict[str, Any]]:
    """Detect product cards from pruned chunks.

    Strategy cascade:
    1. LIST/TABLE chunks — parse <li> items for name+price pairs
    2. parent_xpath grouping — group chunks by parent, pair name+price
    3. Adjacent name/price line pairing (last resort)
    """
    cards: list[dict[str, Any]] = []

    # Strategy 1: LIST chunks with <li> containing name+price
    list_chunks = [c for c in chunks if c.chunk_type in (ChunkType.LIST, ChunkType.TABLE)]
    for chunk in list_chunks:
        # Split by <li> tags
        li_parts = re.split(r"<li[^>]*>", chunk.html, flags=re.IGNORECASE)
        for part in li_parts[1:]:  # skip pre-<li> content
            part_text = re.sub(r"<[^>]+>", " ", part)
            part_text = re.sub(r"\s+", " ", part_text).strip()
            if not part_text or len(part_text) < 5:
                continue
            price_m = _CARD_PRICE_RE.search(part_text)
            if price_m:
                # Everything before the price is likely the product name
                name_part = part_text[: price_m.start()].strip().rstrip("|·-–—")
                price_str = price_m.group(0)
                if name_part and len(name_part) > 2:
                    card: dict[str, Any] = {"name": name_part.strip(), "price_text": price_str}
                    cards.append(card)

    if cards:
        return cards

    # Strategy 2: parent_xpath grouping
    # Group non-meta chunks by parent_xpath
    groups: dict[str, list[HtmlChunk]] = {}
    for c in chunks:
        if c.chunk_type in (ChunkType.META, ChunkType.RSC_DATA):
            continue
        pxpath = c.parent_xpath or c.xpath
        groups.setdefault(pxpath, []).append(c)

    # Find groups that look like product listings (multiple children with prices)
    for _pxpath, group_chunks in groups.items():
        if len(group_chunks) < 2:
            continue
        texts = [c.text.strip() for c in group_chunks if c.text.strip()]
        # Count lines with prices
        price_lines = [t for t in texts if _CARD_PRICE_RE.search(t)]
        name_lines = [t for t in texts if not _CARD_PRICE_RE.search(t) and 3 < len(t) < 200]

        if len(price_lines) >= 2 and name_lines:
            # Pair names and prices by position
            for i, name in enumerate(name_lines):
                if i < len(price_lines):
                    price_m = _CARD_PRICE_RE.search(price_lines[i])
                    card = {"name": name, "price_text": price_m.group(0) if price_m else price_lines[i]}
                    cards.append(card)

    if cards:
        return cards

    # Strategy 3: Adjacent line pairing (fallback)
    all_texts = []
    for c in chunks:
        if c.chunk_type in (ChunkType.META, ChunkType.RSC_DATA):
            continue
        text = c.text.strip()
        if text:
            all_texts.append(text)

    i = 0
    while i < len(all_texts) - 1:
        line = all_texts[i]
        next_line = all_texts[i + 1]

        # Pattern: name line followed by price line
        if not _CARD_PRICE_RE.search(line) and 3 < len(line) < 200 and _CARD_PRICE_RE.search(next_line):
            price_m = _CARD_PRICE_RE.search(next_line)
            card = {"name": line, "price_text": price_m.group(0) if price_m else next_line}
            cards.append(card)
            i += 2
            continue

        # Pattern: single line with both name and price
        if _CARD_PRICE_RE.search(line) and len(line) > 15:
            price_m = _CARD_PRICE_RE.search(line)
            name_part = line[: price_m.start()].strip().rstrip("|·-–—") if price_m else ""
            if name_part and len(name_part) > 2:
                card = {"name": name_part, "price_text": price_m.group(0) if price_m else ""}
                cards.append(card)
        i += 1

    return cards


def _detect_product_cards(
    chunks: list[HtmlChunk] | None,
    metadata: dict | None,
    card_strategy_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Main entry point: detect product cards with cascade priority.

    Priority: JSON-LD ItemList > chunk-based detection > empty list.
    Deduplicates by (name_lower, price_text).

    If card_strategy_hint is provided, tries the hinted strategy first
    and skips the fallback cascade on success.
    """
    cards: list[dict[str, Any]] = []

    if card_strategy_hint == "json_ld_itemlist":
        # Optimistic: try metadata-based only
        cards = _detect_cards_from_metadata(metadata)
        if cards:
            # Skip chunk-based detection entirely
            pass
        else:
            # Hint failed — fall through to full cascade
            if chunks:
                cards = _detect_cards_from_chunks(chunks)
    else:
        # Full cascade (no hint or unknown hint)
        # Try JSON-LD first (highest confidence)
        cards = _detect_cards_from_metadata(metadata)

        # Fallback to chunk-based detection
        if not cards and chunks:
            cards = _detect_cards_from_chunks(chunks)

    # Deduplicate by (name.lower(), price)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for card in cards:
        name = card.get("name", "").lower().strip()
        price = card.get("price_text", "") or str(card.get("price", ""))
        key = (name, price)
        if key not in seen:
            seen.add(key)
            deduped.append(card)

    return deduped


def _serialize_cards(
    cards: list[dict[str, Any]],
    max_cards: int = 15,
    lc: LocaleConfig | None = None,
) -> str:
    """Serialize product cards into numbered lines.

    Format: "1. 상품명 | 가격 | 브랜드"
    """
    if lc is None:
        lc = get_locale(None)
    lines: list[str] = []
    for i, card in enumerate(cards[:max_cards], 1):
        parts = [card.get("name", "")]
        # Price
        price_text = card.get("price_text", "")
        if not price_text and card.get("price") is not None:
            from pagemap.preprocessing.normalize import format_price

            price_text = format_price(card["price"], card.get("currency", "KRW"))
        if price_text:
            parts.append(price_text)
        # Brand
        if card.get("brand"):
            parts.append(card["brand"])
        lines.append(f"{i}. {' | '.join(parts)}")

    if len(cards) > max_cards:
        lines.append(f"... {lc.overflow_template.format(n=len(cards) - max_cards)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pagination detection (from raw HTML — AOM filter removes <nav>)
# ---------------------------------------------------------------------------

_PAGE_PARAM_RE = re.compile(
    r'(?:href|action)=["\'][^"\']*[?&](?:page|p|pg|pn|pageNo|pageNum|currentPage)=(\d+)',
    re.IGNORECASE,
)
_TOTAL_COUNT_RE = re.compile(
    r"(?:"
    r"총\s*[\d,]+\s*건"
    r"|[\d,]+\s*(?:개의?\s*(?:상품|결과|검색결과|아이템|건))"
    r"|\d[\d,]*\s*(?:results?|items?|products?)"
    r"|(?:of|중)\s+[\d,]+"
    r"|\d+-\d+\s+of\s+[\d,]+"
    # ja
    r"|\d[\d,]*\s*件の商品"
    # fr
    r"|\d[\d,]*\s*résultats"
    r"|\d[\d,]*\s*produits"
    # de
    r"|\d[\d,]*\s*Ergebnisse"
    r"|\d[\d,]*\s*Produkte"
    r")",
    re.IGNORECASE,
)
# Build _HAS_NEXT_RE from i18n tuples
_next_terms = NEXT_BUTTON_TERMS + LOAD_MORE_TERMS
_HAS_NEXT_RE = re.compile(
    r"(?:"
    + "|".join(r">" + re.escape(t) + r"<" for t in _next_terms)
    + r"|aria-label=[\"'](?:"
    + "|".join(re.escape(t) for t in _next_terms)
    + r")[\"']"
    + r"|class=[\"'][^\"']*next[^\"']*[\"']"
    + r")",
    re.IGNORECASE,
)
# Build _HAS_PREV_RE from i18n tuples (mirrors _HAS_NEXT_RE)
_prev_terms = PREV_BUTTON_TERMS
_HAS_PREV_RE = re.compile(
    r"(?:"
    + "|".join(r">" + re.escape(t) + r"<" for t in _prev_terms)
    + r"|aria-label=[\"'](?:"
    + "|".join(re.escape(t) for t in _prev_terms)
    + r")[\"']"
    + r"|class=[\"'][^\"']*prev[^\"']*[\"']"
    + r")",
    re.IGNORECASE,
)
_CURRENT_PAGE_RE = re.compile(
    r"(?:"
    r"[Pp]age\s+(\d+)\s+(?:of|/)\s+(\d+)"
    r"|페이지\s*(\d+)\s*/\s*(\d+)"
    r"|(\d+)\s*/\s*(\d+)\s*페이지"
    r"|(\d+)\s*/\s*(\d+)\s*ページ"
    r"|[Ss]eite\s+(\d+)\s+(?:von|/)\s+(\d+)"
    r")",
)


def _extract_pagination_info(
    raw_html: str,
    lc: LocaleConfig | None = None,
    pagination_hint: str | None = None,
) -> str:
    """Extract pagination summary from raw HTML.

    Returns a single-line summary like:
    "페이지네이션: ~25페이지 | 총 500건 | 다음 있음"

    Returns empty string if no pagination info found.

    If pagination_hint is "none", returns empty immediately (known no-pagination domain).
    If pagination_hint is a param name (e.g. "page"), uses targeted regex only.
    """
    if pagination_hint == "none":
        return ""

    if lc is None:
        lc = get_locale(None)

    parts: list[str] = []

    # Max page from URL params (targeted if hint provided)
    if pagination_hint and pagination_hint != "none":
        # Targeted regex for a single known parameter
        _targeted_re = re.compile(
            rf'(?:href|action)=["\'][^"\']*[?&]{re.escape(pagination_hint)}=(\d+)',
            re.IGNORECASE,
        )
        try:
            page_numbers = [int(m) for m in _targeted_re.findall(raw_html)]
        except ValueError:
            page_numbers = []
    else:
        # Full scan of all known pagination parameters
        try:
            page_numbers = [int(m) for m in _PAGE_PARAM_RE.findall(raw_html)]
        except ValueError:
            page_numbers = []
    max_page = max(page_numbers) if page_numbers else 0

    # Current page / total pages from text
    current_page_m = _CURRENT_PAGE_RE.search(raw_html)
    if current_page_m:
        groups = current_page_m.groups()
        try:
            for i in range(0, len(groups), 2):
                if groups[i] is not None:
                    _current = int(groups[i])
                    if i + 1 < len(groups) and groups[i + 1] is not None:
                        total_pages = int(groups[i + 1])
                        max_page = max(max_page, total_pages)
                    break
        except (ValueError, IndexError) as exc:
            logger.warning("Failed to parse pagination numbers: %s", exc)

    if max_page > 1:
        parts.append(f"~{max_page}{lc.label_page_suffix}")

    # Total result count
    total_m = _TOTAL_COUNT_RE.search(raw_html)
    if total_m:
        parts.append(total_m.group(0).strip())

    # Has next page
    has_next = bool(_HAS_NEXT_RE.search(raw_html))
    if has_next:
        parts.append(lc.label_next_available)

    if not parts:
        return ""

    return f"{lc.label_pagination}: " + " | ".join(parts)


def extract_pagination_structured(raw_html: str, lc: LocaleConfig | None = None) -> dict:
    """Extract structured pagination info from raw HTML.

    Returns dict with detected keys only (empty dict if nothing found):
        current_page (int), total_pages (int), total_items (str),
        has_next (bool), has_prev (bool)
    """
    if lc is None:
        lc = get_locale(None)

    result: dict = {}

    # Current page / total pages from text
    current_page = 0
    total_pages = 0
    current_page_m = _CURRENT_PAGE_RE.search(raw_html)
    if current_page_m:
        groups = current_page_m.groups()
        try:
            for i in range(0, len(groups), 2):
                if groups[i] is not None:
                    current_page = int(groups[i])
                    if i + 1 < len(groups) and groups[i + 1] is not None:
                        total_pages = int(groups[i + 1])
                    break
        except (ValueError, IndexError) as exc:
            logger.warning("Failed to parse pagination numbers: %s", exc)

    # Max page from URL params
    try:
        page_numbers = [int(m) for m in _PAGE_PARAM_RE.findall(raw_html)]
    except ValueError:
        page_numbers = []
    if page_numbers:
        total_pages = max(total_pages, max(page_numbers))

    if current_page > 0:
        result["current_page"] = current_page
    if total_pages > 1:
        result["total_pages"] = total_pages

    # Total result count
    total_m = _TOTAL_COUNT_RE.search(raw_html)
    if total_m:
        result["total_items"] = total_m.group(0).strip()

    # Has next / prev
    if _HAS_NEXT_RE.search(raw_html):
        result["has_next"] = True
    if _HAS_PREV_RE.search(raw_html):
        result["has_prev"] = True

    return result


def _compress_for_product(
    pruned_html: str,
    max_tokens: int,
    metadata: dict | None = None,
    lc: LocaleConfig | None = None,
) -> str:
    """Aggressive compression for product detail pages.

    Phase 1: Use structured metadata (high confidence) if available.
    Phase 2: Regex fallback for fields not covered by metadata.
    """
    if lc is None:
        lc = get_locale(None)
    parts: list[str] = []
    used: set[str] = set()

    # -- Phase 1: structured metadata (high confidence) --
    if metadata:
        if metadata.get("name"):
            parts.append(f"{lc.label_title}: {metadata['name']}")
            used.add("title")
        if metadata.get("price") is not None:
            from pagemap.preprocessing.normalize import format_price

            currency = metadata.get("currency", "KRW")
            parts.append(format_price(metadata["price"], currency))
            used.add("price")
        if metadata.get("rating") is not None:
            rating_str = f"{lc.label_rating}: {metadata['rating']}"
            if metadata.get("review_count"):
                rating_str += " " + lc.review_template.format(count=metadata["review_count"])
            parts.append(rating_str)
            used.add("rating")
        if metadata.get("brand"):
            parts.append(f"{lc.label_brand}: {metadata['brand']}")

    # -- Phase 2: regex fallback (unfilled fields only) --
    lines = _extract_text_lines(pruned_html)
    sections: dict[str, list[str]] = {
        "title": [],
        "price": [],
        "rating": [],
        "options": [],
        "other": [],
    }

    for line in lines:
        if len(line) < 2:
            continue
        line_lower = line.lower()

        if "price" not in used and PRICE_PATTERN.search(line) and re.search(r"\d", line):
            if line not in sections["price"]:
                sections["price"].append(line)
        elif "rating" not in used and RATING_PATTERN.search(line):
            if line not in sections["rating"]:
                sections["rating"].append(line)
        elif any(kw in line_lower for kw in _option_kw):
            sections["options"].append(line)
        elif "title" not in used and not sections["title"] and 10 < len(line) < 200:
            sections["title"].append(line)
        else:
            sections["other"].append(line)

    # "원" post-processing -- only when price not from metadata
    if "price" not in used:
        for i, p in enumerate(sections["price"]):
            if re.match(r"^\d{2,3}(?:,\d{3})+$", p.strip()):
                sections["price"][i] = p.strip() + "원"

    # -- Phase 3: combine --
    if "title" not in used and sections["title"]:
        parts.append(f"{lc.label_title}: {sections['title'][0]}")
    if "price" not in used:
        for p in sections["price"][:5]:
            parts.append(p)
    if "rating" not in used:
        for r in sections["rating"][:2]:
            parts.append(r)
    for o in sections["options"][:5]:
        parts.append(o)
    for d in sections["other"][:3]:
        if len(d) > 15:
            parts.append(d[:200])

    result = "\n".join(parts)

    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)

    return result


def _compress_for_article(
    pruned_html: str,
    max_tokens: int,
    lc: LocaleConfig | None = None,
) -> str:
    """Compression for article/news pages: title + first paragraph + meta."""
    if lc is None:
        lc = get_locale(None)
    lines = _extract_text_lines(pruned_html)

    parts = []
    title_found = False
    para_count = 0
    max_paras = 2

    for line in lines:
        if len(line) < 3:
            continue
        # First substantial line is likely the title
        if not title_found and len(line) > 10:
            parts.append(f"{lc.label_title}: {line}")
            title_found = True
            continue
        # Date-like
        if re.search(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", line):
            parts.append(line)
            continue
        # Paragraphs
        if title_found and para_count < max_paras and len(line) > 30:
            parts.append(line[:300])
            para_count += 1

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_search_results(
    pruned_html: str,
    max_tokens: int,
    chunks: list[HtmlChunk] | None = None,
    metadata: dict | None = None,
    lc: LocaleConfig | None = None,
    card_strategy_hint: str | None = None,
) -> str:
    """Compression for search result pages.

    Card detection path: structured cards with name+price pairs.
    Fallback: legacy text-line extraction.
    """
    if lc is None:
        lc = get_locale(None)
    # Try card detection first
    cards = _detect_product_cards(chunks, metadata, card_strategy_hint=card_strategy_hint)
    if cards:
        return _build_card_output(pruned_html, cards, max_tokens, lc=lc)

    # Legacy fallback: text-line based extraction
    lines = _extract_text_lines(pruned_html)

    sections: dict[str, list[str]] = {
        "result_count": [],
        "products": [],
        "filters": [],
    }

    _search_kw = tuple(t.lower() for t in SEARCH_RESULT_TERMS)
    _filter_kw = tuple(t.lower() for t in FILTER_TERMS)

    for line in lines:
        if len(line) < 2:
            continue
        line_lower = line.lower()

        if any(kw in line_lower for kw in _search_kw):
            sections["result_count"].append(line)
        elif PRICE_PATTERN.search(line):
            sections["products"].append(line)
        elif any(kw in line_lower for kw in _filter_kw):
            sections["filters"].append(line[:100])

    parts = sections["result_count"][:2]
    parts.extend(p[:150] for p in sections["products"][:10])
    parts.extend(sections["filters"][:3])

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_listing(
    pruned_html: str,
    max_tokens: int,
    chunks: list[HtmlChunk] | None = None,
    metadata: dict | None = None,
    lc: LocaleConfig | None = None,
    card_strategy_hint: str | None = None,
) -> str:
    """Compression for listing/ranking pages.

    Card detection path: structured cards with name+price pairs.
    Fallback: legacy text-line extraction.
    """
    if lc is None:
        lc = get_locale(None)
    # Try card detection first
    cards = _detect_product_cards(chunks, metadata, card_strategy_hint=card_strategy_hint)
    if cards:
        return _build_card_output(pruned_html, cards, max_tokens, lc=lc)

    # Legacy fallback: text-line based extraction
    lines = _extract_text_lines(pruned_html)

    sections: dict[str, list[str]] = {
        "title": [],
        "products": [],
        "sort_filter": [],
    }

    _listing_kw = tuple(t.lower() for t in LISTING_TERMS)
    _filter_kw = tuple(t.lower() for t in FILTER_TERMS)

    for line in lines:
        if len(line) < 2:
            continue
        line_lower = line.lower()

        if any(kw in line_lower for kw in _listing_kw):
            sections["title"].append(line)
        elif PRICE_PATTERN.search(line):
            sections["products"].append(line)
        elif any(kw in line_lower for kw in _filter_kw):
            sections["sort_filter"].append(line[:100])

    parts = sections["title"][:2]
    parts.extend(p[:150] for p in sections["products"][:10])
    parts.extend(sections["sort_filter"][:3])

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _build_card_output(
    pruned_html: str,
    cards: list[dict[str, Any]],
    max_tokens: int,
    lc: LocaleConfig | None = None,
) -> str:
    """Build structured output from detected cards + page status header."""
    if lc is None:
        lc = get_locale(None)
    parts: list[str] = []

    _heading_kw = tuple(t.lower() for t in LISTING_TERMS + SEARCH_RESULT_TERMS)
    _count_kw = tuple(t.lower() for t in SEARCH_RESULT_TERMS + FILTER_TERMS)

    # Extract page status header from text
    lines = _extract_text_lines(pruned_html)
    for line in lines[:15]:  # only check early lines
        line_lower = line.lower()
        if any(kw in line_lower for kw in _heading_kw):
            parts.append(line[:150])
            break

    # Result count / sort info
    for line in lines[:20]:
        line_lower = line.lower()
        if any(kw in line_lower for kw in _count_kw):
            if line not in parts:
                parts.append(line[:150])
                if len(parts) >= 3:
                    break

    # Serialized cards
    parts.append(_serialize_cards(cards, lc=lc))

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _calibrate_chars_per_token(lines: list[str], min_len: int, max_line_len: int) -> float:
    """Calibrate chars/token ratio from a sample of lines.

    One tiktoken call on a joined sample. Used to convert token budget
    to char budget for O(1)-per-line accumulation loops.
    """
    sample_parts = []
    for line in lines:
        if len(line) < min_len:
            continue
        sample_parts.append(line[:max_line_len])
        if len(sample_parts) >= 20:
            break
    if not sample_parts:
        return 4.0  # English default
    sample = "\n".join(sample_parts)
    tok = count_tokens(sample)
    if tok == 0:
        return 4.0
    return max(len(sample) / tok, 1.5)  # floor 1.5: safe for extreme CJK


def _compress_default(pruned_html: str, max_tokens: int) -> str:
    """Default compression: headings + significant text blocks."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0
    for line in lines:
        if len(line) < 5:
            continue
        text = line[:300]
        cost = len(text) + 1
        if running_chars + cost > char_budget:
            break
        parts.append(text)
        running_chars += cost

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to fit within token budget."""
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated = enc.decode(tokens[:max_tokens])
    return truncated


_NO_TEMPLATE = object()  # sentinel: distinguishes "not passed" from "passed as None"


# ---------------------------------------------------------------------------
# Dispatch table for page-type-specific compression
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CompressorContext:
    """Bundled arguments for page-type-specific compression."""

    pruned_html: str
    max_tokens: int
    chunks: list[HtmlChunk] | None = None
    metadata: dict | None = None
    lc: LocaleConfig | None = None
    card_strategy_hint: str | None = None


def _compress_product_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_product(ctx.pruned_html, ctx.max_tokens, metadata=ctx.metadata, lc=ctx.lc)


def _compress_search_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_search_results(
        ctx.pruned_html,
        ctx.max_tokens,
        chunks=ctx.chunks,
        metadata=ctx.metadata,
        lc=ctx.lc,
        card_strategy_hint=ctx.card_strategy_hint,
    )


def _compress_listing_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_listing(
        ctx.pruned_html,
        ctx.max_tokens,
        chunks=ctx.chunks,
        metadata=ctx.metadata,
        lc=ctx.lc,
        card_strategy_hint=ctx.card_strategy_hint,
    )


def _compress_article_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_article(ctx.pruned_html, ctx.max_tokens, lc=ctx.lc)


def _compress_default_dispatch(ctx: CompressorContext) -> str:
    return _compress_default(ctx.pruned_html, ctx.max_tokens)


# ---------------------------------------------------------------------------
# P7.1: New page-type compressors
# ---------------------------------------------------------------------------


def _compress_for_login(pruned_html: str, max_tokens: int) -> str:
    """Login page: form fields, social login buttons, error messages, forgot password."""
    lines = _extract_text_lines(pruned_html)

    parts: list[str] = []

    # Error/validation messages (priority — no budget check)
    for line in lines:
        ll = line.lower()
        if any(kw in ll for kw in ("error", "invalid", "incorrect", "실패", "오류", "잘못", "エラー")):
            parts.append(f"[error] {line[:200]}")

    # Calibrate + account for existing parts
    cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)
    running_chars = sum(len(p) + 1 for p in parts)

    # Form field labels + social login
    for line in lines:
        ll = line.lower()
        if any(
            kw in ll
            for kw in (
                "email",
                "password",
                "username",
                "이메일",
                "비밀번호",
                "아이디",
                "remember",
                "forgot",
                "비밀번호 찾기",
                "소셜",
                "social",
                "google",
                "facebook",
                "apple",
                "kakao",
                "naver",
            )
        ):
            text = line[:200]
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_checkout(pruned_html: str, max_tokens: int) -> str:
    """Checkout: cart items + total, current step, payment methods, shipping."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=1, max_line_len=300)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0

    for line in lines:
        ll = line.lower()
        # Prioritize: totals, items, step info, payment, shipping
        if any(
            kw in ll
            for kw in (
                "total",
                "합계",
                "소계",
                "subtotal",
                "合計",
                "step",
                "단계",
                "ステップ",
                "payment",
                "결제",
                "お支払い",
                "card",
                "카드",
                "shipping",
                "배송",
                "配送",
                "address",
                "주소",
                "order",
                "주문",
                "注文",
            )
        ):
            text = line[:300]
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_form(pruned_html: str, max_tokens: int) -> str:
    """Form page: label+input pairs, required markers, validation errors, submit."""
    lines = _extract_text_lines(pruned_html)

    parts: list[str] = []

    # Errors first (no budget check)
    for line in lines:
        ll = line.lower()
        if any(kw in ll for kw in ("error", "invalid", "required", "필수", "오류", "必須", "エラー")):
            parts.append(f"[validation] {line[:200]}")

    # Calibrate + account for existing parts
    cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)
    running_chars = sum(len(p) + 1 for p in parts)

    # Labels and field-related text
    for line in lines:
        ll = line.lower()
        if any(
            kw in ll
            for kw in (
                "name",
                "email",
                "phone",
                "이름",
                "이메일",
                "전화",
                "연락처",
                "message",
                "메시지",
                "comment",
                "submit",
                "제출",
                "등록",
                "label",
                "field",
                "select",
                "choose",
                "선택",
            )
        ):
            text = line[:200]
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_dashboard(pruned_html: str, max_tokens: int) -> str:
    """Dashboard: key metrics, table summaries (header+row count), navigation."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0

    # Headings and metrics
    for line in lines:
        if len(line) < 5:
            continue
        ll = line.lower()
        # Short metric-like lines or headings
        if len(line) < 80 or any(
            kw in ll
            for kw in (
                "total",
                "합계",
                "count",
                "average",
                "revenue",
                "users",
                "views",
                "매출",
                "건수",
                "analytics",
                "metric",
            )
        ):
            text = line[:300]
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_help_faq(pruned_html: str, max_tokens: int) -> str:
    """FAQ/Help: Q&A list (numbered), category navigation."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0
    q_num = 0

    for line in lines:
        if len(line) < 5:
            continue
        # Question-like lines (short, often end with ?)
        if "?" in line or "？" in line or len(line) < 120:
            q_num += 1
            text = f"Q{q_num}. {line[:200]}"
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_settings(pruned_html: str, max_tokens: int) -> str:
    """Settings: section headings, field labels + current values, toggle states."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=3, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0

    for line in lines:
        if len(line) < 3:
            continue
        ll = line.lower()
        if any(
            kw in ll
            for kw in (
                "setting",
                "preference",
                "notification",
                "설정",
                "알림",
                "profile",
                "프로필",
                "account",
                "계정",
                "privacy",
                "개인정보",
                "language",
                "언어",
                "theme",
                "테마",
                "on",
                "off",
                "enable",
                "disable",
                "활성",
                "비활성",
            )
        ):
            text = line[:200]
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_error(pruned_html: str, max_tokens: int) -> str:
    """Error page: status code, error message, available actions."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=3, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0

    for line in lines:
        if len(line) < 3:
            continue
        text = line[:200]
        cost = len(text) + 1
        if running_chars + cost > char_budget:
            break
        parts.append(text)
        running_chars += cost

    result = "\n".join(parts) if parts else ""
    if not result:
        return _compress_default(pruned_html, max_tokens)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_documentation(pruned_html: str, max_tokens: int) -> str:
    """Documentation: heading outline, code blocks (first N lines), API signatures."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=3, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0

    for line in lines:
        if len(line) < 3:
            continue
        # Headings (short lines, likely headings)
        if len(line) < 80:
            text = line[:200]
        # Code-like lines
        elif any(
            kw in line for kw in ("def ", "function ", "class ", "import ", "const ", "export ", "->", "=>", "return ")
        ):
            text = f"  {line[:200]}"
        else:
            continue

        cost = len(text) + 1
        if running_chars + cost > char_budget:
            break
        parts.append(text)
        running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


def _compress_for_landing(pruned_html: str, max_tokens: int) -> str:
    """Landing page: hero text, CTA buttons, major section titles."""
    lines = _extract_text_lines(pruned_html)
    cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=200)
    char_budget = int(max_tokens * cpt * 0.95)

    parts: list[str] = []
    running_chars = 0

    for line in lines:
        if len(line) < 5:
            continue
        # Short lines are likely headings/CTAs; keep them
        if len(line) < 100:
            text = line[:200]
            cost = len(text) + 1
            if running_chars + cost > char_budget:
                break
            parts.append(text)
            running_chars += cost

    if not parts:
        return _compress_default(pruned_html, max_tokens)

    result = "\n".join(parts)
    if count_tokens(result) > max_tokens:
        result = _truncate_to_tokens(result, max_tokens)
    return result


# Dispatch wrappers for new types


def _compress_login_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_login(ctx.pruned_html, ctx.max_tokens)


def _compress_checkout_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_checkout(ctx.pruned_html, ctx.max_tokens)


def _compress_form_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_form(ctx.pruned_html, ctx.max_tokens)


def _compress_dashboard_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_dashboard(ctx.pruned_html, ctx.max_tokens)


def _compress_help_faq_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_help_faq(ctx.pruned_html, ctx.max_tokens)


def _compress_settings_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_settings(ctx.pruned_html, ctx.max_tokens)


def _compress_error_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_error(ctx.pruned_html, ctx.max_tokens)


def _compress_documentation_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_documentation(ctx.pruned_html, ctx.max_tokens)


def _compress_landing_dispatch(ctx: CompressorContext) -> str:
    return _compress_for_landing(ctx.pruned_html, ctx.max_tokens)


_COMPRESSORS: dict[str, Callable[[CompressorContext], str]] = {
    # Existing 5
    "product_detail": _compress_product_dispatch,
    "search_results": _compress_search_dispatch,
    "listing": _compress_listing_dispatch,
    "article": _compress_article_dispatch,
    "news": _compress_article_dispatch,
    # P7.1 new 9
    "login": _compress_login_dispatch,
    "checkout": _compress_checkout_dispatch,
    "form": _compress_form_dispatch,
    "dashboard": _compress_dashboard_dispatch,
    "help_faq": _compress_help_faq_dispatch,
    "settings": _compress_settings_dispatch,
    "error": _compress_error_dispatch,
    "documentation": _compress_documentation_dispatch,
    "landing": _compress_landing_dispatch,
}


def build_pruned_context(
    raw_html: str,
    page_type: str = "default",
    site_id: str = "unknown",
    page_id: str = "unknown",
    schema_name: str = "Product",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    locale: str | None = None,
    template: Any = _NO_TEMPLATE,
) -> tuple[str, int, dict]:
    """Build pruned context from raw HTML.

    Uses pruning2 pipeline first, then applies page-type-specific compression.

    Args:
        raw_html: full page HTML
        page_type: one of product_detail, search_results, article, default
        site_id: site identifier for pruning2
        page_id: page identifier for pruning2
        schema_name: schema name for pruning2 heuristics
        max_tokens: maximum token budget
        locale: locale code (e.g. "ko", "ja", "fr"). None → default ("ko").
        template: optional PageTemplate with structural hints for optimization

    Returns:
        (pruned_context_text, token_count, metadata_dict)
    """
    import time as _time

    lc = get_locale(locale)

    # Extract hints from template (if available)
    _template_caching_active = template is not _NO_TEMPLATE
    _source_hint: str | None = None
    _card_hint: str | None = None
    _pag_hint: str | None = None
    if template is not None and template is not _NO_TEMPLATE:
        _source_hint = template.data.metadata_source or None
        _card_hint = template.data.card_strategy
        _pag_hint = template.data.pagination_param
        if not template.data.has_pagination:
            _pag_hint = "none"

    # Schema refinement: Generic → detect from JSON-LD in raw HTML
    if schema_name == "Generic":
        from pagemap.page_map_builder import _detect_schema_from_jsonld

        detected = _detect_schema_from_jsonld(raw_html)
        if detected is not None:
            logger.info("Dynamic schema: Generic -> %s", detected)
            schema_name = detected

    # Step 1: Run pruning2 pipeline
    t0 = _time.monotonic()
    metadata: dict = {}
    selected_chunks: list[HtmlChunk] = []
    result = None
    _pruning_exception: Exception | None = None
    try:
        result = prune_page(raw_html, site_id, page_id, schema_name)
        pruned_html = result.pruned_html
        selected_chunks = result.selected_chunks
        logger.info(
            "pruning2: %d → %d tokens (%.1f%% reduction)",
            result.raw_token_count,
            result.pruned_token_count,
            result.token_reduction_pct,
        )

        # Step 2: Structured metadata extraction
        t1 = _time.monotonic()
        try:
            from pagemap.metadata import extract_metadata

            metadata = extract_metadata(
                result.meta_chunks,
                result.heading_chunks,
                schema_name,
                source_hint=_source_hint,
            )
            if metadata:
                logger.info("Structured metadata: %s", list(metadata.keys()))
        except Exception as e:
            logger.warning("Metadata extraction failed, using heuristic: %s", e)
        t2 = _time.monotonic()

    except Exception as e:
        logger.warning("pruning2 failed, using raw HTML: %s", e)
        _pruning_exception = e
        pruned_html = raw_html
        t1 = t2 = _time.monotonic()

    # Phase 4.4: Propagate pruning failures to agent warnings
    if _pruning_exception is not None or (result is not None and result.errors):
        metadata["_pruning_warnings"] = [
            "Page content extraction encountered issues; displayed content may be incomplete"
        ]

    # Phase 4.1: Expose pruned regions for context coherence annotation
    if result is not None:
        from pagemap.pruning.aom_filter import derive_pruned_regions

        metadata["_pruned_regions"] = derive_pruned_regions(result.aom_filter_stats)

    # Expose PruningResult for template learning (popped by caller in page_map_builder)
    if _template_caching_active and result is not None:
        metadata["_pruning_result"] = result

    # Step 3: Apply page-type-specific aggressive compression (dispatch table)
    ctx = CompressorContext(
        pruned_html=pruned_html,
        max_tokens=max_tokens,
        chunks=selected_chunks,
        metadata=metadata,
        lc=lc,
        card_strategy_hint=_card_hint,
    )
    compressor = _COMPRESSORS.get(page_type, _compress_default_dispatch)
    context = compressor(ctx)
    t3 = _time.monotonic()

    # Append pagination info for listing/search pages
    if page_type in ("listing", "search_results"):
        pagination = _extract_pagination_info(raw_html, lc=lc, pagination_hint=_pag_hint)
        if pagination:
            context = context.rstrip() + "\n" + pagination

    token_count = count_tokens(context)
    logger.info(
        "pruned_context: %d tokens (budget: %d) prune=%.0fms meta=%.0fms compress=%.0fms template=%s",
        token_count,
        max_tokens,
        (t1 - t0) * 1000,
        (t2 - t1) * 1000,
        (t3 - t2) * 1000,
        "hit" if (template is not None and template is not _NO_TEMPLATE) else "miss",
    )
    return context, token_count, metadata


def build_pruned_context_from_snapshot(
    snapshot: Any,
    page_type: str = "default",
    schema_name: str = "Product",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    locale: str | None = None,
) -> tuple[str, int, dict]:
    """Build pruned context from a PageSnapshot object (offline mode).

    Args:
        snapshot: PageSnapshot from collect.py
        page_type: page type classification
        schema_name: schema for pruning heuristics
        max_tokens: token budget
        locale: locale code (e.g. "ko", "ja"). None → default ("ko").

    Returns:
        (pruned_context_text, token_count, metadata_dict)
    """
    return build_pruned_context(
        raw_html=snapshot.html_raw,
        page_type=page_type,
        site_id=snapshot.site_id,
        page_id=snapshot.page_id,
        schema_name=schema_name,
        max_tokens=max_tokens,
        locale=locale,
    )
