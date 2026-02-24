# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Weighted-voting page classifier — multi-signal approach.

Replaces the old first-match waterfall ``detect_page_type()`` with a 3-layer
weighted voting system that evaluates URL, Meta/JSON-LD, and DOM signals
simultaneously.  Each signal can contribute positive *and* negative weights
to multiple page types, eliminating dict-order bugs and improving accuracy.

Layers:
  1. URL   – string matching on the URL    (<0.1 ms)
  2. Meta  – regex on raw HTML for <title>, JSON-LD @type, og:type  (<5 ms)
  3. DOM   – lightweight regex-based structure counting  (<30 ms)

A short-circuit optimisation skips layers 2-3 when layer 1 alone produces a
score exceeding 2× the type threshold.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Anti-bot keywords (shared with check_urls.py via import)
# ---------------------------------------------------------------------------

ANTI_BOT_KEYWORDS: tuple[str, ...] = (
    "captcha",
    "challenge-platform",
    "cf-browser-verification",
    "just a moment",
    "access denied",
    "akamai",
    "errors.edgesuite.net",
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalDef:
    """A single signal that contributes scores to one or more page types."""

    name: str
    scores: dict[str, int]  # {page_type: weight} — positive or negative
    check_url: Callable[[str], bool] | None = None
    check_meta: Callable[[str], bool] | None = None
    check_dom: Callable[[str], bool] | None = None


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Result of page classification."""

    page_type: str
    confidence: float  # 0.0–1.0
    score: int  # raw weighted sum of winning type
    signals: tuple[str, ...]  # names of fired signals
    runner_up: str | None  # 2nd-place type (for ambiguity detection)
    runner_up_score: int  # score of runner-up


# ---------------------------------------------------------------------------
# JSON-LD helpers (reuse regex from page_map_builder)
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_JSONLD_TYPE_TO_PAGE: dict[str, str] = {
    "Product": "product_detail",
    "IndividualProduct": "product_detail",
    "NewsArticle": "news",
    "Article": "article",
    "ReportageNewsArticle": "news",
    "BlogPosting": "article",
    "FAQPage": "help_faq",
    "ContactPage": "form",
    "CheckoutPage": "checkout",
    "Event": "landing",
    "MusicEvent": "landing",
    "SportsEvent": "landing",
    "TheaterEvent": "landing",
    "BusinessEvent": "landing",
    "EducationEvent": "landing",
    "Festival": "landing",
    "ExhibitionEvent": "landing",
    "LocalBusiness": "landing",
    "Restaurant": "landing",
    "Hotel": "landing",
    "Store": "landing",
    "MedicalClinic": "landing",
    "FoodEstablishment": "landing",
    "HealthAndBeautyBusiness": "landing",
    "AutoRepair": "landing",
    "Dentist": "landing",
    "RealEstateAgent": "landing",
}


def _resolve_jsonld_page_type(data: Any) -> str | None:
    """Recursively find @type in JSON-LD and map to page type."""
    if isinstance(data, list):
        return next((r for item in data if (r := _resolve_jsonld_page_type(item))), None)
    if not isinstance(data, dict):
        return None
    if "@graph" in data:
        return _resolve_jsonld_page_type(data["@graph"])
    t = data.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return next(
        (_JSONLD_TYPE_TO_PAGE[x] for x in types if x in _JSONLD_TYPE_TO_PAGE),
        None,
    )


def _detect_jsonld_page_type(raw_html: str) -> str | None:
    """Sniff JSON-LD @type from raw HTML and return page type or None."""
    for m in _JSONLD_RE.finditer(raw_html):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        result = _resolve_jsonld_page_type(data)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Thresholds per page type
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, int] = {
    # Legacy types — low thresholds for backward compat (single URL signal suffices)
    "product_detail": 20,
    "search_results": 20,
    "article": 20,
    "news": 20,
    "listing": 20,
    # Strong-signal new types (easy to confirm)
    "login": 20,
    "checkout": 20,
    "error": 25,
    # Medium-signal new types
    "help_faq": 20,
    "documentation": 20,
    "form": 20,
    "dashboard": 20,
    "settings": 20,
    "landing": 25,
    # Anti-bot / captcha / WAF block pages
    "blocked": 20,
}

_DEFAULT_THRESHOLD = 50

# Maximum positive DOM contribution per page type.  Dashboard DOM signals
# can accumulate up to 70 pts, overwhelming URL+JSON-LD for most types.
# Capping at 40 keeps DOM influential but not dominant.
_DOM_CAP = 40

# ---------------------------------------------------------------------------
# Signal Registry — URL signals
# ---------------------------------------------------------------------------

_URL_SIGNALS: list[SignalDef] = [
    # ---- product_detail ----
    SignalDef("url_vp_products", {"product_detail": 25}, check_url=lambda u: "/vp/products/" in u),
    SignalDef("url_products", {"product_detail": 20}, check_url=lambda u: "/products/" in u),
    SignalDef("url_goods", {"product_detail": 20}, check_url=lambda u: "/good" in u and "/goodbye" not in u),
    SignalDef("url_catalog", {"product_detail": 20}, check_url=lambda u: "/catalog/" in u),
    SignalDef("url_item", {"product_detail": 20}, check_url=lambda u: "/item/" in u),
    SignalDef("url_product_slash", {"product_detail": 25}, check_url=lambda u: "/product/" in u),
    SignalDef("url_product_dot", {"product_detail": 25}, check_url=lambda u: "/product." in u),
    SignalDef("url_dp", {"product_detail": 20}, check_url=lambda u: "/dp/" in u),
    SignalDef(
        "url_amazon_dp",
        {"product_detail": 25},
        check_url=lambda u: "/dp/" in u and "amazon." in u,
    ),
    SignalDef(
        "url_nike_t",
        {"product_detail": 15, "listing": -5},
        check_url=lambda u: "/t/" in u and ("nike.com" in u or "nike." in u),
    ),
    SignalDef("url_product_detail_kw", {"product_detail": 20}, check_url=lambda u: "/productdetail" in u),
    # ---- search_results ----
    SignalDef("url_search", {"search_results": 25, "listing": -10}, check_url=lambda u: "/search" in u),
    SignalDef("url_q_param", {"search_results": 25}, check_url=lambda u: "?q=" in u or "&q=" in u),
    SignalDef("url_query_param", {"search_results": 25}, check_url=lambda u: "?query=" in u or "&query=" in u),
    SignalDef("url_keyword_param", {"search_results": 25}, check_url=lambda u: "?keyword=" in u or "&keyword=" in u),
    SignalDef("url_browse", {"search_results": 20}, check_url=lambda u: "/browse" in u),
    SignalDef("url_searchterm", {"search_results": 25}, check_url=lambda u: "?searchterm=" in u or "&searchterm=" in u),
    # ---- article ----
    SignalDef("url_article", {"article": 25, "news": 5}, check_url=lambda u: "/article/" in u or "/articles/" in u),
    SignalDef("url_wiki", {"article": 30}, check_url=lambda u: "/wiki/" in u),
    SignalDef(
        "url_wikipedia_domain",
        {"article": 15, "dashboard": -15},
        check_url=lambda u: "wikipedia.org" in u,
    ),
    SignalDef("url_blog", {"article": 25}, check_url=lambda u: "/blog/" in u),
    SignalDef("url_post", {"article": 20}, check_url=lambda u: "/post/" in u),
    # ---- news ----
    SignalDef("url_news", {"news": 25, "article": 10}, check_url=lambda u: "/news/" in u),
    # ---- listing ----
    SignalDef("url_list", {"listing": 20}, check_url=lambda u: "/list" in u and "/listing" not in u),
    SignalDef("url_ranking", {"listing": 20}, check_url=lambda u: "/ranking" in u),
    SignalDef("url_best", {"listing": 20}, check_url=lambda u: "/best" in u),
    SignalDef("url_category", {"listing": 25}, check_url=lambda u: "/category/" in u or "/categories/" in u),
    SignalDef("url_nike_w", {"listing": 20}, check_url=lambda u: "/w/" in u and ("nike.com" in u or "nike." in u)),
    SignalDef(
        "url_gender_path",
        {"listing": 20, "product_detail": -5},
        check_url=lambda u: any(p in u for p in ("/men/", "/women/", "/man/", "/woman/", "/men.", "/women.")),
    ),
    # ---- login ----
    SignalDef(
        "url_login", {"login": 25, "form": -10}, check_url=lambda u: "/login" in u or "/signin" in u or "/sign-in" in u
    ),
    SignalDef("url_auth", {"login": 20}, check_url=lambda u: "/auth" in u and "/author" not in u),
    # ---- checkout ----
    SignalDef("url_checkout", {"checkout": 25, "product_detail": -10}, check_url=lambda u: "/checkout" in u),
    SignalDef("url_payment", {"checkout": 25}, check_url=lambda u: "/payment" in u),
    SignalDef("url_order", {"checkout": 20}, check_url=lambda u: "/order" in u and "/orders" not in u),
    # ---- form ----
    SignalDef(
        "url_register",
        {"form": 20, "login": -10},
        check_url=lambda u: "/register" in u or "/signup" in u or "/sign-up" in u,
    ),
    SignalDef("url_contact", {"form": 20}, check_url=lambda u: "/contact" in u),
    SignalDef("url_apply", {"form": 20}, check_url=lambda u: "/apply" in u),
    # ---- dashboard ----
    SignalDef("url_dashboard", {"dashboard": 20}, check_url=lambda u: "/dashboard" in u),
    SignalDef("url_admin", {"dashboard": 20}, check_url=lambda u: "/admin" in u),
    SignalDef("url_analytics", {"dashboard": 20}, check_url=lambda u: "/analytics" in u),
    # ---- help_faq ----
    SignalDef("url_faq", {"help_faq": 20, "article": -10}, check_url=lambda u: "/faq" in u),
    SignalDef("url_help", {"help_faq": 20}, check_url=lambda u: "/help" in u),
    SignalDef("url_support", {"help_faq": 20}, check_url=lambda u: "/support" in u),
    # ---- settings ----
    SignalDef(
        "url_settings", {"settings": 20, "form": -10}, check_url=lambda u: "/settings" in u or "/preferences" in u
    ),
    SignalDef("url_profile_edit", {"settings": 20}, check_url=lambda u: "/profile/edit" in u or "/account/edit" in u),
    # ---- error ----
    SignalDef("url_404", {"error": 15}, check_url=lambda u: "/404" in u),
    SignalDef("url_error", {"error": 15}, check_url=lambda u: "/error" in u),
    # ---- documentation ----
    SignalDef(
        "url_docs", {"documentation": 20, "article": -5}, check_url=lambda u: "/docs" in u or "/documentation" in u
    ),
    SignalDef("url_api_ref", {"documentation": 25}, check_url=lambda u: "/api-reference" in u or "/api-docs" in u),
    # ---- landing ----
    SignalDef("url_root", {"landing": 30, "listing": -10}, check_url=lambda u: _is_root_url(u)),
    # ---- blocked (captcha/WAF) ----
    SignalDef("url_sorry", {"blocked": 30}, check_url=lambda u: "/sorry/" in u),
    SignalDef("url_captcha", {"blocked": 25, "error": -10}, check_url=lambda u: "/captcha" in u),
    SignalDef(
        "url_challenge", {"blocked": 25, "error": -10}, check_url=lambda u: "/challenge" in u and "/challenges" not in u
    ),
    SignalDef(
        "url_cf_verify",
        {"blocked": 30},
        check_url=lambda u: "challenge-platform" in u or "cf-browser-verification" in u,
    ),
    SignalDef("url_edgesuite", {"blocked": 30}, check_url=lambda u: "errors.edgesuite.net" in u),
]

# ---------------------------------------------------------------------------
# Signal Registry — Meta signals (raw HTML regex, <5ms)
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_TYPE_RE = re.compile(r'property=["\']og:type["\'][^>]*content=["\']([^"\']+)', re.IGNORECASE)

_META_SIGNALS: list[SignalDef] = [
    # ---- login ----
    SignalDef(
        "meta_title_login",
        {"login": 15},
        check_meta=lambda h: _title_contains(
            h, ("login", "sign in", "log in", "로그인", "ログイン", "se connecter", "anmelden")
        ),
    ),
    # ---- error ----
    SignalDef(
        "meta_title_error",
        {"error": 35},
        check_meta=lambda h: _title_contains(
            h, ("404", "500", "not found", "page not found", "페이지를 찾을 수 없", "ページが見つかりません")
        ),
    ),
    # ---- help_faq ----
    SignalDef(
        "meta_title_faq",
        {"help_faq": 15},
        check_meta=lambda h: _title_contains(
            h, ("faq", "frequently asked", "자주 묻는 질문", "よくある質問", "help center", "도움말")
        ),
    ),
    # ---- og:type ----
    SignalDef("meta_og_article", {"article": 20}, check_meta=lambda h: _og_type_is(h, "article")),
    # ---- blocked (captcha/WAF) ----
    SignalDef(
        "meta_title_blocked",
        {"blocked": 30, "error": -15},
        check_meta=lambda h: _title_contains(
            h,
            (
                "access denied",
                "attention required",
                "please verify",
                "just a moment",
                "you have been blocked",
                "접근이 거부",
                "アクセスが拒否",
            ),
        ),
    ),
]

# JSON-LD weight map — parsed once per page, not per-signal
_JSONLD_WEIGHTS: dict[str, int] = {
    "product_detail": 40,
    "news": 40,
    "article": 40,
    "help_faq": 40,
    "form": 35,
    "checkout": 40,
}

# ---------------------------------------------------------------------------
# Signal Registry — DOM signals (raw HTML regex, <30ms)
# ---------------------------------------------------------------------------

_DOM_SIGNALS: list[SignalDef] = [
    # ---- login ----
    SignalDef(
        "dom_password_input",
        {"login": 30, "form": -15, "settings": -10},
        check_dom=lambda h: 'type="password"' in h or "type='password'" in h,
    ),
    SignalDef(
        "dom_remember_me", {"login": 20}, check_dom=lambda h: "remember" in h and ("checkbox" in h or "check" in h)
    ),
    SignalDef(
        "dom_single_form_password",
        {"login": 10},
        check_dom=lambda h: h.count("<form") == 1 and ('type="password"' in h or "type='password'" in h),
    ),
    # ---- checkout ----
    SignalDef(
        "dom_cc_fields",
        {"checkout": 30, "form": -10},
        check_dom=lambda h: 'autocomplete="cc-' in h or "autocomplete='cc-" in h,
    ),
    SignalDef(
        "dom_shipping_fields",
        {"checkout": 20},
        check_dom=lambda h: any(
            kw in h for kw in ('autocomplete="shipping', "autocomplete='shipping", 'name="shipping', "name='shipping")
        ),
    ),
    SignalDef(
        "dom_step_indicator",
        {"checkout": 10},
        check_dom=lambda h: "step" in h and ("progress" in h or "stepper" in h or "step-indicator" in h),
    ),
    # ---- form (not login) ----
    SignalDef(
        "dom_many_fields_no_password",
        {"form": 25, "login": -20},
        check_dom=lambda h: h.count("<input") > 5 and 'type="password"' not in h and "type='password'" not in h,
    ),
    SignalDef("dom_textarea", {"form": 15}, check_dom=lambda h: "<textarea" in h),
    SignalDef("dom_fieldset", {"form": 20}, check_dom=lambda h: h.count("<fieldset") >= 2),
    # ---- dashboard ----
    SignalDef("dom_many_tables", {"dashboard": 25, "article": -10}, check_dom=lambda h: h.count("<table") >= 2),
    SignalDef("dom_chart_elements", {"dashboard": 25}, check_dom=lambda h: h.count("<canvas") + h.count("<svg") >= 3),
    SignalDef(
        "dom_sidebar_nav",
        {"dashboard": 20},
        check_dom=lambda h: 'role="navigation"' in h and ("sidebar" in h or "side-nav" in h or "sidenav" in h),
    ),
    # ---- help_faq ----
    SignalDef("dom_details_elements", {"help_faq": 30, "article": -10}, check_dom=lambda h: h.count("<details") >= 3),
    SignalDef(
        "dom_qa_pattern",
        {"help_faq": 20},
        check_dom=lambda h: h.count("question") >= 3 or h.count("faq-item") >= 2 or h.count("accordion") >= 2,
    ),
    # ---- settings ----
    SignalDef("dom_switch_role", {"settings": 15, "form": -10, "login": -15}, check_dom=lambda h: 'role="switch"' in h),
    SignalDef("dom_many_selects", {"settings": 10}, check_dom=lambda h: h.count("<select") >= 3),
    # ---- error ----
    SignalDef("dom_very_short_content", {"error": 20}, check_dom=lambda h: _stripped_text_length(h) < 200),
    SignalDef(
        "dom_not_found_text",
        {"error": 25},
        check_dom=lambda h: any(
            kw in h
            for kw in (
                "page not found",
                "페이지를 찾을 수 없",
                "ページが見つかりません",
                "page introuvable",
                "seite nicht gefunden",
            )
        ),
    ),
    # ---- documentation ----
    SignalDef(
        "dom_code_blocks",
        {"documentation": 30, "article": -5},
        check_dom=lambda h: h.count("<code") + h.count("<pre") >= 3,
    ),
    SignalDef("dom_toc_sidebar", {"documentation": 25}, check_dom=lambda h: _has_toc_sidebar(h)),
    SignalDef(
        "dom_version_selector",
        {"documentation": 15},
        check_dom=lambda h: "version" in h and ("<select" in h or "dropdown" in h),
    ),
    # ---- article (MediaWiki sites) ----
    SignalDef(
        "dom_mw_content",
        {"article": 25, "dashboard": -20},
        check_dom=lambda h: "mw-content-text" in h or "mw-parser-output" in h,
    ),
    # ---- landing ----
    SignalDef(
        "dom_hero_cta",
        {"landing": 20, "article": -10, "listing": -10},
        check_dom=lambda h: (
            ("hero" in h or "jumbotron" in h)
            and ("cta" in h or "call-to-action" in h or "get-started" in h or "sign-up" in h)
        ),
    ),
    SignalDef("dom_many_sections", {"landing": 15}, check_dom=lambda h: h.count("<section") >= 5),
    # ---- product_detail (cart/buy keywords) ----
    SignalDef(
        "dom_add_to_cart",
        {"product_detail": 20},
        check_dom=lambda h: any(
            kw in h
            for kw in (
                "add to cart",
                "add to bag",
                "add to basket",
                "buy now",
                "장바구니",
                "카트에 담기",
                "구매하기",
                "바로구매",
                "カートに入れる",
                "今すぐ買う",
                "ajouter au panier",
                "in den warenkorb",
                "加入购物车",
                "立即购买",
                "añadir al carrito",
                "comprar ahora",
            )
        ),
    ),
    # ---- blocked (captcha/WAF) ----
    # Cloudflare, reCAPTCHA, hCaptcha, Turnstile
    SignalDef(
        "dom_captcha_element",
        {"blocked": 30, "error": -10},
        check_dom=lambda h: any(
            kw in h
            for kw in (
                "g-recaptcha",
                "h-captcha",
                "cf-turnstile",
                "challenge-form",
                "captcha-container",
            )
        ),
    ),
    # Modern providers: DataDome, PerimeterX/HUMAN, Imperva
    SignalDef(
        "dom_modern_antibot",
        {"blocked": 25},
        check_dom=lambda h: any(
            kw in h
            for kw in (
                "datadome",
                "px-captcha",
                "human-challenge",
                "incapsula",
                "_incap_",
            )
        ),
    ),
    # Short "Access Denied" pages (WAF)
    SignalDef(
        "dom_blocked_short",
        {"blocked": 35, "error": -10},
        check_dom=lambda h: (
            _stripped_text_length(h) < 2000
            and any(kw in h for kw in ("access denied", "access blocked", "forbidden", "접근이 거부", "アクセスが拒否"))
        ),
    ),
    # Cloudflare challenge DOM markers
    SignalDef(
        "dom_cf_challenge",
        {"blocked": 35},
        check_dom=lambda h: any(
            kw in h
            for kw in (
                "cf-browser-verification",
                "challenge-platform",
                "cf-chl-bypass",
                "challenge-running",
            )
        ),
    ),
    # Cloudflare "Just a moment" interstitial
    SignalDef(
        "dom_just_a_moment",
        {"blocked": 30},
        check_dom=lambda h: "just a moment" in h and _stripped_text_length(h) < 2000,
    ),
]

# Combined registry for iteration
SIGNAL_REGISTRY: list[SignalDef] = _URL_SIGNALS + _META_SIGNALS + _DOM_SIGNALS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_root_url(url: str) -> bool:
    """Check if URL is a site root (/ or /index.*)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return path == "" or path.startswith("/index")


_TOC_RE = re.compile(r'(?:class|id)=["\'][^"\']*\btoc\b[^"\']*["\']', re.IGNORECASE)


def _has_toc_sidebar(h: str) -> bool:
    """Check for TOC + sidebar pattern without false positives on 'protocol' etc."""
    has_sidebar = "sidebar" in h or "side-nav" in h or "sidenav" in h
    if not has_sidebar:
        return False
    return "table-of-contents" in h or bool(_TOC_RE.search(h))


def _title_contains(raw_html: str, terms: tuple[str, ...]) -> bool:
    """Check if <title> contains any of the given terms (case-insensitive)."""
    m = _TITLE_RE.search(raw_html)
    if not m:
        return False
    title = m.group(1).lower()
    return any(t in title for t in terms)


def _og_type_is(raw_html: str, expected: str) -> bool:
    """Check if og:type meta tag matches expected value."""
    m = _OG_TYPE_RE.search(raw_html)
    return m is not None and m.group(1).lower() == expected.lower()


_TAG_RE = re.compile(r"<[^>]+>")


def _stripped_text_length(raw_html: str) -> int:
    """Approximate visible text length by stripping tags."""
    text = _TAG_RE.sub("", raw_html)
    return len(text.strip())


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------


def classify_page(url: str, raw_html: str | None = None) -> ClassificationResult:
    """3-layer weighted voting page classifier with short-circuit.

    Args:
        url: page URL (always available)
        raw_html: full page HTML (optional — enables meta + DOM signals)

    Returns:
        ClassificationResult with page_type, confidence, score, signals
    """
    url_lower = url.lower()
    scores: dict[str, int] = {}
    fired: list[str] = []

    # Layer 1: URL signals
    for sig in _URL_SIGNALS:
        if sig.check_url and sig.check_url(url_lower):
            fired.append(sig.name)
            for ptype, weight in sig.scores.items():
                scores[ptype] = scores.get(ptype, 0) + weight

    # Short-circuit: if top score > threshold×2, skip layers 2-3
    can_short_circuit = False
    if scores:
        top_type = max(scores, key=scores.get)  # type: ignore[arg-type]
        top_score = scores[top_type]
        threshold = THRESHOLDS.get(top_type, _DEFAULT_THRESHOLD)
        if top_score > threshold * 2:
            can_short_circuit = True

    # Layers 2-3: Meta + DOM signals (only if raw_html provided and no short-circuit)
    if raw_html is not None and not can_short_circuit:
        html_lower = raw_html.lower()

        # Layer 2a: Meta signals — use ORIGINAL html (JSON-LD @type is case-sensitive)
        for sig in _META_SIGNALS:
            if sig.check_meta and sig.check_meta(raw_html):
                fired.append(sig.name)
                for ptype, weight in sig.scores.items():
                    scores[ptype] = scores.get(ptype, 0) + weight

        # Layer 2b: JSON-LD — parse once, apply weight to detected type
        jsonld_type = _detect_jsonld_page_type(raw_html)
        if jsonld_type and jsonld_type in _JSONLD_WEIGHTS:
            fired.append(f"meta_jsonld_{jsonld_type}")
            scores[jsonld_type] = scores.get(jsonld_type, 0) + _JSONLD_WEIGHTS[jsonld_type]

        # Layer 3: DOM signals — use lowered html
        dom_pos: dict[str, int] = {}
        for sig in _DOM_SIGNALS:
            if sig.check_dom and sig.check_dom(html_lower):
                fired.append(sig.name)
                for ptype, weight in sig.scores.items():
                    scores[ptype] = scores.get(ptype, 0) + weight
                    if weight > 0:
                        dom_pos[ptype] = dom_pos.get(ptype, 0) + weight

        # Clamp excess positive DOM contribution per type
        for ptype, total in dom_pos.items():
            if total > _DOM_CAP:
                scores[ptype] -= total - _DOM_CAP

    elif raw_html is not None and can_short_circuit:
        # Even when short-circuiting, always check blocked signals (safety override).
        # Captcha/WAF pages can appear on any URL pattern (e.g. search, product).
        html_lower = raw_html.lower()
        for sig in _META_SIGNALS:
            if sig.check_meta and "blocked" in sig.scores and sig.check_meta(raw_html):
                fired.append(sig.name)
                for ptype, weight in sig.scores.items():
                    scores[ptype] = scores.get(ptype, 0) + weight
        dom_pos_blocked: dict[str, int] = {}
        for sig in _DOM_SIGNALS:
            if sig.check_dom and "blocked" in sig.scores and sig.check_dom(html_lower):
                fired.append(sig.name)
                for ptype, weight in sig.scores.items():
                    scores[ptype] = scores.get(ptype, 0) + weight
                    if weight > 0:
                        dom_pos_blocked[ptype] = dom_pos_blocked.get(ptype, 0) + weight
        for ptype, total in dom_pos_blocked.items():
            if total > _DOM_CAP:
                scores[ptype] -= total - _DOM_CAP

    # Determine winner
    if not scores:
        return ClassificationResult(
            page_type="unknown",
            confidence=0.0,
            score=0,
            signals=tuple(fired),
            runner_up=None,
            runner_up_score=0,
        )

    # Sort by score descending
    sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    winner_type, winner_score = sorted_types[0]

    # Check threshold
    threshold = THRESHOLDS.get(winner_type, _DEFAULT_THRESHOLD)
    if winner_score < threshold:
        # Below threshold — return unknown
        runner_up = sorted_types[1][0] if len(sorted_types) > 1 else None
        runner_up_score = sorted_types[1][1] if len(sorted_types) > 1 else 0
        return ClassificationResult(
            page_type="unknown",
            confidence=min(1.0, winner_score / (threshold * 2)) if threshold else 0.0,
            score=winner_score,
            signals=tuple(fired),
            runner_up=runner_up,
            runner_up_score=runner_up_score,
        )

    # Confidence: min(1.0, score / (threshold × 2))
    confidence = min(1.0, winner_score / (threshold * 2)) if threshold else 1.0

    # Runner-up
    runner_up = sorted_types[1][0] if len(sorted_types) > 1 else None
    runner_up_score = sorted_types[1][1] if len(sorted_types) > 1 else 0

    return ClassificationResult(
        page_type=winner_type,
        confidence=confidence,
        score=winner_score,
        signals=tuple(fired),
        runner_up=runner_up,
        runner_up_score=runner_up_score,
    )
