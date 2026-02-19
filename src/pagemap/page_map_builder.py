"""PageMap orchestrator: combines interactive detection + pruned context.

Two modes:
- Live mode: navigates to URL, captures AX tree + HTML → PageMap
- Offline mode: loads snapshot HTML, runs detection + pruning → PageMap
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from pagemap.preprocessing.preprocess import count_tokens

from . import Interactable, PageMap
from .browser_session import BrowserSession
from .i18n import detect_locale
from .interactive_detector import detect_all
from .pruned_context_builder import (
    build_pruned_context,
    extract_product_images,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_PRUNED_CONTEXT_TOKENS = 1500
DEFAULT_TOTAL_BUDGET_TOKENS = 5000

# Page type detection heuristics
PAGE_TYPE_PATTERNS: dict[str, list[str]] = {
    "product_detail": [
        "/vp/products/",
        "/products/",
        "/goods/",
        "/catalog/",
        "/item/",
        "/product/",
        "/product.",  # COS (e.g. /women/denim-edit/product.facade-...)
        "/dp/",
        "/Product/",  # W Concept
        "/t/",  # Nike
        "/productDetail",  # Handsome
        "/good",  # SSF (/good, /goods)
    ],
    "search_results": [
        "/search",
        "?q=",
        "?query=",
        "?keyword=",
        "/browse",
        "?searchTerm=",  # Zara
        "/w?q=",  # Nike
    ],
    "article": [
        "/article/",
        "/articles/",
        "/news/",
        "/wiki/",
        "/blog/",
        "/post/",
    ],
    "listing": [
        "/list",
        "/ranking",
        "/best",
        "/category/",
        "/w/",  # Nike categories
        "/men/",
        "/women/",  # Global fashion categories
        "/man/",
        "/woman/",
        "/men.",
        "/women.",
    ],
}

# Domain → schema name mapping
DOMAIN_SCHEMA_MAP: dict[str, str] = {
    "coupang.com": "Product",
    "musinsa.com": "Product",
    "29cm.co.kr": "Product",
    "kurly.com": "Product",
    # Phase 1: Fashion e-commerce
    "wconcept.co.kr": "Product",
    "ssfshop.com": "Product",
    "thehandsome.com": "Product",
    "zara.com": "Product",
    "cos.com": "Product",
    "hm.com": "Product",
    "uniqlo.com": "Product",
    "nike.com": "Product",
    # Non-ecommerce
    "news.naver.com": "NewsArticle",
    "bbc.com": "NewsArticle",
    "ko.wikipedia.org": "WikiArticle",
    "github.com": "SaaSPage",
    "gov.kr": "GovernmentPage",
}


def detect_page_type(url: str) -> str:
    """Detect page type from URL patterns."""
    url_lower = url.lower()
    for page_type, patterns in PAGE_TYPE_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return page_type
    return "unknown"


def detect_schema(url: str) -> str:
    """Detect schema name from URL domain."""
    for domain, schema in DOMAIN_SCHEMA_MAP.items():
        if domain in url:
            return schema
    return "Product"


def _extract_site_id(url: str) -> str:
    """Extract site_id from URL domain."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    # e.g. www.coupang.com → coupang
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return host


async def build_page_map_live(
    session: BrowserSession,
    url: str | None = None,
    enable_tier3: bool = True,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
) -> PageMap:
    """Build a PageMap from a live browser session.

    If url is provided, navigates to it first.
    Otherwise uses the current page.

    Args:
        session: active BrowserSession
        url: optional URL to navigate to
        enable_tier3: enable CDP-based Tier 3 detection
        max_pruned_tokens: token budget for pruned_context

    Returns:
        Complete PageMap
    """
    start = time.monotonic()

    if url:
        await session.navigate(url)

    page_url = await session.get_page_url()
    page_title = await session.get_page_title()
    page_type = detect_page_type(page_url)
    schema = detect_schema(page_url)
    site_id = _extract_site_id(page_url)

    # Detect interactables (isolated: failure yields empty list + warning)
    warnings: list[str] = []
    try:
        interactables, detect_warnings = await detect_all(session.page, enable_tier3=enable_tier3)
        warnings.extend(detect_warnings)
    except Exception as e:
        logger.error("Interactive detection completely failed: %s", e)
        interactables = []
        warnings.append(f"Interactive element detection failed ({type(e).__name__}): only page content is available")

    raw_html = await session.get_page_html()

    # Build pruned context with auto-detected locale
    locale = detect_locale(page_url)
    pruned_context, pruned_tokens, metadata = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id="live",
        schema_name=schema,
        max_tokens=max_pruned_tokens,
        locale=locale,
    )

    # Extract product images
    images = extract_product_images(raw_html, page_url)

    # Budget-aware filtering
    interactables = _budget_filter_interactables(interactables, pruned_tokens)

    elapsed_ms = (time.monotonic() - start) * 1000

    page_map = PageMap(
        url=page_url,
        title=page_title,
        page_type=page_type,
        interactables=interactables,
        pruned_context=pruned_context,
        pruned_tokens=pruned_tokens,
        generation_ms=elapsed_ms,
        images=images,
        metadata=metadata,
        warnings=warnings,
    )

    total_tokens = _estimate_total_tokens(page_map)
    logger.info(
        "PageMap built: %d interactables, %d pruned tokens, %d total tokens, %d images, %.0fms",
        len(interactables),
        pruned_tokens,
        total_tokens,
        len(images),
        elapsed_ms,
    )
    return page_map


def _extract_interactables_from_html(raw_html: str) -> list[Interactable]:
    """Extract interactive elements from raw HTML via static parsing.

    Lightweight fallback for offline mode (no browser/AX tree).
    Detects buttons, links with CTA text, inputs, and selects.
    """
    import re

    interactables: list[Interactable] = []
    ref = 1

    # CTA keywords for filtering links (only keep action-oriented links)
    _CTA_RE = re.compile(
        r"(장바구니|카트|구매|구입|주문|담기|바로구매"
        r"|add.to.(?:cart|bag|basket)|buy.now|purchase|checkout|order"
        r"|size.guide|사이즈\s*가이드"
        r"|wishlist|위시리스트|찜)",
        re.IGNORECASE,
    )

    # --- Buttons ---
    for m in re.finditer(
        r"<button\b([^>]*)>(.*?)</button>",
        raw_html,
        re.IGNORECASE | re.DOTALL,
    ):
        attrs_str, inner = m.group(1), m.group(2)
        # Skip hidden/disabled
        if re.search(r'(?:type=["\']hidden|disabled\b|style=["\'][^"\']*display:\s*none)', attrs_str, re.IGNORECASE):
            continue
        # Extract name from aria-label, title, or inner text
        name = ""
        aria_m = re.search(r'aria-label=["\']([^"\']+)["\']', attrs_str, re.IGNORECASE)
        if aria_m:
            name = aria_m.group(1).strip()
        if not name:
            name = re.sub(r"<[^>]+>", " ", inner).strip()
            name = re.sub(r"\s+", " ", name)
        if not name or len(name) > 100:
            continue
        interactables.append(
            Interactable(
                ref=ref,
                role="button",
                name=name,
                affordance="click",
                region="main",
                tier=2,
            )
        )
        ref += 1

    # --- Links with CTA text ---
    for m in re.finditer(
        r"<a\b([^>]*)>(.*?)</a>",
        raw_html,
        re.IGNORECASE | re.DOTALL,
    ):
        attrs_str, inner = m.group(1), m.group(2)
        name = ""
        aria_m = re.search(r'aria-label=["\']([^"\']+)["\']', attrs_str, re.IGNORECASE)
        if aria_m:
            name = aria_m.group(1).strip()
        if not name:
            name = re.sub(r"<[^>]+>", " ", inner).strip()
            name = re.sub(r"\s+", " ", name)
        if not name or len(name) > 100:
            continue
        # Only keep CTA-like links
        if _CTA_RE.search(name) or _CTA_RE.search(attrs_str):
            interactables.append(
                Interactable(
                    ref=ref,
                    role="link",
                    name=name,
                    affordance="click",
                    region="main",
                    tier=2,
                )
            )
            ref += 1

    # --- Inputs (search, text) ---
    for m in re.finditer(r"<input\b([^>]*)>", raw_html, re.IGNORECASE):
        attrs_str = m.group(1)
        type_m = re.search(r'type=["\'](\w+)["\']', attrs_str, re.IGNORECASE)
        input_type = type_m.group(1).lower() if type_m else "text"
        if input_type in ("hidden", "submit", "image", "reset"):
            continue
        name = ""
        for attr in ("aria-label", "placeholder", "name", "title"):
            attr_m = re.search(rf'{attr}=["\']([^"\']+)["\']', attrs_str, re.IGNORECASE)
            if attr_m:
                name = attr_m.group(1).strip()
                break
        role = "searchbox" if input_type == "search" or "search" in name.lower() else "textbox"
        if not name:
            name = input_type
        interactables.append(
            Interactable(
                ref=ref,
                role=role,
                name=name,
                affordance="type",
                region="main",
                tier=2,
            )
        )
        ref += 1

    # --- Selects ---
    for m in re.finditer(
        r"<select\b([^>]*)>(.*?)</select>",
        raw_html,
        re.IGNORECASE | re.DOTALL,
    ):
        attrs_str, inner = m.group(1), m.group(2)
        name = ""
        for attr in ("aria-label", "name", "id", "title"):
            attr_m = re.search(rf'{attr}=["\']([^"\']+)["\']', attrs_str, re.IGNORECASE)
            if attr_m:
                name = attr_m.group(1).strip()
                break
        options = re.findall(r"<option[^>]*>(.*?)</option>", inner, re.IGNORECASE | re.DOTALL)
        options = [re.sub(r"<[^>]+>", "", o).strip() for o in options if o.strip()]
        interactables.append(
            Interactable(
                ref=ref,
                role="combobox",
                name=name or "select",
                affordance="select",
                region="main",
                tier=2,
                options=options[:10],
            )
        )
        ref += 1

    # Deduplicate by (role, name)
    seen: set[tuple[str, str]] = set()
    deduped: list[Interactable] = []
    for el in interactables:
        key = (el.role, el.name.lower())
        if key not in seen:
            seen.add(key)
            deduped.append(el)

    # Renumber
    for i, el in enumerate(deduped, 1):
        el.ref = i

    return deduped


def build_page_map_offline(
    raw_html: str,
    url: str = "offline://unknown",
    site_id: str = "unknown",
    page_id: str = "page_000",
    page_type: str | None = None,
    schema_name: str | None = None,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
) -> PageMap:
    """Build a PageMap from offline HTML (no browser, no AX tree).

    In offline mode, interactive elements are extracted via static HTML
    parsing (buttons, CTA links, inputs, selects). This is less accurate
    than live AX tree detection but covers common e-commerce UI patterns.

    Args:
        raw_html: full page HTML
        url: original URL
        site_id: site identifier
        page_id: page identifier
        page_type: override page type detection
        schema_name: override schema detection
        max_pruned_tokens: token budget for pruned_context

    Returns:
        PageMap with statically-extracted interactables
    """
    start = time.monotonic()

    if page_type is None:
        page_type = detect_page_type(url)
    if schema_name is None:
        schema_name = detect_schema(url)

    # Extract title from HTML
    import re

    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    locale = detect_locale(url)
    pruned_context, pruned_tokens, metadata = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id=page_id,
        schema_name=schema_name,
        max_tokens=max_pruned_tokens,
        locale=locale,
    )

    # Extract interactables from HTML (static parsing)
    interactables = _extract_interactables_from_html(raw_html)

    # Extract product images
    images = extract_product_images(raw_html, url)

    # Budget-aware filtering
    interactables = _budget_filter_interactables(interactables, pruned_tokens)

    elapsed_ms = (time.monotonic() - start) * 1000

    page_map = PageMap(
        url=url,
        title=title,
        page_type=page_type,
        interactables=interactables,
        pruned_context=pruned_context,
        pruned_tokens=pruned_tokens,
        generation_ms=elapsed_ms,
        images=images,
        metadata=metadata,
    )

    logger.info(
        "PageMap (offline %s/%s): %d interactables, %d pruned tokens, %.0fms",
        site_id,
        page_id,
        len(interactables),
        pruned_tokens,
        elapsed_ms,
    )
    return page_map


async def build_page_map_from_snapshot(
    session: BrowserSession,
    snapshot_dir: Path,
    enable_tier3: bool = False,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
) -> PageMap:
    """Build a PageMap by loading a snapshot into a browser session.

    Loads raw.html into the browser for AX tree capture, then builds
    pruned context from the same HTML.

    Args:
        session: active BrowserSession
        snapshot_dir: path to snapshot directory containing raw.html, snapshot.json
        enable_tier3: enable Tier 3 CDP detection (usually False for offline)
        max_pruned_tokens: token budget

    Returns:
        PageMap with interactables from AX tree + pruned context
    """
    import json

    start = time.monotonic()

    raw_html_path = snapshot_dir / "raw.html"
    meta_path = snapshot_dir / "snapshot.json"

    raw_html = raw_html_path.read_text(encoding="utf-8")

    # Load metadata
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    url = meta.get("url", f"file://{snapshot_dir}")
    site_id = meta.get("site_id", snapshot_dir.parent.name)
    page_id = meta.get("page_id", snapshot_dir.name)

    page_type = detect_page_type(url)
    schema_name = detect_schema(url)

    # Load HTML into browser for AX tree
    await session.load_html(raw_html)

    # Detect interactables from loaded page (isolated: failure yields empty list + warning)
    warnings: list[str] = []
    try:
        interactables, detect_warnings = await detect_all(session.page, enable_tier3=enable_tier3)
        warnings.extend(detect_warnings)
    except Exception as e:
        logger.error("Interactive detection completely failed: %s", e)
        interactables = []
        warnings.append(f"Interactive element detection failed ({type(e).__name__}): only page content is available")

    # Build pruned context with auto-detected locale
    locale = detect_locale(url)
    pruned_context, pruned_tokens, structured_meta = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id=page_id,
        schema_name=schema_name,
        max_tokens=max_pruned_tokens,
        locale=locale,
    )

    # Title from metadata or HTML
    import re

    title = meta.get("title", "")
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

    # Extract product images
    images = extract_product_images(raw_html, url)

    # Budget-aware filtering
    interactables = _budget_filter_interactables(interactables, pruned_tokens)

    elapsed_ms = (time.monotonic() - start) * 1000

    page_map = PageMap(
        url=url,
        title=title,
        page_type=page_type,
        interactables=interactables,
        pruned_context=pruned_context,
        pruned_tokens=pruned_tokens,
        generation_ms=elapsed_ms,
        images=images,
        metadata=structured_meta,
        warnings=warnings,
    )

    total_tokens = _estimate_total_tokens(page_map)
    logger.info(
        "PageMap (snapshot %s/%s): %d interactables, %d pruned tokens, %d total, %.0fms",
        site_id,
        page_id,
        len(interactables),
        pruned_tokens,
        total_tokens,
        elapsed_ms,
    )
    return page_map


def _estimate_total_tokens(page_map: PageMap) -> int:
    """Estimate total token count for a PageMap (interactables + pruned_context)."""
    # Estimate interactables tokens
    interactable_text = "\n".join(str(i) for i in page_map.interactables)
    interactable_tokens = count_tokens(interactable_text) if interactable_text else 0
    return interactable_tokens + page_map.pruned_tokens


def _budget_filter_interactables(
    interactables: list[Interactable],
    pruned_tokens: int,
    total_budget: int = DEFAULT_TOTAL_BUDGET_TOKENS,
) -> list[Interactable]:
    """Filter interactables to fit within the total token budget.

    Priority order:
    1. Key input elements: searchbox, textbox, combobox, checkbox, radio, switch
    2. Named buttons in header/navigation/search regions
    3. Tier 1 elements (well-labeled) by region priority
    4. Remaining elements until budget is exhausted

    Args:
        interactables: full list of detected interactables
        pruned_tokens: tokens used by pruned_context
        total_budget: total token budget for the entire PageMap prompt

    Returns:
        Filtered list fitting within budget, renumbered sequentially
    """
    if not interactables:
        return interactables

    # Reserve tokens: header (~50) + meta (~30) + pruned_context
    overhead = 80
    available = total_budget - pruned_tokens - overhead
    if available < 100:
        available = 100

    # Priority buckets
    INPUT_ROLES = {"searchbox", "textbox", "combobox", "checkbox", "radio", "switch", "slider"}
    HIGH_REGIONS = {"header", "navigation", "search"}

    bucket_input: list[Interactable] = []
    bucket_high_region: list[Interactable] = []
    bucket_tier1_main: list[Interactable] = []
    bucket_rest: list[Interactable] = []

    for el in interactables:
        if el.role in INPUT_ROLES:
            bucket_input.append(el)
        elif el.region in HIGH_REGIONS and el.name:
            bucket_high_region.append(el)
        elif el.tier == 1 and el.region == "main":
            bucket_tier1_main.append(el)
        else:
            bucket_rest.append(el)

    # Greedily add from priority buckets
    selected: list[Interactable] = []
    current_tokens = 0

    for bucket in [bucket_input, bucket_high_region, bucket_tier1_main, bucket_rest]:
        for el in bucket:
            el_tokens = count_tokens(str(el))
            if current_tokens + el_tokens > available:
                break
            selected.append(el)
            current_tokens += el_tokens

    # Re-sort by original ref order and renumber
    selected.sort(key=lambda e: e.ref)
    for i, el in enumerate(selected, 1):
        el.ref = i

    if len(selected) < len(interactables):
        logger.info(
            "Budget filter: %d → %d interactables (%d tokens available)", len(interactables), len(selected), available
        )

    return selected
