# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for P7.1 weighted-voting page classifier."""

from __future__ import annotations

import pytest

from pagemap.page_classifier import ClassificationResult, classify_page
from pagemap.page_map_builder import detect_page_type

# ---------------------------------------------------------------------------
# Phase A: Backward compatibility — existing URL patterns produce same results
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing URL patterns must return the same page_type as the old waterfall."""

    CASES = [
        ("https://www.coupang.com/vp/products/1234", "product_detail"),
        ("https://www.29cm.co.kr/products/1234", "product_detail"),
        ("https://www.musinsa.com/goods/1234", "product_detail"),
        (
            "https://www.cos.com/ko-kr/women/denim-edit/product.facade-straight-leg-jeans-dusty-blue.1205065015.html",
            "product_detail",
        ),
        ("https://www.zara.com/kr/ko/search?searchTerm=jacket", "search_results"),
        ("https://www.google.com/search?q=python", "search_results"),
        ("https://en.wikipedia.org/wiki/Python", "article"),
        ("https://medium.com/blog/my-post", "article"),
        ("https://www.nike.com/kr/w/men", "listing"),
        ("https://www.coupang.com/np/categories/1234", "listing"),
        ("https://unknown-site.com/page", "unknown"),
    ]

    @pytest.mark.parametrize("url,expected", CASES, ids=[c[0].split("//")[1][:40] for c in CASES])
    def test_url_only(self, url: str, expected: str):
        assert classify_page(url).page_type == expected

    @pytest.mark.parametrize("url,expected", CASES, ids=[c[0].split("//")[1][:40] for c in CASES])
    def test_wrapper_compat(self, url: str, expected: str):
        """detect_page_type wrapper returns same result."""
        assert detect_page_type(url) == expected


class TestCOSProductVsListing:
    """COS URLs with /product. should be product_detail, not listing."""

    def test_cos_product(self):
        url = "https://www.cos.com/ko-kr/women/denim-edit/product.facade-straight-leg-jeans-dusty-blue.1205065015.html"
        result = classify_page(url)
        assert result.page_type == "product_detail"
        # Should have product signals fired, not listing
        assert any("product" in s for s in result.signals)

    def test_cos_listing(self):
        url = "https://www.cos.com/ko-kr/women/denim-edit/"
        result = classify_page(url)
        assert result.page_type == "listing"


# ---------------------------------------------------------------------------
# Phase A: Order independence
# ---------------------------------------------------------------------------


class TestOrderIndependence:
    """Results must not depend on signal evaluation order."""

    def test_same_result_regardless_of_url_format(self):
        """Different orderings of URL segments should produce same result."""
        # Both have /women/ and /product. — product_detail should win
        url1 = "https://www.cos.com/women/product.abc.html"
        url2 = "https://www.cos.com/product.abc/women/thing.html"
        r1 = classify_page(url1)
        r2 = classify_page(url2)
        assert r1.page_type == r2.page_type == "product_detail"


# ---------------------------------------------------------------------------
# Phase A: Short-circuit
# ---------------------------------------------------------------------------


class TestShortCircuit:
    """High-confidence URL signals should produce correct results without raw_html."""

    def test_strong_product_url(self):
        """Multiple product URL signals → product_detail without HTML."""
        url = "https://shop.com/vp/products/12345"
        result = classify_page(url, raw_html=None)
        assert result.page_type == "product_detail"
        assert result.confidence > 0.0

    def test_strong_search_url(self):
        url = "https://shop.com/search?q=shoes&keyword=running"
        result = classify_page(url, raw_html=None)
        assert result.page_type == "search_results"

    def test_url_only_matches_full_classify(self):
        """URL-only classification should match when raw_html adds no new info."""
        url = "https://example.com/vp/products/1234"
        r_url = classify_page(url)
        r_html = classify_page(url, raw_html="<html><body>No special signals</body></html>")
        assert r_url.page_type == r_html.page_type


# ---------------------------------------------------------------------------
# Phase A: Negative signals
# ---------------------------------------------------------------------------


class TestNegativeSignals:
    """Negative weights resolve ambiguity between similar types."""

    def test_password_input_means_login_not_form(self):
        """Page with password input → login, not form."""
        url = "https://example.com/account"
        html = '<html><body><form><input type="text" name="email"><input type="password" name="pw"><button>Sign in</button></form></body></html>'
        result = classify_page(url, raw_html=html)
        assert result.page_type == "login"

    def test_many_fields_no_password_means_form(self):
        """Page with many fields but no password → form, not login."""
        url = "https://example.com/contact"
        html = """<html><body><form>
            <input type="text" name="name">
            <input type="email" name="email">
            <input type="text" name="phone">
            <input type="text" name="company">
            <input type="text" name="subject">
            <input type="text" name="city">
            <textarea name="message"></textarea>
            <button>Submit</button>
        </form></body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "form"
        # Should NOT be login
        assert result.page_type != "login"

    def test_faq_not_article(self):
        """/faq + details elements → help_faq, not article."""
        url = "https://example.com/faq"
        html = """<html><body>
            <h1>FAQ</h1>
            <details><summary>Q1?</summary>Answer 1</details>
            <details><summary>Q2?</summary>Answer 2</details>
            <details><summary>Q3?</summary>Answer 3</details>
            <details><summary>Q4?</summary>Answer 4</details>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "help_faq"


# ---------------------------------------------------------------------------
# Phase B: New page types
# ---------------------------------------------------------------------------


class TestNewPageTypes:
    """Each new page type is detectable via URL, DOM, or both."""

    def test_login_url(self):
        result = classify_page("https://example.com/login")
        assert result.page_type == "login"

    def test_signin_url(self):
        result = classify_page("https://example.com/signin")
        assert result.page_type == "login"

    def test_checkout_url(self):
        result = classify_page("https://shop.com/checkout")
        assert result.page_type == "checkout"

    def test_checkout_payment_url(self):
        result = classify_page("https://shop.com/payment")
        assert result.page_type == "checkout"

    def test_form_register_url(self):
        result = classify_page("https://example.com/register")
        assert result.page_type == "form"

    def test_form_signup_url(self):
        result = classify_page("https://example.com/signup")
        assert result.page_type == "form"

    def test_dashboard_url(self):
        result = classify_page("https://app.example.com/dashboard")
        assert result.page_type == "dashboard"

    def test_help_faq_url(self):
        result = classify_page("https://example.com/faq")
        assert result.page_type == "help_faq"

    def test_settings_url(self):
        result = classify_page("https://example.com/settings")
        assert result.page_type == "settings"

    def test_error_via_dom(self):
        """Error page detected via title + short content."""
        url = "https://example.com/unknown-page"
        html = "<html><head><title>404 Not Found</title></head><body><h1>Page not found</h1></body></html>"
        result = classify_page(url, raw_html=html)
        assert result.page_type == "error"

    def test_documentation_url(self):
        result = classify_page("https://docs.example.com/docs/getting-started")
        assert result.page_type == "documentation"

    def test_documentation_api_ref(self):
        result = classify_page("https://example.com/api-reference/endpoints")
        assert result.page_type == "documentation"

    def test_landing_root_url(self):
        result = classify_page("https://www.example.com/")
        assert result.page_type == "landing"

    def test_landing_root_no_slash(self):
        result = classify_page("https://www.example.com")
        assert result.page_type == "landing"


class TestNewPageTypesWithDOM:
    """DOM signals improve classification of new types."""

    def test_login_with_password(self):
        url = "https://example.com/login"
        html = '<html><body><form><input type="password" name="pw"></form></body></html>'
        result = classify_page(url, raw_html=html)
        assert result.page_type == "login"
        assert result.confidence > 0.5

    def test_checkout_with_cc_fields(self):
        url = "https://shop.com/checkout"
        html = '<html><body><form><input autocomplete="cc-number"><input autocomplete="cc-exp"></form></body></html>'
        result = classify_page(url, raw_html=html)
        assert result.page_type == "checkout"

    def test_dashboard_with_tables_charts(self):
        url = "https://app.com/dashboard"
        html = """<html><body>
            <table><tr><td>Metric 1</td></tr></table>
            <table><tr><td>Metric 2</td></tr></table>
            <canvas id="chart1"></canvas>
            <svg id="chart2"></svg>
            <svg id="chart3"></svg>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "dashboard"

    def test_documentation_with_code_blocks(self):
        url = "https://docs.example.com/docs/api"
        html = """<html><body>
            <div class="sidebar toc">Table of Contents</div>
            <code>example 1</code>
            <pre>example 2</pre>
            <code>example 3</code>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "documentation"

    def test_settings_with_switches(self):
        url = "https://example.com/settings"
        html = """<html><body>
            <div role="switch" aria-label="Dark mode">On</div>
            <select><option>English</option></select>
            <select><option>UTC</option></select>
            <select><option>Default</option></select>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "settings"


# ---------------------------------------------------------------------------
# Phase B: Ambiguity resolution
# ---------------------------------------------------------------------------


class TestAmbiguityResolution:
    """Conflicting signals resolved via negative weights."""

    def test_ecommerce_login(self):
        """/login on e-commerce site → login, not product_detail."""
        url = "https://www.coupang.com/login"
        result = classify_page(url)
        assert result.page_type == "login"

    def test_faq_with_articles(self):
        """/faq with article-like content → help_faq, not article."""
        url = "https://example.com/faq"
        html = """<html><body>
            <h1>Frequently Asked Questions</h1>
            <details><summary>How do I return?</summary>Answer</details>
            <details><summary>What's your policy?</summary>Answer</details>
            <details><summary>Where do I ship?</summary>Answer</details>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "help_faq"

    def test_docs_vs_article(self):
        """/docs with code blocks → documentation, not article."""
        url = "https://example.com/docs/guide"
        html = """<html><body>
            <div class="sidebar table-of-contents">TOC</div>
            <code>import foo</code>
            <pre>def bar(): pass</pre>
            <code>class Baz: ...</code>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "documentation"


# ---------------------------------------------------------------------------
# Phase B: Confidence scoring
# ---------------------------------------------------------------------------


class TestConfidence:
    """Confidence values are in valid range and meaningful."""

    def test_confidence_range(self):
        """Confidence is always 0.0–1.0."""
        urls = [
            "https://example.com/login",
            "https://example.com/checkout",
            "https://example.com/unknown-random",
            "https://example.com/",
        ]
        for url in urls:
            result = classify_page(url)
            assert 0.0 <= result.confidence <= 1.0, f"Confidence out of range for {url}"

    def test_strong_signal_high_confidence(self):
        """Strong URL + DOM signals → high confidence."""
        url = "https://example.com/login"
        html = '<html><body><form><input type="password"><div class="remember">Remember me <input type="checkbox"></div></form></body></html>'
        result = classify_page(url, raw_html=html)
        assert result.page_type == "login"
        assert result.confidence >= 0.5

    def test_unknown_low_confidence(self):
        """Unknown pages have 0 confidence."""
        result = classify_page("https://example.com/random-page-xyz")
        assert result.page_type == "unknown"
        assert result.confidence == 0.0

    def test_runner_up_present(self):
        """Ambiguous cases should have runner_up info."""
        # /news/ triggers both news and article signals
        url = "https://example.com/news/article/12345"
        result = classify_page(url)
        assert result.page_type in ("news", "article")
        assert result.runner_up is not None


# ---------------------------------------------------------------------------
# Phase B: ClassificationResult structure
# ---------------------------------------------------------------------------


class TestClassificationResult:
    """ClassificationResult has correct structure."""

    def test_fields_present(self):
        result = classify_page("https://example.com/login")
        assert isinstance(result, ClassificationResult)
        assert isinstance(result.page_type, str)
        assert isinstance(result.confidence, float)
        assert isinstance(result.score, int)
        assert isinstance(result.signals, tuple)
        assert result.runner_up is None or isinstance(result.runner_up, str)
        assert isinstance(result.runner_up_score, int)

    def test_signals_are_strings(self):
        result = classify_page("https://example.com/login")
        for sig in result.signals:
            assert isinstance(sig, str)

    def test_frozen(self):
        """ClassificationResult is frozen (immutable)."""
        result = classify_page("https://example.com/login")
        with pytest.raises(AttributeError):
            result.page_type = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JSON-LD meta signal tests
# ---------------------------------------------------------------------------


class TestJSONLDSignals:
    """JSON-LD @type contributes to classification."""

    def test_product_jsonld(self):
        url = "https://unknown-shop.com/item/123"
        html = """<html><head><script type="application/ld+json">{"@type": "Product", "name": "Shoes"}</script></head><body>Content</body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "product_detail"

    def test_faq_jsonld(self):
        url = "https://example.com/help"
        html = """<html><head><script type="application/ld+json">{"@type": "FAQPage", "mainEntity": []}</script></head><body>Content</body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "help_faq"

    def test_news_jsonld(self):
        url = "https://news.example.com/story/123"
        body_text = "This is a full news article with enough content to avoid being classified as an error page. " * 5
        html = f"""<html><head><script type="application/ld+json">{{"@type": "NewsArticle", "headline": "Breaking"}}</script></head><body><article><p>{body_text}</p></article></body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "news"


# ---------------------------------------------------------------------------
# C4 regression: Wikipedia page type (article, not dashboard)
# ---------------------------------------------------------------------------


class TestWikipediaClassification:
    """C4: Wikipedia pages must be classified as 'article', not 'dashboard'."""

    def test_wikipedia_url_only(self):
        """Wikipedia URL-only → article (high confidence, short-circuit)."""
        url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
        result = classify_page(url)
        assert result.page_type == "article"
        # Should short-circuit (score > threshold * 2)
        assert result.score > 40

    def test_wikipedia_with_dashboard_like_html(self):
        """Wikipedia URL + dashboard-like DOM signals → still article."""
        url = "https://en.wikipedia.org/wiki/Python_(programming_language)"
        # Simulate Wikipedia HTML with multiple tables, sidebar nav, many sections
        html = """<html><body>
            <nav role="navigation"><div class="sidebar">Navigation</div></nav>
            <div id="mw-content-text" class="mw-parser-output">
                <table><tr><td>Infobox</td></tr></table>
                <table><tr><td>Comparison table</td></tr></table>
                <section>Section 1</section>
                <section>Section 2</section>
                <section>Section 3</section>
                <section>Section 4</section>
                <section>Section 5</section>
            </div>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "article"

    def test_wikipedia_ko(self):
        """Korean Wikipedia → article."""
        url = "https://ko.wikipedia.org/wiki/파이썬"
        result = classify_page(url)
        assert result.page_type == "article"

    def test_wikipedia_ja(self):
        """Japanese Wikipedia → article."""
        url = "https://ja.wikipedia.org/wiki/Python"
        result = classify_page(url)
        assert result.page_type == "article"

    def test_non_wikipedia_wiki_still_works(self):
        """Non-Wikipedia /wiki/ URL → article from url_wiki signal."""
        url = "https://company.com/wiki/internal-doc"
        result = classify_page(url)
        assert result.page_type == "article"
        # Lower score than Wikipedia (no domain bonus)
        assert result.score == 30

    def test_mediawiki_dom_signal(self):
        """Non-Wikipedia site with MediaWiki DOM + /wiki/ URL → article."""
        url = "https://wiki.archlinux.org/wiki/Installation_guide"
        html = """<html><body>
            <nav role="navigation"><div class="sidebar">Portal</div></nav>
            <div id="mw-content-text" class="mw-parser-output">
                <table><tr><td>Info</td></tr></table>
                <table><tr><td>Packages</td></tr></table>
                <p>Main article content here with enough text to avoid error classification.</p>
            </div>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "article"
        assert "dom_mw_content" in result.signals


# ---------------------------------------------------------------------------
# QR-04: Amazon classification
# ---------------------------------------------------------------------------


class TestAmazonClassification:
    """Amazon /dp/ pages must be classified as product_detail."""

    def test_amazon_dp_url_only(self):
        """Amazon /dp/ASIN URL alone → product_detail."""
        url = "https://www.amazon.com/dp/B09V3KXJPB"
        result = classify_page(url)
        assert result.page_type == "product_detail"

    def test_amazon_dp_heavy_dom_no_jsonld(self):
        """Amazon /dp/ + heavy dashboard DOM, no JSON-LD → product_detail (short-circuit)."""
        url = "https://www.amazon.com/dp/B09V3KXJPB"
        html = """<html><body>
            <table><tr><td>Specs</td></tr></table>
            <table><tr><td>Reviews</td></tr></table>
            <canvas id="chart1"></canvas>
            <svg id="icon1"></svg>
            <svg id="icon2"></svg>
            <nav role="navigation"><div class="sidebar">Menu</div></nav>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "product_detail"

    def test_amazon_dp_with_jsonld_and_cart(self):
        """Full Amazon product page → product_detail."""
        url = "https://www.amazon.com/dp/B09V3KXJPB"
        html = """<html><head>
            <script type="application/ld+json">{"@type": "Product", "name": "Widget"}</script>
        </head><body>
            <button>Add to Cart</button>
            <table><tr><td>Details</td></tr></table>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "product_detail"


# ---------------------------------------------------------------------------
# QR-04: DOM cap behaviour
# ---------------------------------------------------------------------------


class TestDomCapBehavior:
    """_DOM_CAP prevents dashboard DOM from overwhelming other signals."""

    def test_dashboard_dom_capped(self):
        """Generic URL + max dashboard DOM → dashboard score capped at 40."""
        url = "https://example.com/page"
        # Note: visible text < 200 chars, so dom_very_short_content fires (error: 20),
        # but error threshold (25) prevents it from winning over dashboard (40).
        html = """<html><body>
            <table><tr><td>A</td></tr></table>
            <table><tr><td>B</td></tr></table>
            <canvas></canvas><svg></svg><svg></svg><svg></svg>
            <nav role="navigation"><div class="sidebar">Nav</div></nav>
            <p>Dashboard content with metrics and analytics data.</p>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        # Dashboard raw DOM = 25+25+20 = 70, capped to 40
        assert result.page_type == "dashboard"
        assert result.score <= 40

    def test_product_url_beats_capped_dashboard_dom(self):
        """/products/ URL + JSON-LD Product + dashboard DOM → product_detail wins."""
        url = "https://shop.com/products/12345"
        html = """<html><head>
            <script type="application/ld+json">{"@type": "Product", "name": "Widget"}</script>
        </head><body>
            <table><tr><td>A</td></tr></table>
            <table><tr><td>B</td></tr></table>
            <canvas></canvas><svg></svg><svg></svg><svg></svg>
            <nav role="navigation"><div class="sidebar">Nav</div></nav>
            <p>Enough visible content to pass the threshold check easily.</p>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "product_detail"

    def test_legitimate_dashboard_still_works(self):
        """/dashboard URL + heavy DOM → dashboard (URL + capped DOM = 60)."""
        url = "https://app.example.com/dashboard"
        html = """<html><body>
            <table><tr><td>Metric A</td></tr></table>
            <table><tr><td>Metric B</td></tr></table>
            <canvas></canvas><svg></svg><svg></svg><svg></svg>
            <nav role="navigation"><div class="sidebar">Sidebar</div></nav>
            <p>Dashboard with enough content to display metrics and analytics data for users.</p>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "dashboard"


# ---------------------------------------------------------------------------
# QR-04: Error threshold raised
# ---------------------------------------------------------------------------


class TestErrorThresholdRaised:
    """Error threshold (25) prevents short-content-alone misclassification."""

    def test_short_content_alone_not_error(self):
        """Simple HTML < 200 chars → unknown, not error."""
        url = "https://example.com/page"
        html = "<html><body><p>Hello</p></body></html>"
        result = classify_page(url, raw_html=html)
        assert result.page_type != "error"

    def test_short_content_plus_404_url_is_error(self):
        """/404 URL + short content → error."""
        url = "https://example.com/404"
        html = "<html><body><p>Oops</p></body></html>"
        result = classify_page(url, raw_html=html)
        assert result.page_type == "error"

    def test_title_error_still_works(self):
        """Title '404 Not Found' → error (meta_title_error = 35 > 25)."""
        url = "https://example.com/unknown-page"
        html = "<html><head><title>404 Not Found</title></head><body><h1>Page not found</h1></body></html>"
        result = classify_page(url, raw_html=html)
        assert result.page_type == "error"


# ---------------------------------------------------------------------------
# QR-04: dom_add_to_cart signal
# ---------------------------------------------------------------------------


class TestDomAddToCart:
    """dom_add_to_cart boosts product_detail classification."""

    def test_cart_text_boosts_product(self):
        """'Add to Cart' text on generic URL fires product_detail signal."""
        url = "https://shop.com/item/12345"
        html = """<html><body>
            <h1>Widget</h1>
            <button>Add to Cart</button>
            <p>This is a product page with enough content to display the full product description and specifications.</p>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert result.page_type == "product_detail"
        assert "dom_add_to_cart" in result.signals

    def test_korean_cart_text(self):
        """Korean '장바구니' triggers dom_add_to_cart."""
        url = "https://shop.kr/goods/12345"
        html = """<html><body>
            <h1>상품</h1>
            <button>장바구니</button>
            <p>상품 설명이 충분히 길어야 에러 페이지로 분류되지 않습니다. 이 상품은 최고의 품질을 자랑합니다.</p>
        </body></html>"""
        result = classify_page(url, raw_html=html)
        assert "dom_add_to_cart" in result.signals


# ---------------------------------------------------------------------------
# QR-04: Cross-type contamination invariant
# ---------------------------------------------------------------------------


# Types with strong enough URL signals to short-circuit past dashboard DOM.
# Types with a single weak URL signal (20-25 pts) need additional meta/DOM
# support to beat capped dashboard (40 pts) — tested in TestDomCapBehavior.
_CROSS_TYPE_CASES = [
    ("https://example.com/search?q=test", "search_results"),  # 25+25=50
    ("https://en.wikipedia.org/wiki/Test", "article"),  # 30+15=45
    ("https://shop.com/vp/products/123", "product_detail"),  # 25+20=45
    ("https://www.amazon.com/dp/B09V3KXJPB", "product_detail"),  # 20+25=45
]

# Heavy dashboard DOM: 2 tables + 3 SVGs + sidebar nav + enough text (>200 chars)
_HEAVY_DASHBOARD_HTML = """<html><body>
    <table><tr><td>Metric A value</td></tr></table>
    <table><tr><td>Metric B value</td></tr></table>
    <canvas></canvas><svg></svg><svg></svg><svg></svg>
    <nav role="navigation"><div class="sidebar">Navigation</div></nav>
    <p>This page contains sufficient visible text content to avoid triggering the
    short-content error signal. The text needs to be longer than two hundred characters
    when all HTML tags are stripped, so we add this extended paragraph here.</p>
</body></html>"""


class TestCrossTypeContamination:
    """Types with strong URL signals resist dashboard DOM contamination."""

    @pytest.mark.parametrize(
        "url,expected",
        _CROSS_TYPE_CASES,
        ids=[c[0].split("//")[1][:40] for c in _CROSS_TYPE_CASES],
    )
    def test_type_resists_dashboard_dom(self, url: str, expected: str):
        result = classify_page(url, raw_html=_HEAVY_DASHBOARD_HTML)
        assert result.page_type == expected, (
            f"Expected {expected} but got {result.page_type} (score={result.score}, signals={result.signals})"
        )


# ---------------------------------------------------------------------------
# QR-06: Captcha/WAF block page detection
# ---------------------------------------------------------------------------


class TestBlockedPageType:
    """QR-06: Captcha/WAF block page detection."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://example.com/captcha", "blocked"),
            ("https://google.com/sorry/index?continue=foo", "blocked"),
            ("https://example.com/challenge", "blocked"),
            ("https://cdn.example.com/cdn-cgi/challenge-platform/abc", "blocked"),
            ("https://errors.edgesuite.net/something", "blocked"),
        ],
    )
    def test_blocked_url_signals(self, url, expected):
        assert classify_page(url).page_type == expected

    @pytest.mark.parametrize(
        "title,dom_content",
        [
            ("Just a moment...", '<div id="cf-browser-verification">'),
            ("Access Denied", "<p>Access denied.</p>"),
            ("Please verify", '<div class="g-recaptcha">'),
            ("Attention Required", '<div class="cf-turnstile">'),
        ],
    )
    def test_blocked_html_signals(self, title, dom_content):
        html = f"<html><head><title>{title}</title></head><body>{dom_content}</body></html>"
        assert classify_page("https://example.com/page", raw_html=html).page_type == "blocked"

    def test_large_page_not_blocked(self):
        """Large page with captcha string in JS should NOT be classified as blocked."""
        content = "Product description " * 500
        html = f'<html><body><script>var x = "captcha";</script><p>{content}</p></body></html>'
        assert classify_page("https://shop.com/products/123", raw_html=html).page_type != "blocked"

    def test_search_url_with_challenge(self):
        """Google search URL + CF challenge -> blocked wins over search_results."""
        html = (
            "<html><head><title>Just a moment...</title></head>"
            '<body><div class="challenge-running"></div></body></html>'
        )
        assert classify_page("https://google.com/search?q=test", raw_html=html).page_type == "blocked"

    def test_modern_provider_datadome(self):
        html = '<html><body><div class="datadome-captcha"></div><p>Verify</p></body></html>'
        assert classify_page("https://example.com/page", raw_html=html).page_type == "blocked"
