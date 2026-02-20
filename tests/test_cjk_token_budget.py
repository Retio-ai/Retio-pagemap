"""Tests for CJK token budget compensation in page_map_builder."""

from __future__ import annotations

import pytest

from pagemap.page_map_builder import (
    DEFAULT_PRUNED_CONTEXT_TOKENS,
    DEFAULT_TOTAL_BUDGET_TOKENS,
    _sample_visible_text,
    build_page_map_offline,
    compute_token_budget,
)

# ---------------------------------------------------------------------------
# compute_token_budget — locale-only (no HTML)
# ---------------------------------------------------------------------------


class TestComputeTokenBudgetLocaleOnly:
    def test_korean_locale_no_html(self):
        b = compute_token_budget("ko")
        assert b.multiplier == 1.8
        assert b.pruned_context == 2700  # 1500 * 1.8
        assert b.total == 9000  # 5000 * 1.8
        assert b.locale == "ko"
        assert b.cjk_ratio == 0.0

    def test_english_locale_no_html(self):
        b = compute_token_budget("en")
        assert b.multiplier == 1.0
        assert b.pruned_context == 1500
        assert b.total == 5000
        assert b.locale == "en"

    def test_japanese_locale_no_html(self):
        b = compute_token_budget("ja")
        assert b.multiplier == 1.5
        assert b.pruned_context == 2250  # 1500 * 1.5
        assert b.total == 7500  # 5000 * 1.5

    def test_french_locale_no_html(self):
        b = compute_token_budget("fr")
        assert b.multiplier == 1.0

    def test_german_locale_no_html(self):
        b = compute_token_budget("de")
        assert b.multiplier == 1.0

    def test_unknown_locale_no_html(self):
        b = compute_token_budget("pt")
        assert b.multiplier == 1.0
        assert b.pruned_context == DEFAULT_PRUNED_CONTEXT_TOKENS
        assert b.total == DEFAULT_TOTAL_BUDGET_TOKENS

    def test_none_html_explicit(self):
        b = compute_token_budget("ko", raw_html=None)
        assert b.cjk_ratio == 0.0
        assert b.multiplier == 1.8  # locale default


# ---------------------------------------------------------------------------
# compute_token_budget — content-based refinement
# ---------------------------------------------------------------------------


def _make_html(body_text: str, head_text: str = "") -> str:
    """Helper to build minimal HTML with body text."""
    return f"<html><head><title>Test</title>{head_text}</head><body>{body_text}</body></html>"


class TestComputeTokenBudgetContentRefinement:
    def test_english_locale_korean_html(self):
        """Non-CJK locale + heavy CJK content → override multiplier > 1.0."""
        korean_text = "한국어 " * 200  # heavy Korean content
        html = _make_html(korean_text)
        b = compute_token_budget("en", raw_html=html)
        assert b.multiplier > 1.0
        assert b.cjk_ratio > 0.3

    def test_korean_locale_english_html(self):
        """CJK locale + non-CJK content → suppress multiplier < 1.8."""
        english_text = "This is a completely English product page with no Korean text. " * 20
        html = _make_html(english_text)
        b = compute_token_budget("ko", raw_html=html)
        assert b.multiplier < 1.8
        # Should still be >= 1.0 (floor)
        assert b.multiplier >= 1.0

    def test_korean_locale_korean_html(self):
        """CJK locale + CJK content → confirmed full multiplier."""
        korean_text = "쿠팡 상품 페이지 가격 배송 리뷰 " * 50
        html = _make_html(korean_text)
        b = compute_token_budget("ko", raw_html=html)
        assert b.multiplier == 1.8

    def test_english_locale_english_html(self):
        """Non-CJK locale + non-CJK content → no adjustment."""
        english_text = "This is a normal English product page with prices and reviews. " * 20
        html = _make_html(english_text)
        b = compute_token_budget("en", raw_html=html)
        assert b.multiplier == 1.0
        assert b.cjk_ratio < 0.3

    def test_mixed_cjk_english(self):
        """Mixed CJK/English content with CJK > 30% → multiplier between 1.0 and 1.8."""
        mixed = "Hello 안녕하세요 제품설명 " * 100  # higher CJK ratio
        html = _make_html(mixed)
        b = compute_token_budget("en", raw_html=html)
        assert b.cjk_ratio > 0.3, f"CJK ratio too low: {b.cjk_ratio}"
        assert 1.0 < b.multiplier <= 1.8

    def test_short_sample_skipped(self):
        """<50 chars visible text → use locale default only, skip content detection."""
        html = _make_html("짧은")  # very short
        b = compute_token_budget("ko", raw_html=html)
        assert b.multiplier == 1.8  # locale default, no content override
        assert b.cjk_ratio == 0.0  # not computed (sample too short)

    def test_multiplier_clamp_floor(self):
        """Multiplier never goes below 1.0."""
        # English locale + English content → should be exactly 1.0
        english_text = "English text only " * 50
        html = _make_html(english_text)
        b = compute_token_budget("en", raw_html=html)
        assert b.multiplier >= 1.0

    def test_multiplier_clamp_ceiling(self):
        """Multiplier never exceeds 2.5."""
        # Even with extreme parameters, ceiling holds
        b = compute_token_budget("ko", base_pruned=1500, base_total=5000)
        assert b.multiplier <= 2.5

    def test_custom_base_budgets(self):
        """Custom base budgets are scaled correctly."""
        b = compute_token_budget("ko", base_pruned=2000, base_total=6000)
        assert b.pruned_context == 3600  # 2000 * 1.8
        assert b.total == 10800  # 6000 * 1.8


# ---------------------------------------------------------------------------
# Script/style content stripping
# ---------------------------------------------------------------------------


class TestScriptStyleStripping:
    def test_script_style_not_counted(self):
        """Korean text inside script/style tags should be excluded from CJK ratio."""
        html = _make_html(
            '<script>var x = "한국어한국어한국어한국어한국어한국어한국어";</script>'
            "<style>.한국어 { color: red; }</style>"
            "This is English text only on the visible page. " * 20
        )
        b = compute_token_budget("en", raw_html=html)
        assert b.cjk_ratio < 0.1  # CJK in scripts excluded

    def test_noscript_content_excluded(self):
        """noscript content also stripped."""
        html = _make_html(
            "<noscript>한국어한국어한국어한국어한국어한국어</noscript>"
            "Plain English visible text with no Korean at all. " * 20
        )
        b = compute_token_budget("en", raw_html=html)
        assert b.cjk_ratio < 0.1


# ---------------------------------------------------------------------------
# Head content skipping
# ---------------------------------------------------------------------------


class TestHeadContentSkipping:
    def test_head_content_skipped(self):
        """CJK content only in <head> should not affect the ratio."""
        head_text = (
            '<script type="application/ld+json">'
            '{"name": "한국어 상품 페이지 한국어 가격 한국어 배송"}'
            "</script>"
            '<meta name="description" content="한국어 설명 한국어 리뷰">'
        )
        body_text = "This is a completely English product page. " * 30
        html = _make_html(body_text, head_text=head_text)
        b = compute_token_budget("en", raw_html=html)
        assert b.cjk_ratio < 0.1  # Head CJK not counted


# ---------------------------------------------------------------------------
# _sample_visible_text
# ---------------------------------------------------------------------------


class TestSampleVisibleText:
    def test_sample_finds_body(self):
        """Should skip head and sample body text."""
        html = "<html><head><title>Skip this</title></head><body><p>Hello World</p></body></html>"
        text = _sample_visible_text(html)
        assert "Hello World" in text
        assert "Skip this" not in text

    def test_sample_strips_script_style(self):
        """JS and CSS content should be excluded."""
        html = (
            "<body>"
            '<script>var korean = "한국어";</script>'
            "<p>Visible text here</p>"
            "<style>.cls { color: red; }</style>"
            "</body>"
        )
        text = _sample_visible_text(html)
        assert "Visible text here" in text
        assert "한국어" not in text
        assert "color" not in text

    def test_sample_no_body_tag(self):
        """Falls back to start of HTML if no body tag."""
        html = "<div>Some content without body tags</div>"
        text = _sample_visible_text(html)
        assert "Some content" in text

    def test_sample_empty_html(self):
        """Empty HTML returns empty string."""
        text = _sample_visible_text("")
        assert text == ""

    def test_sample_truncates_to_2000(self):
        """Output is limited to 2000 characters."""
        long_text = "A" * 5000
        html = f"<body>{long_text}</body>"
        text = _sample_visible_text(html)
        assert len(text) <= 2000


# ---------------------------------------------------------------------------
# TokenBudget dataclass
# ---------------------------------------------------------------------------


class TestTokenBudgetDataclass:
    def test_frozen(self):
        b = compute_token_budget("ko")
        with pytest.raises(AttributeError):
            b.multiplier = 2.0  # type: ignore[misc]

    def test_fields(self):
        b = compute_token_budget("en")
        assert isinstance(b.pruned_context, int)
        assert isinstance(b.total, int)
        assert isinstance(b.multiplier, float)
        assert isinstance(b.locale, str)
        assert isinstance(b.cjk_ratio, float)


# ---------------------------------------------------------------------------
# Integration tests — build_page_map_offline
# ---------------------------------------------------------------------------


def _make_product_html(text_content: str, url_hint: str = "") -> str:
    """Build a realistic product page HTML for offline testing."""
    return (
        "<html><head><title>Product</title></head><body>"
        '<main itemscope itemtype="https://schema.org/Product">'
        f'<h1 itemprop="name">Test Product</h1>'
        f'<span itemprop="price">10000</span>'
        f"<div>{text_content}</div>"
        "<button>장바구니 담기</button>"
        "</main></body></html>"
    )


class TestOfflineIntegration:
    def test_offline_korean_product_higher_budget(self):
        """Korean product page should get higher pruned_context budget."""
        korean_desc = "이 상품은 최고의 품질로 만들어진 프리미엄 제품입니다. " * 100
        html = _make_product_html(korean_desc)
        pm = build_page_map_offline(
            raw_html=html,
            url="https://www.coupang.com/vp/products/12345",
            site_id="coupang",
            page_id="test",
        )
        # Korean page gets higher budget, so pruned_tokens CAN exceed base 1500
        # (doesn't guarantee it will — depends on actual content length)
        assert pm.metadata.get("_total_budget") == 9000  # 5000 * 1.8

    def test_offline_english_product_base_budget(self):
        """English product page should use base budget."""
        english_desc = "This product is made with the highest quality materials. " * 100
        html = _make_product_html(english_desc)
        pm = build_page_map_offline(
            raw_html=html,
            url="https://www.amazon.com/dp/B12345",
            site_id="amazon",
            page_id="test",
        )
        assert pm.metadata.get("_total_budget") == 5000  # base budget

    def test_metadata_stores_total_budget(self):
        """_total_budget should be present in metadata after build."""
        html = _make_product_html("Some product content " * 20)
        pm = build_page_map_offline(
            raw_html=html,
            url="https://www.coupang.com/vp/products/12345",
            site_id="coupang",
            page_id="test",
        )
        assert "_total_budget" in pm.metadata
        assert isinstance(pm.metadata["_total_budget"], int)
        assert pm.metadata["_total_budget"] > 0
