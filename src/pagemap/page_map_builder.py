# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""PageMap orchestrator: combines interactive detection + pruned context.

Two modes:
- Live mode: navigates to URL, captures AX tree + HTML → PageMap
- Offline mode: loads snapshot HTML, runs detection + pruning → PageMap
"""

from __future__ import annotations

import asyncio
import html as _html
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pagemap.preprocessing.preprocess import count_tokens, count_tokens_approx

from . import Interactable, PageMap
from .browser_session import BrowserSession
from .errors import ResourceExhaustionError
from .i18n import (
    LOAD_MORE_TERMS,
    NEXT_BUTTON_TERMS,
    PREV_BUTTON_TERMS,
    detect_locale,
)
from .interactive_detector import _is_table_noise, detect_all
from .page_classifier import classify_page
from .pipeline_timer import PipelineTimer
from .pruned_context_builder import (
    _ALLOWED_URL_PREFIXES,
    _EXCLUDE_IMG_PATTERNS,
    _MAX_URL_LENGTH,
    _NO_TEMPLATE,
    _normalize_image_url,
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

# ── Budget filtering constants ────────────────────────────────────────
_OVERHEAD_TOKEN_ESTIMATE = 80
_MIN_INTERACTABLE_BUDGET = 100
_VISIBLE_TEXT_SAMPLE_LEN = 2000
_HTML_BODY_SAMPLE_LEN = 30000

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_PRUNED_CONTEXT_TOKENS = 1500
DEFAULT_TOTAL_BUDGET_TOKENS = 5000

# ── Resource exhaustion limits ────────────────────────────────────────
MAX_DOM_NODES = 50_000  # Reject pages with >50K DOM nodes (memory exhaustion defense)
MAX_HTML_SIZE_BYTES = 5 * 1024 * 1024  # 5MB limit on page.content() (OOM prevention)

# ── DOM guard + hidden content detection (single evaluate call) ───────
_DOM_GUARD_AND_HIDDEN_JS = """
(() => {
  const all = document.body.querySelectorAll('*');
  const nodeCount = all.length;
  let hiddenRemoved = 0;
  for (let i = all.length - 1; i >= 0; i--) {
    const el = all[i];
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'style' || tag === 'noscript' || tag === 'link' || tag === 'meta') continue;
    if (!el.parentNode) continue;
    try {
      const cs = getComputedStyle(el);
      if (
        cs.display === 'none' ||
        cs.visibility === 'hidden' ||
        cs.opacity === '0' ||
        (cs.fontSize === '0px' && el.children.length === 0) ||
        cs.clipPath === 'inset(100%)' ||
        /scale\\(0[),\\s]/.test(cs.transform) ||
        (parseInt(cs.textIndent) <= -9000 && cs.overflow === 'hidden') ||
        (cs.overflow === 'hidden' && parseInt(cs.height) === 0 && el.children.length === 0) ||
        (cs.position === 'absolute' && (
          parseInt(cs.left) < -9000 || parseInt(cs.top) < -9000 ||
          (parseInt((cs.clip || '').split(',')[0]?.replace('rect(','')) === 0 &&
           parseInt((cs.clip || '').split(',')[1]) === 0)
        ))
      ) {
        el.remove();
        hiddenRemoved++;
      }
    } catch(e) {}
  }
  return { nodeCount, hiddenRemoved };
})()
"""


def _merge_structured_images(
    html_images: list[str],
    metadata: dict[str, Any],
) -> tuple[list[str], bool]:
    """Merge structured-data image (JSON-LD/OG) into the HTML-extracted list.

    Prepends the metadata image_url to the front of the list if it passes
    security validation and is not already present (canonical dedup).

    Returns (merged_list, was_merged) tuple.
    """
    meta_img = metadata.get("image_url")
    if not meta_img or not isinstance(meta_img, str):
        return html_images[:10], False

    meta_img = meta_img.strip()
    if not meta_img:
        return html_images[:10], False

    # Security: scheme allowlist + length limit
    img_lower = meta_img.lower()
    if ":" in img_lower.split("/")[0] and not img_lower.startswith(_ALLOWED_URL_PREFIXES):
        return html_images[:10], False
    if len(meta_img) > _MAX_URL_LENGTH:
        return html_images[:10], False

    # Exclude pattern check (tracking, logos, etc.)
    if _EXCLUDE_IMG_PATTERNS.search(meta_img):
        return html_images[:10], False

    # Canonical dedup: check if already in list
    canon_meta = _normalize_image_url(meta_img)
    for existing in html_images:
        if _normalize_image_url(existing) == canon_meta:
            return html_images[:10], False

    # Prepend structured image and cap at 10
    merged = [meta_img] + html_images
    return merged[:10], True


def _check_html_size(raw_html: str) -> None:
    """Reject HTML exceeding 5MB limit. Raises ResourceExhaustionError + emits telemetry."""
    html_size = len(raw_html.encode("utf-8"))
    if html_size > MAX_HTML_SIZE_BYTES:
        try:
            from .telemetry import emit
            from .telemetry.events import RESOURCE_GUARD_TRIGGERED

            emit(RESOURCE_GUARD_TRIGGERED, {"guard": "html_size", "value": html_size, "limit": MAX_HTML_SIZE_BYTES})
        except Exception:  # nosec B110
            pass
        raise ResourceExhaustionError(
            f"HTML size {html_size:,} bytes exceeds {MAX_HTML_SIZE_BYTES:,} byte limit. "
            "Try a more specific URL or a lighter page."
        )


def _extract_pruning_metadata(
    meta: dict[str, Any],
    warnings: list[str],
) -> set[str]:
    """Pop internal pruning keys from metadata and update warnings."""
    pruning_warnings = meta.pop("_pruning_warnings", [])
    warnings.extend(pruning_warnings)
    if meta.pop("_mcg_activated", False):
        warnings.append("Content extraction used minimum content guarantee; page content may be sparse")
    return meta.pop("_pruned_regions", set())


_BLOCKED_PAGE_WARNING = (
    "Page is blocked by anti-bot protection (captcha/WAF). "
    "Content shown is from the block page, not the intended page. "
    "Try: (1) a different URL on the same site, (2) a less protected page, "
    "or (3) inform the user the site requires manual verification."
)


def _check_blocked_page(
    page_type: str,
    warnings: list[str],
    metadata: dict,
    *,
    url: str = "",
    http_status: int | None = None,
) -> None:
    """Append warning and structured info if page is classified as blocked."""
    if page_type != "blocked":
        return
    warnings.append(_BLOCKED_PAGE_WARNING)
    blocked_info: dict = {"detected": True}
    if http_status is not None:
        blocked_info["http_status"] = http_status
    metadata["blocked_info"] = blocked_info
    try:
        from .telemetry import emit
        from .telemetry.events import CAPTCHA_DETECTED

        emit(CAPTCHA_DETECTED, {"url": url, "http_status": http_status})
    except Exception:  # nosec B110
        pass


async def _check_resource_limits(page, raw_html: str) -> str:
    """HTML size + DOM guard + hidden content JS. Returns (possibly refreshed) HTML.

    Performs:
    1. HTML size check (raises ResourceExhaustionError if > 5MB)
    2. DOM node guard via JS evaluate (raises if > 50K nodes)
    3. Hidden content removal via getComputedStyle
    4. Re-fetches HTML if hidden elements were removed
    """
    _check_html_size(raw_html)

    dom_check: dict | None = None
    try:
        dom_check = await page.evaluate(_DOM_GUARD_AND_HIDDEN_JS)
    except Exception:
        logger.warning("DOM guard/hidden content check failed, skipping")

    if dom_check is not None:
        node_count = dom_check.get("nodeCount", 0)
        if node_count > MAX_DOM_NODES:
            try:
                from .telemetry import emit
                from .telemetry.events import RESOURCE_GUARD_TRIGGERED

                emit(RESOURCE_GUARD_TRIGGERED, {"guard": "dom_nodes", "value": node_count, "limit": MAX_DOM_NODES})
            except Exception:  # nosec B110
                pass
            raise ResourceExhaustionError(
                f"DOM has {node_count:,} nodes (limit: {MAX_DOM_NODES:,}). "
                "Page is too complex. Try a more specific URL."
            )
        hidden_removed = dom_check.get("hiddenRemoved", 0)
        if hidden_removed > 0:
            logger.info("Hidden content removal: %d elements stripped via getComputedStyle", hidden_removed)
            try:
                from .telemetry import emit
                from .telemetry.events import HIDDEN_CONTENT_REMOVED

                emit(HIDDEN_CONTENT_REMOVED, {"hidden_removed": hidden_removed})
            except Exception:  # nosec B110
                pass
            # Re-fetch HTML after hidden element removal
            raw_html = await page.content()

    return raw_html


# ── CJK Token Budget ─────────────────────────────────────────────────

_CJK_TOKEN_MULTIPLIERS: dict[str, float] = {
    "ko": 1.8,  # Hangul: ~0.61 chars/token, 9.4x penalty
    "zh": 1.6,  # Hanzi-heavy: between ko(1.8 Hangul) and ja(1.5 kana+kanji mix)
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
    html_slice = raw_html[start : start + _HTML_BODY_SAMPLE_LEN]

    # Strip script/style/noscript CONTENT (not just tags)
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html_slice)
    # Strip remaining HTML tags
    text = _TAG_RE.sub(" ", cleaned)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:_VISIBLE_TEXT_SAMPLE_LEN]


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


_PRUNED_CONTEXT_THREAD_TIMEOUT = 30.0


async def _build_pruned_context_async(
    raw_html: str,
    page_type: str = "default",
    site_id: str = "unknown",
    page_id: str = "unknown",
    schema_name: str = "Product",
    max_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
    locale: str | None = None,
    template: Any = _NO_TEMPLATE,
    enable_lang_filter: bool = True,
) -> tuple[str, int, dict]:
    """Run build_pruned_context in a worker thread to unblock the event loop.

    Thread-safe: all arguments are immutable or read-only.
    lxml doc is created inside the function (thread-local).
    lxml/tiktoken are C/Rust extensions that release the GIL.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(
            build_pruned_context,
            raw_html,
            page_type=page_type,
            site_id=site_id,
            page_id=page_id,
            schema_name=schema_name,
            max_tokens=max_tokens,
            locale=locale,
            template=template,
            enable_lang_filter=enable_lang_filter,
        ),
        timeout=_PRUNED_CONTEXT_THREAD_TIMEOUT,
    )


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
    "amazon.com": "Product",
    # Non-ecommerce
    "news.naver.com": "NewsArticle",
    "bbc.com": "NewsArticle",
    "ko.wikipedia.org": "WikiArticle",
    "github.com": "SaaSPage",
    "gov.kr": "GovernmentPage",
}


def detect_page_type(url: str, raw_html: str | None = None) -> str:
    """Detect page type via weighted voting (backward-compatible wrapper).

    Delegates to :func:`page_classifier.classify_page`.
    """
    return classify_page(url, raw_html).page_type


_GOV_TLD_RE = re.compile(r"\.go(?:v)?(?:\.[a-z]{2})?(?:/|$)", re.IGNORECASE)


def detect_schema(url: str) -> str:
    """Domain fast path + URL signal → Generic fallback."""
    from urllib.parse import urlparse

    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
    for domain, schema in DOMAIN_SCHEMA_MAP.items():
        if hostname == domain or hostname.endswith("." + domain):
            return schema
    if _GOV_TLD_RE.search(url):
        return "GovernmentPage"
    return "Generic"


# ---------------------------------------------------------------------------
# JSON-LD schema sniffing (used for Generic → concrete schema override)
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_JSONLD_TYPE_TO_SCHEMA: dict[str, str] = {
    "Product": "Product",
    "IndividualProduct": "Product",
    "NewsArticle": "NewsArticle",
    "Article": "NewsArticle",
    "ReportageNewsArticle": "NewsArticle",
    "BlogPosting": "NewsArticle",
    "SoftwareApplication": "SaaSPage",
    "WebApplication": "SaaSPage",
    "GovernmentOrganization": "GovernmentPage",
    "GovernmentService": "GovernmentPage",
    "FAQPage": "FAQPage",
    "Event": "Event",
    "MusicEvent": "Event",
    "SportsEvent": "Event",
    "TheaterEvent": "Event",
    "BusinessEvent": "Event",
    "EducationEvent": "Event",
    "Festival": "Event",
    "ExhibitionEvent": "Event",
    "LocalBusiness": "LocalBusiness",
    "Restaurant": "LocalBusiness",
    "Hotel": "LocalBusiness",
    "Store": "LocalBusiness",
    "MedicalClinic": "LocalBusiness",
    "FoodEstablishment": "LocalBusiness",
    "HealthAndBeautyBusiness": "LocalBusiness",
    "AutoRepair": "LocalBusiness",
    "Dentist": "LocalBusiness",
    "RealEstateAgent": "LocalBusiness",
}


def _resolve_jsonld_type(data: Any) -> str | None:
    """Recursively find @type in JSON-LD (handles @graph, arrays)."""
    if isinstance(data, list):
        return next((r for item in data if (r := _resolve_jsonld_type(item))), None)
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _resolve_jsonld_type(data["@graph"])
    t = data.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return next(
        (_JSONLD_TYPE_TO_SCHEMA[x] for x in types if x in _JSONLD_TYPE_TO_SCHEMA),
        None,
    )


def _detect_schema_from_jsonld(raw_html: str) -> str | None:
    """Lightweight JSON-LD @type sniffing — regex + json.loads, no lxml."""
    for m in _JSONLD_RE.finditer(raw_html):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        result = _resolve_jsonld_type(data)
        if result is not None:
            return result
    return None


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
    timer: PipelineTimer | None = None,
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

    nav_result = None
    if url:
        if timer:
            timer.stage("navigation")
        nav_result = await session.navigate(url)

    if timer:
        timer.stage("page_info")
    page_url = await session.get_page_url()
    page_title = await session.get_page_title()
    page_type = detect_page_type(page_url)  # URL-only (pre raw_html)
    schema = detect_schema(page_url)
    site_id = _extract_site_id(page_url)

    # Template lookup (before build_pruned_context)
    template: PageTemplate | None = None
    _template_key: TemplateKey | None = None
    if template_cache is not None and page_type != "unknown":
        _template_key = TemplateKey(extract_template_domain(page_url), page_type)
        template = template_cache.lookup(_template_key)

    # Parallel: detect interactables + fetch HTML (independent read-only ops)
    if timer:
        timer.stage("detection")
    warnings: list[str] = []
    (interactables, detect_warnings), raw_html = await asyncio.gather(
        _detect_all_safe(session.page, enable_tier3),
        session.get_page_html(),
    )
    warnings.extend(detect_warnings)

    # ── Resource exhaustion guards ────────────────────────────────────
    raw_html = await _check_resource_limits(session.page, raw_html)

    # Re-classify with raw HTML for meta/DOM signals
    page_type = detect_page_type(page_url, raw_html)
    if template_cache is not None and page_type != "unknown":
        _template_key = TemplateKey(extract_template_domain(page_url), page_type)
        template = template_cache.lookup(_template_key)

    # Build pruned context with auto-detected locale + CJK budget compensation
    if timer:
        timer.stage("pruning")
    locale = detect_locale(page_url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)
    pruned_context, pruned_tokens, metadata = await _build_pruned_context_async(
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
    _pruned_regions = _extract_pruning_metadata(metadata, warnings)

    # Captcha/WAF block page detection
    _check_blocked_page(
        page_type,
        warnings,
        metadata,
        url=page_url,
        http_status=nav_result.http_status if nav_result else None,
    )

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

    # Assembly
    if timer:
        timer.stage("assembly")
    # Extract product images
    images, _img_stats = extract_product_images(raw_html, page_url)
    images, _img_merged = _merge_structured_images(images, metadata)
    _img_stats["structured_image_merged"] = _img_merged
    try:
        from .telemetry import emit, events
        from .telemetry.events import IMAGE_FILTER_APPLIED

        emit(IMAGE_FILTER_APPLIED, events.image_filter_applied(**_img_stats))
    except Exception:  # nosec B110
        pass

    # Budget-aware filtering
    interactables = _budget_filter_interactables(
        interactables,
        pruned_tokens,
        total_budget=budget.total,
        warnings=warnings,
        pruned_regions=_pruned_regions,
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

    if timer:
        timer.finalize()
        metadata["stage_timing"] = timer.success_metadata()

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


# ── Batch: build from an already-navigated Page ──────────────────────


async def build_page_map_from_page(
    page,
    enable_tier3: bool = True,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
    template_cache: InMemoryTemplateCache | None = None,
) -> PageMap:
    """Build a PageMap from an already-navigated Page object.

    Used by batch_get_page_map — no navigation, no template learning.
    Per-page CDP session is handled by detect_all internally.
    """
    start = time.monotonic()

    page_url = page.url
    page_title = await page.title()
    schema = detect_schema(page_url)
    site_id = _extract_site_id(page_url)

    warnings: list[str] = []
    (interactables, detect_warnings), raw_html = await asyncio.gather(
        _detect_all_safe(page, enable_tier3),
        page.content(),
    )
    warnings.extend(detect_warnings)

    # ── Resource exhaustion guards ────────────────────────────────────
    raw_html = await _check_resource_limits(page, raw_html)

    # Classify with full HTML
    page_type = detect_page_type(page_url, raw_html)

    locale = detect_locale(page_url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)

    # Template lookup (read-only — no learning in batch)
    template: PageTemplate | None = None
    if template_cache is not None and page_type != "unknown":
        _template_key = TemplateKey(extract_template_domain(page_url), page_type)
        template = template_cache.lookup(_template_key)

    pruned_context, pruned_tokens, metadata = await _build_pruned_context_async(
        raw_html=raw_html,
        page_type=page_type,
        site_id=site_id,
        page_id="batch",
        schema_name=schema,
        max_tokens=budget.pruned_context,
        locale=locale,
        template=template,
    )
    metadata["_total_budget"] = budget.total

    metadata.pop("_pruning_result", None)
    _pruned_regions = _extract_pruning_metadata(metadata, warnings)

    # Captcha/WAF block page detection (no http_status — already navigated)
    _check_blocked_page(page_type, warnings, metadata, url=page_url)

    images, _img_stats = extract_product_images(raw_html, page_url)
    images, _img_merged = _merge_structured_images(images, metadata)
    _img_stats["structured_image_merged"] = _img_merged
    try:
        from .telemetry import emit, events
        from .telemetry.events import IMAGE_FILTER_APPLIED

        emit(IMAGE_FILTER_APPLIED, events.image_filter_applied(**_img_stats))
    except Exception:  # nosec B110
        pass

    interactables = _budget_filter_interactables(
        interactables,
        pruned_tokens,
        total_budget=budget.total,
        warnings=warnings,
        pruned_regions=_pruned_regions,
    )

    navigation_hints = _build_navigation_hints(interactables, raw_html, page_type)

    if _pruned_regions and interactables:
        affected = sum(1 for el in interactables if el.region in _pruned_regions)
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
        "PageMap (batch): %d interactables, %d pruned tokens, %.0fms",
        len(interactables),
        pruned_tokens,
        elapsed_ms,
    )
    return page_map


# ── Tier B/C partial rebuild functions ────────────────────────────────


async def rebuild_content_only(
    session: BrowserSession,
    cached: PageMap,
    max_pruned_tokens: int = DEFAULT_PRUNED_CONTEXT_TOKENS,
    template_cache: InMemoryTemplateCache | None = None,
    timer: PipelineTimer | None = None,
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
    schema = detect_schema(page_url)
    site_id = _extract_site_id(page_url)

    raw_html = await session.get_page_html()

    # ── Resource exhaustion guards (full: HTML + DOM + hidden) ─────
    raw_html = await _check_resource_limits(session.page, raw_html)

    # Classify with full HTML
    page_type = detect_page_type(page_url, raw_html)

    # Template lookup
    template: PageTemplate | None = None
    _template_key: TemplateKey | None = None
    if template_cache is not None and page_type != "unknown":
        _template_key = TemplateKey(extract_template_domain(page_url), page_type)
        template = template_cache.lookup(_template_key)

    locale = detect_locale(page_url)
    budget = compute_token_budget(locale, raw_html, base_pruned=max_pruned_tokens)
    pruned_context, pruned_tokens, metadata = await _build_pruned_context_async(
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
    warnings = list(cached.warnings)  # don't mutate cached
    _pruned_regions = _extract_pruning_metadata(metadata, warnings)

    # Captcha/WAF block page detection
    _check_blocked_page(page_type, warnings, metadata, url=page_url)

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

    images, _img_stats = extract_product_images(raw_html, page_url)
    images, _img_merged = _merge_structured_images(images, metadata)
    _img_stats["structured_image_merged"] = _img_merged
    try:
        from .telemetry import emit, events
        from .telemetry.events import IMAGE_FILTER_APPLIED

        emit(IMAGE_FILTER_APPLIED, events.image_filter_applied(**_img_stats))
    except Exception:  # nosec B110
        pass
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
        interactables,
        cached.pruned_tokens,
        total_budget=total_budget,
        warnings=warnings,
        pruned_regions=cached.pruned_regions,
    )

    raw_html = await session.get_page_html()
    raw_html = await _check_resource_limits(session.page, raw_html)
    page_type = detect_page_type(page_url, raw_html)

    # Captcha/WAF block page detection — shallow copy to avoid mutating cached metadata
    metadata = dict(cached.metadata)
    _check_blocked_page(page_type, warnings, metadata, url=page_url)

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
        metadata=metadata,
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
        options = [_html.unescape(re.sub(r"<[^>]+>", "", o).strip()) for o in options if o.strip()]
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

    # Decode HTML entities in all extracted names
    for el in interactables:
        el.name = _html.unescape(el.name).replace("\xa0", " ")

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

    # HTML size guard (no browser — cannot run DOM/hidden JS)
    _check_html_size(raw_html)

    if page_type is None:
        page_type = detect_page_type(url, raw_html)
    if schema_name is None:
        schema_name = detect_schema(url)

    # Extract title from HTML
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    title = _html.unescape(title_match.group(1).strip()) if title_match else ""

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

    warnings: list[str] = []
    _pruned_regions = _extract_pruning_metadata(metadata, warnings)

    # Captcha/WAF block page detection
    _check_blocked_page(page_type, warnings, metadata, url=url)

    # Extract interactables from HTML (static parsing)
    interactables = _extract_interactables_from_html(raw_html)

    # Extract product images
    images, _img_stats = extract_product_images(raw_html, url)
    images, _img_merged = _merge_structured_images(images, metadata)
    _img_stats["structured_image_merged"] = _img_merged
    try:
        from .telemetry import emit, events
        from .telemetry.events import IMAGE_FILTER_APPLIED

        emit(IMAGE_FILTER_APPLIED, events.image_filter_applied(**_img_stats))
    except Exception:  # nosec B110
        pass

    # Budget-aware filtering
    interactables = _budget_filter_interactables(
        interactables,
        pruned_tokens,
        total_budget=budget.total,
        warnings=warnings,
        pruned_regions=_pruned_regions,
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

    # Pre-load HTML size guard
    _check_html_size(raw_html)

    # Load metadata
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    url = meta.get("url", f"file://{snapshot_dir}")
    site_id = meta.get("site_id", snapshot_dir.parent.name)
    page_id = meta.get("page_id", snapshot_dir.name)

    page_type = detect_page_type(url, raw_html)
    schema_name = detect_schema(url)

    # Load HTML into browser for AX tree
    await session.load_html(raw_html)

    # Post-load resource guards (DOM node count + hidden content removal)
    raw_html = await _check_resource_limits(session.page, raw_html)

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
    pruned_context, pruned_tokens, structured_meta = await _build_pruned_context_async(
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
    if structured_meta.pop("_mcg_activated", False):
        warnings.append("Content extraction used minimum content guarantee; page content may be sparse")
    _pruned_regions: set[str] = structured_meta.pop("_pruned_regions", set())

    # Captcha/WAF block page detection
    _check_blocked_page(page_type, warnings, structured_meta, url=url)

    # Title from metadata or HTML
    title = meta.get("title", "")
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
        title = _html.unescape(title_match.group(1).strip()) if title_match else ""

    # Extract product images
    images, _img_stats = extract_product_images(raw_html, url)
    images, _img_merged = _merge_structured_images(images, structured_meta)
    _img_stats["structured_image_merged"] = _img_merged
    try:
        from .telemetry import emit, events
        from .telemetry.events import IMAGE_FILTER_APPLIED

        emit(IMAGE_FILTER_APPLIED, events.image_filter_applied(**_img_stats))
    except Exception:  # nosec B110
        pass

    # Budget-aware filtering
    interactables = _budget_filter_interactables(
        interactables,
        pruned_tokens,
        total_budget=budget.total,
        warnings=warnings,
        pruned_regions=_pruned_regions,
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


_SUBMIT_TERMS_LOWER = ("submit", "제출", "送信", "envoyer", "absenden", "pay", "결제", "place order", "주문")
_CANCEL_TERMS_LOWER = ("cancel", "취소", "キャンセル", "annuler", "abbrechen", "back", "뒤로")
_HOME_TERMS_LOWER = ("home", "홈", "ホーム", "accueil", "startseite", "go home", "go back")
_SEARCH_TERMS_LOWER = ("search", "검색", "検索", "rechercher", "suchen")


def _build_navigation_hints(
    interactables: list[Interactable],
    raw_html: str,
    page_type: str,
) -> dict:
    """Build navigation hints for various page types.

    Must be called AFTER budget filtering so refs match final numbering.

    Args:
        interactables: budget-filtered interactables with final ref numbers
        raw_html: full page HTML for pagination extraction
        page_type: detected page type

    Returns:
        Dict with detected keys only; empty dict for unsupported page types.
    """
    # Pages that get pagination + filter hints
    if page_type in ("search_results", "listing"):
        return _build_listing_hints(interactables, raw_html)

    # Pages that get submit/cancel hints
    if page_type in ("checkout", "form"):
        return _build_form_hints(interactables, page_type)

    # Pages that get sidebar nav hints
    if page_type in ("dashboard", "documentation"):
        return _build_sidebar_hints(interactables)

    # Pages that get accordion/search hints
    if page_type == "help_faq":
        return _build_faq_hints(interactables)

    # Error pages: home link + search
    if page_type == "error":
        return _build_error_hints(interactables)

    # Blocked pages: verify button hint
    if page_type == "blocked":
        return _build_blocked_hints(interactables)

    return {}


def _build_blocked_hints(interactables: list[Interactable]) -> dict:
    """Verify/retry hints for captcha/WAF block pages."""
    hints: dict = {}
    verify_terms = ("verify", "확인", "continue", "retry", "다시 시도")
    for item in interactables:
        if any(t in item.name.lower() for t in verify_terms):
            hints["verify_ref"] = item.ref
            break
    return hints


def _build_listing_hints(interactables: list[Interactable], raw_html: str) -> dict:
    """Pagination + filter hints for search/listing pages."""
    hints: dict = {}
    pagination = extract_pagination_structured(raw_html)

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

    filter_refs = [item.ref for item in interactables if item.region == "complementary"]
    if filter_refs:
        hints["filters"] = {"filter_refs": filter_refs[:_MAX_FILTER_REFS]}

    return hints


def _build_form_hints(interactables: list[Interactable], page_type: str) -> dict:
    """Submit/cancel hints for checkout and form pages."""
    hints: dict = {}

    for item in interactables:
        name_lower = item.name.lower()
        if any(t in name_lower for t in _SUBMIT_TERMS_LOWER):
            hints["submit_ref"] = item.ref
            break

    for item in interactables:
        name_lower = item.name.lower()
        if any(t in name_lower for t in _CANCEL_TERMS_LOWER):
            hints["cancel_ref"] = item.ref
            break

    # Checkout-specific: step indicator
    if page_type == "checkout":
        step_refs = [item.ref for item in interactables if "step" in item.name.lower() or "단계" in item.name.lower()]
        if step_refs:
            hints["step_refs"] = step_refs[:5]

    return hints


def _build_sidebar_hints(interactables: list[Interactable]) -> dict:
    """Sidebar nav hints for dashboard/documentation pages."""
    hints: dict = {}

    nav_refs = [item.ref for item in interactables if item.region in ("navigation", "complementary")]
    if nav_refs:
        hints["nav_refs"] = nav_refs[:_MAX_FILTER_REFS]

    # Tab refs
    tab_refs = [item.ref for item in interactables if item.role == "tab"]
    if tab_refs:
        hints["tab_refs"] = tab_refs[:_MAX_FILTER_REFS]

    return hints


def _build_faq_hints(interactables: list[Interactable]) -> dict:
    """Accordion/search hints for help/FAQ pages."""
    hints: dict = {}

    # Search ref
    for item in interactables:
        if item.role == "searchbox":
            hints["search_ref"] = item.ref
            break

    # Accordion / question refs (buttons that toggle content)
    question_refs = [item.ref for item in interactables if item.role == "button" and item.region == "main"]
    if question_refs:
        hints["question_refs"] = question_refs[:20]

    return hints


def _build_error_hints(interactables: list[Interactable]) -> dict:
    """Home/search hints for error pages."""
    hints: dict = {}

    for item in interactables:
        name_lower = item.name.lower()
        if any(t in name_lower for t in _HOME_TERMS_LOWER):
            hints["home_ref"] = item.ref
            break

    for item in interactables:
        if item.role == "searchbox" or any(t in item.name.lower() for t in _SEARCH_TERMS_LOWER):
            hints["search_ref"] = item.ref
            break

    return hints


def _budget_filter_interactables(
    interactables: list[Interactable],
    pruned_tokens: int,
    total_budget: int = DEFAULT_TOTAL_BUDGET_TOKENS,
    warnings: list[str] | None = None,
    pruned_regions: set[str] | None = None,
) -> list[Interactable]:
    """Filter interactables to fit within the total token budget.

    Priority order:
    1. Key input elements: searchbox, textbox, combobox, checkbox, radio, switch
    2. Named buttons in header/navigation/search regions
    3. Tier 1 elements (well-labeled) by region priority
    4. Remaining elements until budget is exhausted
    5. Table-structural noise (unnamed row/cell/gridcell) — lowest priority

    Args:
        interactables: full list of detected interactables
        pruned_tokens: tokens used by pruned_context
        total_budget: total token budget for the entire PageMap prompt
        warnings: if provided, appends a message when elements are dropped
        pruned_regions: regions removed during pruning (chrome inputs demoted)

    Returns:
        Filtered list fitting within budget, renumbered sequentially
    """
    if not interactables:
        return interactables

    # Reserve tokens: header (~50) + meta (~30) + pruned_context
    overhead = _OVERHEAD_TOKEN_ESTIMATE
    available = total_budget - pruned_tokens - overhead
    if available < _MIN_INTERACTABLE_BUDGET:
        available = _MIN_INTERACTABLE_BUDGET

    # Priority buckets
    INPUT_ROLES = {"searchbox", "textbox", "combobox", "checkbox", "radio", "switch", "slider"}
    HIGH_REGIONS = {"header", "navigation", "search"}
    _CHROME_DEMOTE_ROLES = {"radio", "checkbox", "switch"}

    bucket_input: list[Interactable] = []
    bucket_high_region: list[Interactable] = []
    bucket_tier1_main: list[Interactable] = []
    bucket_rest: list[Interactable] = []
    bucket_table_noise: list[Interactable] = []

    noise_demoted = 0
    chrome_demoted_roles: list[str] = []

    for el in interactables:
        _in_pruned = bool(pruned_regions and el.region in pruned_regions)

        # QR-01: Table noise → lowest bucket
        if _is_table_noise(el.role, el.name):
            bucket_table_noise.append(el)
            noise_demoted += 1
        elif el.role in INPUT_ROLES:
            # QR-01: Demote chrome inputs in pruned regions
            if _in_pruned and el.role in _CHROME_DEMOTE_ROLES:
                bucket_rest.append(el)
                noise_demoted += 1
                chrome_demoted_roles.append(el.role)
            else:
                bucket_input.append(el)
        elif el.region in HIGH_REGIONS and el.name:
            bucket_high_region.append(el)
        elif el.tier == 1 and el.region == "main":
            bucket_tier1_main.append(el)
        else:
            bucket_rest.append(el)

    # Greedily add from priority buckets (approx token counting)
    selected: list[Interactable] = []
    current_tokens = 0

    for bucket in [bucket_input, bucket_high_region, bucket_tier1_main, bucket_rest, bucket_table_noise]:
        for el in bucket:
            el_tokens = count_tokens_approx(str(el))
            if current_tokens + el_tokens > available:
                break
            selected.append(el)
            current_tokens += el_tokens

    # Exact trim: if approx under-counted, cut by ratio (1 tiktoken call)
    if len(selected) > 1:
        total_text = "\n".join(str(e) for e in selected)
        actual_tokens = count_tokens(total_text)
        if actual_tokens > available:
            keep_ratio = available / actual_tokens
            keep_count = max(1, int(len(selected) * keep_ratio * 0.95))
            selected = selected[:keep_count]

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

    if noise_demoted:
        logger.info("Noise demotion: %d table-structural/chrome elements deprioritized", noise_demoted)
        try:
            from collections import Counter

            from .telemetry import emit, events
            from .telemetry.events import NOISE_FILTER_APPLIED

            noise_counter = Counter(el.role for el in bucket_table_noise)
            noise_counter.update(chrome_demoted_roles)
            emit(
                NOISE_FILTER_APPLIED,
                events.noise_filter_applied(
                    total_interactables=len(interactables),
                    noise_demoted=noise_demoted,
                    noise_roles=dict(noise_counter),
                ),
            )
        except Exception:  # nosec B110
            pass

    return selected
