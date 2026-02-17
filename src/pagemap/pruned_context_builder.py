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
from typing import Any
from urllib.parse import urljoin

from pagemap.i18n import (
    FILTER_TERMS,
    LISTING_TERMS,
    LOAD_MORE_TERMS,
    NEXT_BUTTON_TERMS,
    OPTION_TERMS,
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
            if not url or url.startswith("data:"):
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
    # Extract heading content
    re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", cleaned, re.DOTALL | re.IGNORECASE)
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
) -> list[dict[str, Any]]:
    """Main entry point: detect product cards with cascade priority.

    Priority: JSON-LD ItemList > chunk-based detection > empty list.
    Deduplicates by (name_lower, price_text).
    """
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
_CURRENT_PAGE_RE = re.compile(
    r"(?:"
    r"[Pp]age\s+(\d+)\s+(?:of|/)\s+(\d+)"
    r"|페이지\s*(\d+)\s*/\s*(\d+)"
    r"|(\d+)\s*/\s*(\d+)\s*페이지"
    r"|(\d+)\s*/\s*(\d+)\s*ページ"
    r"|[Ss]eite\s+(\d+)\s+(?:von|/)\s+(\d+)"
    r")",
)


def _extract_pagination_info(raw_html: str, lc: LocaleConfig | None = None) -> str:
    """Extract pagination summary from raw HTML.

    Returns a single-line summary like:
    "페이지네이션: ~25페이지 | 총 500건 | 다음 있음"

    Returns empty string if no pagination info found.
    """
    if lc is None:
        lc = get_locale(None)

    parts: list[str] = []

    # Max page from URL params
    page_numbers = [int(m) for m in _PAGE_PARAM_RE.findall(raw_html)]
    max_page = max(page_numbers) if page_numbers else 0

    # Current page / total pages from text
    current_page_m = _CURRENT_PAGE_RE.search(raw_html)
    if current_page_m:
        groups = current_page_m.groups()
        # Find the first non-None pair
        for i in range(0, len(groups), 2):
            if groups[i] is not None:
                _current = int(groups[i])
                total_pages = int(groups[i + 1])
                max_page = max(max_page, total_pages)
                break

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
) -> str:
    """Compression for search result pages.

    Card detection path: structured cards with name+price pairs.
    Fallback: legacy text-line extraction.
    """
    if lc is None:
        lc = get_locale(None)
    # Try card detection first
    cards = _detect_product_cards(chunks, metadata)
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
) -> str:
    """Compression for listing/ranking pages.

    Card detection path: structured cards with name+price pairs.
    Fallback: legacy text-line extraction.
    """
    if lc is None:
        lc = get_locale(None)
    # Try card detection first
    cards = _detect_product_cards(chunks, metadata)
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


def _compress_default(pruned_html: str, max_tokens: int) -> str:
    """Default compression: headings + significant text blocks."""
    lines = _extract_text_lines(pruned_html)

    parts = []
    for line in lines:
        if len(line) < 5:
            continue
        parts.append(line[:300])
        if count_tokens("\n".join(parts)) > max_tokens:
            parts.pop()
            break

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


def build_pruned_context(
    raw_html: str,
    page_type: str = "default",
    site_id: str = "unknown",
    page_id: str = "unknown",
    schema_name: str = "Product",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    locale: str | None = None,
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

    Returns:
        (pruned_context_text, token_count, metadata_dict)
    """
    lc = get_locale(locale)

    # Step 1: Run pruning2 pipeline
    metadata: dict = {}
    selected_chunks: list[HtmlChunk] = []
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
        try:
            from pagemap.metadata import extract_metadata

            metadata = extract_metadata(result.meta_chunks, result.heading_chunks, schema_name)
            if metadata:
                logger.info("Structured metadata: %s", list(metadata.keys()))
        except Exception as e:
            logger.warning("Metadata extraction failed, using heuristic: %s", e)

    except Exception as e:
        logger.warning("pruning2 failed, using raw HTML: %s", e)
        pruned_html = raw_html

    # Step 3: Apply page-type-specific aggressive compression
    if page_type == "product_detail":
        context = _compress_for_product(pruned_html, max_tokens, metadata=metadata, lc=lc)
    elif page_type == "search_results":
        context = _compress_for_search_results(
            pruned_html, max_tokens, chunks=selected_chunks, metadata=metadata, lc=lc
        )
    elif page_type == "listing":
        context = _compress_for_listing(pruned_html, max_tokens, chunks=selected_chunks, metadata=metadata, lc=lc)
    elif page_type in ("article", "news"):
        context = _compress_for_article(pruned_html, max_tokens, lc=lc)
    else:
        context = _compress_default(pruned_html, max_tokens)

    # Append pagination info for listing/search pages
    if page_type in ("listing", "search_results"):
        pagination = _extract_pagination_info(raw_html, lc=lc)
        if pagination:
            context = context.rstrip() + "\n" + pagination

    token_count = count_tokens(context)
    logger.info("pruned_context: %d tokens (budget: %d)", token_count, max_tokens)
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
