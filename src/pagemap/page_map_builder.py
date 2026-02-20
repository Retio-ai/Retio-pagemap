# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""PageMap orchestrator: combines interactive detection + pruned context.

Two modes:
- Live mode: navigates to URL, captures AX tree + HTML → PageMap
- Offline mode: loads snapshot HTML, runs detection + pruning → PageMap
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from pagemap.preprocessing.preprocess import count_tokens

from . import Interactable, PageMap
from .browser_session import BrowserSession
from .i18n import (
    LOAD_MORE_TERMS,
    NEXT_BUTTON_TERMS,
    PREV_BUTTON_TERMS,
    detect_locale,
)
from .interactive_detector import detect_all
from .pruned_context_builder import (
    build_pruned_context,
    extract_pagination_structured,
    extract_product_images,
)
from .template_cache import (
    InMemoryTemplateCache,
    PageTemplate,
    TemplateKey,
    extract_template_domain,
    infer_metadata_source,
    learn_template,
    validate_template,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_PRUNED_CONTEXT_TOKENS = 1500
DEFAULT_TOTAL_BUDGET_TOKENS = 5000

# ── CJK Token Budget ─────────────────────────────────────────────────

_CJK_TOKEN_MULTIPLIERS: dict[str, float] = {
    "ko": 1.8,  # Hangul: ~0.61 chars/token, 9.4x penalty
    "ja": 1.5,  # Mixed kana+kanji: ~1.0 chars/token avg
    "en": 1.0,
    "fr": 1.0,
    "de": 1.0,
}
_CJK_DEFAULT_MULTIPLIER = 1.0

# CJK Unicode ranges (CJK Unified + Hangul Syllables + Compat + Fullwidth)
_CJK_RE = re.compile(r"[\u3000-\u9fff\uac00-\ud7af\uf900-\ufaff\uff01-\uff60]")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

_CJK_OVERRIDE_THRESHOLD = 0.3  # en locale + high CJK → boost multiplier
_CJK_SUPPRESS_THRESHOLD = 0.1  # ko locale + low CJK → reduce multiplier
_CJK_MIN_SAMPLE_CHARS = 50  # below this → skip content detection
_MULTIPLIER_CEILING = 2.5


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """Computed token budgets with CJK compensation."""

    pruned_context: int
    total: int
    multiplier: float
    locale: str
    cjk_ratio: float


def _sample_visible_text(raw_html: str) -> str:
    """Extract visible text sample from HTML body for CJK ratio detection.

    Skips <head>, strips script/style/noscript content, then remaining tags.
    """
    # Jump to <body> to skip <head> content (meta, scripts, JSON-LD)
    body_idx = raw_html.lower().find("<body")
    start = body_idx if body_idx >= 0 else 0
    html_slice = raw_html[start : start + 30000]

    # Strip script/style/noscript CONTENT (not just tags)
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html_slice)
    # Strip remaining HTML tags
    text = _TAG_RE.sub(" ", cleaned)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:2000]


def compute_token_budget(
    locale: str,
    raw_html: str | None = None,
    base_pruned: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
    base_total: int = DEFAULT_TOTAL_BUDGET_TOKENS,
) -> TokenBudget:
    """Compute CJK-compensated token budgets.

    Pure function. Locale as primary signal, content-based refinement
    when raw_html is available.
    """
    multiplier = _CJK_TOKEN_MULTIPLIERS.get(locale, _CJK_DEFAULT_MULTIPLIER)
    cjk_ratio = 0.0

    if raw_html:
        sample = _sample_visible_text(raw_html)
        if len(sample) >= _CJK_MIN_SAMPLE_CHARS:
            cjk_chars = len(_CJK_RE.findall(sample))
            cjk_ratio = cjk_chars / len(sample)

            if multiplier <= 1.0 and cjk_ratio > _CJK_OVERRIDE_THRESHOLD:
                # Non-CJK locale but CJK content (e.g., .com with Korean text)
                ko_mult = _CJK_TOKEN_MULTIPLIERS["ko"]
                scale = min(cjk_ratio / 0.7, 1.0)  # linear ramp 0.3→0.7
                multiplier = 1.0 + (ko_mult - 1.0) * scale
            elif multiplier > 1.0 and cjk_ratio < _CJK_SUPPRESS_THRESHOLD:
                # CJK locale but non-CJK content (e.g., .kr with English text)
                multiplier = 1.0 + (multiplier - 1.0) * (cjk_ratio / _CJK_SUPPRESS_THRESHOLD)

    multiplier = max(1.0, min(_MULTIPLIER_CEILING, multiplier))

    return TokenBudget(
        pruned_context=round(base_pruned * multiplier),
        total=round(base_total * multiplier),
        multiplier=round(multiplier, 3),
        locale=locale,
        cjk_ratio=round(cjk_ratio, 3),
    )


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


async def _detect_all_safe(
    page,
    enable_tier3: bool,
) -> tuple[list[Interactable], list[str]]:
    """detect_all with error isolation — never raises on detection failure."""
    try:
        return await detect_all(page, enable_tier3=enable_tier3)
    except Exception as e:
        logger.error("Interactive detection completely failed: %s", e)
        return [], [f"Interactive element detection failed ({type(e).__name__}): only page content is available"]


async def build_page_map_live(
    session: BrowserSession,
    url: str | None = None,
    enable_tier3: bool = True,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
    template_cache: InMemoryTemplateCache | None = None,
) -> PageMap:
    """Build a PageMap from a live browser session.

    If url is provided, navigates to it first.
    Otherwise uses the current page.

    Args:
        session: active BrowserSession
        url: optional URL to navigate to
        enable_tier3: enable CDP-based Tier 3 detection
        max_pruned_tokens: token budget for pruned_context
        template_cache: optional template cache for structural hints

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

    # Template lookup (before build_pruned_context)
    template: PageTemplate | None = None
    _template_key: TemplateKey | None = None
    if template_cache is not None and page_type != "unknown":
        _template_key = TemplateKey(extract_template_domain(page_url), page_type)
        template = template_cache.lookup(_template_key)

    # Parallel: detect interactables + fetch HTML (independent read-only ops)
    warnings: list[str] = []
    (interactables, detect_warnings), raw_html = await asyncio.gather(
        _detect_all_safe(session.page, enable_tier3),
        session.get_page_html(),
    )
    warnings.extend(detect_warnings)

    # Build pruned context with auto-detected locale + CJK budget compensation
    locale = detect_locale(page_url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)
    pruned_context, pruned_tokens, metadata = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id="live",
        schema_name=schema,
        max_tokens=budget.pruned_context,
        locale=locale,
        template=template,
    )
    metadata["_total_budget"] = budget.total

    if budget.multiplier != 1.0:
        logger.info(
            "CJK budget: locale=%s mul=%.2f cjk=%.2f pruned=%d→%d total=%d→%d",
            budget.locale,
            budget.multiplier,
            budget.cjk_ratio,
            max_pruned_tokens,
            budget.pruned_context,
            DEFAULT_TOTAL_BUDGET_TOKENS,
            budget.total,
        )

    # Extract learning data (popped immediately — never reaches serializer)
    _pruning_result = metadata.pop("_pruning_result", None)
    _pruning_warnings = metadata.pop("_pruning_warnings", [])
    warnings.extend(_pruning_warnings)
    _pruned_regions: set[str] = metadata.pop("_pruned_regions", set())

    # Template learning / validation
    if template_cache is not None and _pruning_result is not None and _template_key is not None:
        if template is None:
            # First visit → learn
            new_tmpl = learn_template(
                key=_template_key,
                schema_name=schema,
                pruning_result=_pruning_result,
                metadata=metadata,
                source_url=page_url,
                raw_html=raw_html,
            )
            template_cache.store(new_tmpl)
            logger.debug("Template learned: %s from %s", _template_key, page_url)
        else:
            # Revisit → validate
            actual_source = infer_metadata_source(metadata, _pruning_result.meta_chunks)
            actual_has_main = any(c.in_main for c in _pruning_result.selected_chunks)
            aom_stats = _pruning_result.aom_filter_stats
            actual_aom_ratio = aom_stats.removed_nodes / max(aom_stats.total_nodes, 1)
            actual_chunk_ratio = _pruning_result.chunk_count_selected / max(_pruning_result.chunk_count_total, 1)
            validation = validate_template(
                template,
                actual_has_main=actual_has_main,
                actual_metadata_source=actual_source,
                actual_aom_removal_ratio=actual_aom_ratio,
                actual_chunk_selection_ratio=actual_chunk_ratio,
            )
            if validation.passed:
                template_cache.record_validation_pass(_template_key)
            else:
                template_cache.record_validation_failure(_template_key)
                logger.info("Template validation failed: %s", validation.mismatches)

    # Extract product images
    images = extract_product_images(raw_html, page_url)

    # Budget-aware filtering
    interactables = _budget_filter_interactables(
        interactables, pruned_tokens, total_budget=budget.total, warnings=warnings
    )

    # Phase 4.1: Pruned region coherence warning
    if _pruned_regions and interactables:
        affected = sum(1 for el in interactables if el.region in _pruned_regions)
        if affected:
            region_list = ", ".join(sorted(_pruned_regions))
            warnings.append(
                f"{affected} interactable(s) in pruned regions ({region_list}) — surrounding context unavailable"
            )

    # Navigation hints (after budget filter so refs match)
    navigation_hints = _build_navigation_hints(interactables, raw_html, page_type)

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
        navigation_hints=navigation_hints,
        pruned_regions=_pruned_regions,
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


# ── Tier B/C partial rebuild functions ────────────────────────────────


async def rebuild_content_only(
    session: BrowserSession,
    cached: PageMap,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
    template_cache: InMemoryTemplateCache | None = None,
) -> PageMap:
    """Tier B: structure identical, text changed.

    Reuses cached interactables (refs preserved), rebuilds only pruned_context.
    Skips detect_all (~300ms savings).

    Args:
        session: active BrowserSession
        cached: the previously cached PageMap
        max_pruned_tokens: token budget for pruned_context
        template_cache: optional template cache for structural hints

    Returns:
        New PageMap with cached interactables + fresh pruned_context
    """
    start = time.monotonic()

    page_url = await session.get_page_url()
    page_title = await session.get_page_title()
    page_type = detect_page_type(page_url)
    schema = detect_schema(page_url)
    site_id = _extract_site_id(page_url)

    # Template lookup
    template: PageTemplate | None = None
    _template_key: TemplateKey | None = None
    if template_cache is not None and page_type != "unknown":
        _template_key = TemplateKey(extract_template_domain(page_url), page_type)
        template = template_cache.lookup(_template_key)

    raw_html = await session.get_page_html()

    locale = detect_locale(page_url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)
    pruned_context, pruned_tokens, metadata = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id="live",
        schema_name=schema,
        max_tokens=budget.pruned_context,
        locale=locale,
        template=template,
    )
    metadata["_total_budget"] = budget.total

    if budget.multiplier != 1.0:
        logger.info(
            "CJK budget: locale=%s mul=%.2f cjk=%.2f pruned=%d→%d total=%d→%d",
            budget.locale,
            budget.multiplier,
            budget.cjk_ratio,
            max_pruned_tokens,
            budget.pruned_context,
            DEFAULT_TOTAL_BUDGET_TOKENS,
            budget.total,
        )

    # Extract learning data (popped immediately)
    _pruning_result = metadata.pop("_pruning_result", None)
    _pruning_warnings = metadata.pop("_pruning_warnings", [])
    warnings = list(cached.warnings) + _pruning_warnings  # don't mutate cached
    _pruned_regions: set[str] = metadata.pop("_pruned_regions", set())

    # Template validation (Tier B — template should already exist)
    if template_cache is not None and _pruning_result is not None and _template_key is not None:
        if template is None:
            new_tmpl = learn_template(
                key=_template_key,
                schema_name=schema,
                pruning_result=_pruning_result,
                metadata=metadata,
                source_url=page_url,
                raw_html=raw_html,
            )
            template_cache.store(new_tmpl)
        else:
            actual_source = infer_metadata_source(metadata, _pruning_result.meta_chunks)
            actual_has_main = any(c.in_main for c in _pruning_result.selected_chunks)
            aom_stats = _pruning_result.aom_filter_stats
            actual_aom_ratio = aom_stats.removed_nodes / max(aom_stats.total_nodes, 1)
            actual_chunk_ratio = _pruning_result.chunk_count_selected / max(_pruning_result.chunk_count_total, 1)
            validation = validate_template(
                template,
                actual_has_main=actual_has_main,
                actual_metadata_source=actual_source,
                actual_aom_removal_ratio=actual_aom_ratio,
                actual_chunk_selection_ratio=actual_chunk_ratio,
            )
            if validation.passed:
                template_cache.record_validation_pass(_template_key)
            else:
                template_cache.record_validation_failure(_template_key)

    images = extract_product_images(raw_html, page_url)
    navigation_hints = _build_navigation_hints(cached.interactables, raw_html, page_type)

    # Phase 4.1: Pruned region coherence warning
    if _pruned_regions and cached.interactables:
        affected = sum(1 for el in cached.interactables if el.region in _pruned_regions)
        if affected:
            region_list = ", ".join(sorted(_pruned_regions))
            warnings.append(
                f"{affected} interactable(s) in pruned regions ({region_list}) — surrounding context unavailable"
            )

    elapsed_ms = (time.monotonic() - start) * 1000

    page_map = PageMap(
        url=page_url,
        title=page_title,
        page_type=page_type,
        interactables=cached.interactables,  # reused from cache
        pruned_context=pruned_context,
        pruned_tokens=pruned_tokens,
        generation_ms=elapsed_ms,
        images=images,
        metadata=metadata,
        warnings=warnings,
        navigation_hints=navigation_hints,
        pruned_regions=_pruned_regions,
    )

    logger.info(
        "PageMap content-only rebuild: %d interactables (reused), %d pruned tokens, %.0fms",
        len(cached.interactables),
        pruned_tokens,
        elapsed_ms,
    )
    return page_map


async def rebuild_interactables_only(
    session: BrowserSession,
    cached: PageMap,
    enable_tier3: bool = True,
) -> PageMap:
    """Tier C-light: minor structural change.

    Re-detects interactables only, reuses pruned_context.
    Saves HTML fetch + pruned_context build (~200ms savings).

    Args:
        session: active BrowserSession
        cached: the previously cached PageMap
        enable_tier3: enable CDP-based Tier 3 detection

    Returns:
        New PageMap with fresh interactables + cached pruned_context
    """
    start = time.monotonic()

    page_url = await session.get_page_url()
    page_title = await session.get_page_title()
    page_type = detect_page_type(page_url)

    # Reuse stored total_budget from original build, fallback to URL-only computation
    total_budget = cached.metadata.get("_total_budget")
    if total_budget is None:
        locale = detect_locale(page_url)
        budget = compute_token_budget(locale)  # URL-only fallback
        total_budget = budget.total

    warnings: list[str] = []
    interactables, detect_warnings = await _detect_all_safe(session.page, enable_tier3)
    warnings.extend(detect_warnings)

    interactables = _budget_filter_interactables(
        interactables, cached.pruned_tokens, total_budget=total_budget, warnings=warnings
    )

    raw_html = await session.get_page_html()
    navigation_hints = _build_navigation_hints(interactables, raw_html, page_type)

    # Phase 4.1: Pruned region coherence warning (carry forward from cache)
    if cached.pruned_regions and interactables:
        affected = sum(1 for el in interactables if el.region in cached.pruned_regions)
        if affected:
            region_list = ", ".join(sorted(cached.pruned_regions))
            warnings.append(
                f"{affected} interactable(s) in pruned regions ({region_list}) — surrounding context unavailable"
            )

    elapsed_ms = (time.monotonic() - start) * 1000

    page_map = PageMap(
        url=page_url,
        title=page_title,
        page_type=page_type,
        interactables=interactables,
        pruned_context=cached.pruned_context,  # reused from cache
        pruned_tokens=cached.pruned_tokens,
        generation_ms=elapsed_ms,
        images=cached.images,
        metadata=cached.metadata,
        warnings=warnings,
        navigation_hints=navigation_hints,
        pruned_regions=cached.pruned_regions,
    )

    logger.info(
        "PageMap interactables-only rebuild: %d interactables, %d pruned tokens (reused), %.0fms",
        len(interactables),
        cached.pruned_tokens,
        elapsed_ms,
    )
    return page_map


def _extract_interactables_from_html(raw_html: str) -> list[Interactable]:
    """Extract interactive elements from raw HTML via static parsing.

    Lightweight fallback for offline mode (no browser/AX tree).
    Detects buttons, links with CTA text, inputs, and selects.
    """
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
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    locale = detect_locale(url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)
    pruned_context, pruned_tokens, metadata = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id=page_id,
        schema_name=schema_name,
        max_tokens=budget.pruned_context,
        locale=locale,
    )
    metadata["_total_budget"] = budget.total

    if budget.multiplier != 1.0:
        logger.info(
            "CJK budget: locale=%s mul=%.2f cjk=%.2f pruned=%d→%d total=%d→%d",
            budget.locale,
            budget.multiplier,
            budget.cjk_ratio,
            max_pruned_tokens,
            budget.pruned_context,
            DEFAULT_TOTAL_BUDGET_TOKENS,
            budget.total,
        )

    warnings: list[str] = metadata.pop("_pruning_warnings", [])
    _pruned_regions: set[str] = metadata.pop("_pruned_regions", set())

    # Extract interactables from HTML (static parsing)
    interactables = _extract_interactables_from_html(raw_html)

    # Extract product images
    images = extract_product_images(raw_html, url)

    # Budget-aware filtering
    interactables = _budget_filter_interactables(
        interactables, pruned_tokens, total_budget=budget.total, warnings=warnings
    )

    # Navigation hints (after budget filter so refs match)
    navigation_hints = _build_navigation_hints(interactables, raw_html, page_type)

    # Phase 4.1: Pruned region coherence warning
    if _pruned_regions and interactables:
        affected = sum(1 for el in interactables if el.region in _pruned_regions)
        if affected:
            region_list = ", ".join(sorted(_pruned_regions))
            warnings.append(
                f"{affected} interactable(s) in pruned regions ({region_list}) — surrounding context unavailable"
            )

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
        warnings=warnings,
        navigation_hints=navigation_hints,
        pruned_regions=_pruned_regions,
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

    # Build pruned context with auto-detected locale + CJK budget compensation
    locale = detect_locale(url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)
    pruned_context, pruned_tokens, structured_meta = build_pruned_context(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id=page_id,
        schema_name=schema_name,
        max_tokens=budget.pruned_context,
        locale=locale,
    )
    structured_meta["_total_budget"] = budget.total

    if budget.multiplier != 1.0:
        logger.info(
            "CJK budget: locale=%s mul=%.2f cjk=%.2f pruned=%d→%d total=%d→%d",
            budget.locale,
            budget.multiplier,
            budget.cjk_ratio,
            max_pruned_tokens,
            budget.pruned_context,
            DEFAULT_TOTAL_BUDGET_TOKENS,
            budget.total,
        )

    _pruning_warnings = structured_meta.pop("_pruning_warnings", [])
    warnings.extend(_pruning_warnings)
    _pruned_regions: set[str] = structured_meta.pop("_pruned_regions", set())

    # Title from metadata or HTML
    title = meta.get("title", "")
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

    # Extract product images
    images = extract_product_images(raw_html, url)

    # Budget-aware filtering
    interactables = _budget_filter_interactables(
        interactables, pruned_tokens, total_budget=budget.total, warnings=warnings
    )

    # Navigation hints (after budget filter so refs match)
    navigation_hints = _build_navigation_hints(interactables, raw_html, page_type)

    # Phase 4.1: Pruned region coherence warning
    if _pruned_regions and interactables:
        affected = sum(1 for el in interactables if el.region in _pruned_regions)
        if affected:
            region_list = ", ".join(sorted(_pruned_regions))
            warnings.append(
                f"{affected} interactable(s) in pruned regions ({region_list}) — surrounding context unavailable"
            )

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
        navigation_hints=navigation_hints,
        pruned_regions=_pruned_regions,
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


# Pre-compute lowered term sets for navigation hint matching
_NEXT_TERMS_LOWER = tuple(t.lower() for t in NEXT_BUTTON_TERMS)
_PREV_TERMS_LOWER = tuple(t.lower() for t in PREV_BUTTON_TERMS)
_LOAD_MORE_TERMS_LOWER = tuple(t.lower() for t in LOAD_MORE_TERMS)
_MAX_FILTER_REFS = 10


def _build_navigation_hints(
    interactables: list[Interactable],
    raw_html: str,
    page_type: str,
) -> dict:
    """Build navigation hints (pagination + filter refs) for listing/search pages.

    Must be called AFTER budget filtering so refs match final numbering.

    Args:
        interactables: budget-filtered interactables with final ref numbers
        raw_html: full page HTML for pagination extraction
        page_type: detected page type

    Returns:
        Dict with detected keys only; empty dict for non-listing pages.
    """
    if page_type not in ("search_results", "listing"):
        return {}

    hints: dict = {}

    # Pagination info from HTML
    pagination = extract_pagination_structured(raw_html)

    # Match interactable names to navigation terms
    for item in interactables:
        name_lower = item.name.lower()
        if any(t in name_lower for t in _NEXT_TERMS_LOWER):
            pagination["next_ref"] = item.ref
            break

    for item in interactables:
        name_lower = item.name.lower()
        if any(t in name_lower for t in _PREV_TERMS_LOWER):
            pagination["prev_ref"] = item.ref
            break

    for item in interactables:
        name_lower = item.name.lower()
        if any(t in name_lower for t in _LOAD_MORE_TERMS_LOWER):
            pagination["load_more_ref"] = item.ref
            break

    if pagination:
        hints["pagination"] = pagination

    # Filter refs: complementary-region interactables
    filter_refs = [item.ref for item in interactables if item.region == "complementary"]
    if filter_refs:
        hints["filters"] = {"filter_refs": filter_refs[:_MAX_FILTER_REFS]}

    return hints


def _budget_filter_interactables(
    interactables: list[Interactable],
    pruned_tokens: int,
    total_budget: int = DEFAULT_TOTAL_BUDGET_TOKENS,
    warnings: list[str] | None = None,
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
        warnings: if provided, appends a message when elements are dropped

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
            "Budget filter: %d → %d interactables (%d tokens available)",
            len(interactables),
            len(selected),
            available,
        )
        if warnings is not None:
            warnings.append(f"{len(selected)} of {len(interactables)} interactable elements shown (token budget)")

    return selected
