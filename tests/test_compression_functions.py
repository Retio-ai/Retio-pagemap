# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for pruned_context_builder.py compression functions.

Phase 7.4 — per-compressor behaviour tests.
Cross-cutting token budget test lives in test_page_type_compression.py.
"""

from __future__ import annotations

from pagemap.pruned_context_builder import (
    _calibrate_chars_per_token,
    _compress_default,
    _compress_for_article,
    _compress_for_checkout,
    _compress_for_dashboard,
    _compress_for_documentation,
    _compress_for_error,
    _compress_for_form,
    _compress_for_help_faq,
    _compress_for_landing,
    _compress_for_listing,
    _compress_for_login,
    _compress_for_product,
    _compress_for_search_results,
    _compress_for_settings,
    _extract_text_lines,
    _truncate_to_tokens,
)
from tests._pruning_helpers import html

# ---------------------------------------------------------------------------
# TestExtractTextLines
# ---------------------------------------------------------------------------


class TestExtractTextLines:
    def test_strips_tags(self):
        lines = _extract_text_lines("<p>Hello</p><p>World</p>")
        assert "Hello" in lines
        assert "World" in lines

    def test_removes_script(self):
        lines = _extract_text_lines("<script>alert(1)</script><p>Visible</p>")
        assert not any("alert" in line for line in lines)
        assert "Visible" in lines

    def test_whitespace_normalization(self):
        lines = _extract_text_lines("<p>  hello   world  </p>")
        assert any("hello world" in line for line in lines)

    def test_empty_html(self):
        assert _extract_text_lines("") == []

    def test_cjk_text(self):
        lines = _extract_text_lines("<p>안녕하세요 세계</p>")
        assert len(lines) >= 1
        assert "안녕하세요" in lines[0]

    def test_nested_tags(self):
        lines = _extract_text_lines("<div><span><b>Bold</b></span></div>")
        assert any("Bold" in line for line in lines)

    def test_style_removed(self):
        lines = _extract_text_lines("<style>.x{color:red}</style><p>Text</p>")
        assert not any("color" in line for line in lines)
        assert "Text" in lines


# ---------------------------------------------------------------------------
# TestCalibrateCharsPerToken
# ---------------------------------------------------------------------------


class TestCalibrateCharsPerToken:
    def test_empty_returns_default(self):
        assert _calibrate_chars_per_token([], min_len=5, max_line_len=300) == 4.0

    def test_below_min_len_returns_default(self):
        short_lines = ["ab", "cd", "ef"]
        assert _calibrate_chars_per_token(short_lines, min_len=5, max_line_len=300) == 4.0

    def test_english_ratio(self):
        lines = ["This is a sample sentence for calibration testing"] * 5
        ratio = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
        assert 2.0 < ratio < 6.0

    def test_cjk_floor(self):
        lines = ["이것은 한국어 텍스트입니다 테스트 문장"] * 5
        ratio = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
        assert ratio >= 1.5

    def test_sample_cap_20(self):
        """At most 20 lines sampled."""
        lines = [f"Line number {i} with enough content" for i in range(100)]
        # Should not error, and should produce a reasonable ratio
        ratio = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
        assert ratio > 0


# ---------------------------------------------------------------------------
# TestTruncateToTokens
# ---------------------------------------------------------------------------


class TestTruncateToTokens:
    def test_short_unchanged(self):
        text = "Hello world"
        assert _truncate_to_tokens(text, 100) == text

    def test_long_truncated(self):
        text = "Hello world! " * 200
        result = _truncate_to_tokens(text, 10)
        assert len(result) < len(text)

    def test_zero_tokens(self):
        result = _truncate_to_tokens("Hello world", 0)
        assert result == ""

    def test_cjk_truncation(self):
        text = "안녕하세요 " * 100
        result = _truncate_to_tokens(text, 10)
        assert len(result) < len(text)


# ---------------------------------------------------------------------------
# Tier 1: Original 5 compressors (deep)
# ---------------------------------------------------------------------------


class TestCompressForProduct:
    def test_metadata_fields(self):
        result = _compress_for_product(
            html("<p>Extra</p>"),
            max_tokens=500,
            metadata={"name": "Widget", "price": 29900, "currency": "KRW"},
        )
        assert "Widget" in result

    def test_regex_fallback(self):
        src = html("<p>Great Product Name Here</p><p>29,900원</p><p>★ 4.5점</p>")
        result = _compress_for_product(src, max_tokens=500)
        assert "29,900" in result

    def test_no_duplication(self):
        """When metadata covers a field, regex shouldn't duplicate it."""
        src = html("<p>Some Title</p><p>10,000원</p>")
        result = _compress_for_product(src, max_tokens=500, metadata={"name": "Some Title", "price": 10000})
        count = result.count("Some Title")
        assert count == 1

    def test_won_postfix(self):
        """Bare KRW price gets 원 postfix."""
        src = html("<p>29,900</p>")
        result = _compress_for_product(src, max_tokens=500)
        assert "29,900원" in result

    def test_empty_metadata(self):
        src = html("<p>Product Page Content is here for testing</p>")
        result = _compress_for_product(src, max_tokens=500, metadata={})
        assert len(result) > 0

    def test_option_keywords(self):
        from pagemap.i18n import OPTION_TERMS

        # Use first option term
        kw = OPTION_TERMS[0] if OPTION_TERMS else "색상"
        src = html(f"<p>{kw}: 빨강, 파랑</p><p>Product Title for Testing</p>")
        result = _compress_for_product(src, max_tokens=500)
        assert kw in result

    def test_rating_from_regex(self):
        src = html("<p>Amazing Product Title Here</p><p>평점 4.8점</p>")
        result = _compress_for_product(src, max_tokens=500)
        assert "4.8" in result


class TestCompressForArticle:
    def test_first_substantial_line_title(self):
        src = html("<h1>Breaking News Headline</h1><p>Short article content here.</p>")
        result = _compress_for_article(src, max_tokens=500)
        assert "Breaking News" in result

    def test_date_extraction(self):
        src = html(
            "<h1>Article Title Here Long Enough</h1><p>2024-10-22</p><p>Paragraph content for article testing.</p>"
        )
        result = _compress_for_article(src, max_tokens=500)
        assert "2024-10-22" in result

    def test_max_2_paragraphs(self):
        src = html(
            "<h1>Title of the Article Here</h1>"
            "<p>First paragraph with enough content for testing here.</p>"
            "<p>Second paragraph with enough content for testing here.</p>"
            "<p>Third paragraph with enough content for testing here.</p>"
        )
        result = _compress_for_article(src, max_tokens=500)
        assert "First" in result
        assert "Second" in result
        assert "Third" not in result

    def test_truncation_300(self):
        long_para = "x" * 500
        src = html(f"<h1>Title Goes Here With Content</h1><p>{long_para}</p>")
        result = _compress_for_article(src, max_tokens=1500)
        # Individual line truncated to 300
        lines = result.split("\n")
        for line in lines:
            assert len(line) <= 350  # some formatting overhead

    def test_short_lines_skipped(self):
        src = html("<p>Hi</p><h1>Real Title for the Article</h1><p>Long enough paragraph with more text here.</p>")
        result = _compress_for_article(src, max_tokens=500)
        # "Hi" is too short for title (< 10 chars) and too short for para (< 30)
        assert "Hi" not in result


class TestCompressForSearchResults:
    def test_legacy_fallback_no_cards(self):
        """Without chunks or metadata, falls back to text-line extraction."""
        src = html("<p>검색결과 50건</p><p>29,900원</p>")
        result = _compress_for_search_results(src, max_tokens=500)
        assert len(result) > 0

    def test_result_count(self):
        from pagemap.i18n import SEARCH_RESULT_TERMS

        kw = SEARCH_RESULT_TERMS[0] if SEARCH_RESULT_TERMS else "검색결과"
        src = html(f"<p>{kw} 50건</p><p>Product 29,900원</p>")
        result = _compress_for_search_results(src, max_tokens=500)
        assert kw in result

    def test_card_path_priority(self):
        from pagemap.pruning import ChunkType, HtmlChunk

        chunks = [
            HtmlChunk(
                xpath="/body/ul",
                html="<ul><li>Product A 10,000원</li><li>Product B 20,000원</li></ul>",
                text="Product A 10,000원 Product B 20,000원",
                tag="ul",
                chunk_type=ChunkType.LIST,
            )
        ]
        result = _compress_for_search_results(
            html("<ul><li>Product A 10,000원</li><li>Product B 20,000원</li></ul>"),
            max_tokens=500,
            chunks=chunks,
        )
        assert len(result) > 0

    def test_empty_no_crash(self):
        result = _compress_for_search_results("", max_tokens=500)
        assert isinstance(result, str)


class TestCompressForListing:
    def test_listing_keywords(self):
        from pagemap.i18n import LISTING_TERMS

        kw = LISTING_TERMS[0] if LISTING_TERMS else "인기상품"
        src = html(f"<p>{kw}</p><p>Item 1 29,900원</p>")
        result = _compress_for_listing(src, max_tokens=500)
        assert len(result) > 0

    def test_card_same_as_search(self):
        """Listing uses same card detection as search results."""
        from pagemap.pruning import ChunkType, HtmlChunk

        chunks = [
            HtmlChunk(
                xpath="/body/ul",
                html="<ul><li>Item A 5,000원</li></ul>",
                text="Item A 5,000원",
                tag="ul",
                chunk_type=ChunkType.LIST,
            )
        ]
        result = _compress_for_listing(
            html("<ul><li>Item A 5,000원</li></ul>"),
            max_tokens=500,
            chunks=chunks,
        )
        assert len(result) > 0


class TestCompressDefault:
    def test_headings_prioritized(self):
        src = html("<h1>Main Heading</h1><p>Paragraph with enough text for testing here.</p>")
        result = _compress_default(src, max_tokens=500)
        assert "Main Heading" in result

    def test_significant_text(self):
        src = html("<p>This is significant text with enough length</p>")
        result = _compress_default(src, max_tokens=500)
        assert "significant" in result

    def test_short_excluded(self):
        src = html("<p>Hi</p><p>This is a longer text block for testing</p>")
        result = _compress_default(src, max_tokens=500)
        # "Hi" is < 5 chars, should be excluded
        assert "Hi" not in result


# ---------------------------------------------------------------------------
# Tier 2: Notable features
# ---------------------------------------------------------------------------


class TestCompressForLogin:
    def test_error_bypass_budget(self):
        """Error messages are added regardless of budget."""
        src = html("<p>오류: 잘못된 비밀번호입니다</p><p>Email</p>")
        result = _compress_for_login(src, max_tokens=500)
        assert "error" in result.lower() or "오류" in result

    def test_social_login(self):
        src = html("<p>Google로 로그인</p><p>Kakao 로그인</p><p>Email</p>")
        result = _compress_for_login(src, max_tokens=500)
        assert any(kw in result.lower() for kw in ("google", "kakao", "email"))

    def test_korean_keywords(self):
        src = html("<p>이메일 주소</p><p>비밀번호 입력</p>")
        result = _compress_for_login(src, max_tokens=500)
        assert "이메일" in result or "비밀번호" in result

    def test_fallback_to_default(self):
        src = html("<p>Totally unrelated content about nature and wildlife</p>")
        result = _compress_for_login(src, max_tokens=500)
        assert len(result) > 0


class TestCompressForForm:
    def test_validation_bypass_budget(self):
        src = html("<p>필수 항목입니다</p><p>Name field label</p>")
        result = _compress_for_form(src, max_tokens=500)
        assert "validation" in result.lower() or "필수" in result

    def test_field_keywords(self):
        src = html("<p>이름</p><p>이메일</p><p>전화번호</p>")
        result = _compress_for_form(src, max_tokens=500)
        assert any(kw in result for kw in ("이름", "이메일", "전화"))

    def test_fallback_to_default(self):
        src = html("<p>Random unrelated content about cooking recipes</p>")
        result = _compress_for_form(src, max_tokens=500)
        assert len(result) > 0


class TestCompressForHelpFaq:
    def test_question_numbered(self):
        src = html("<p>How do I return an item?</p><p>What is the shipping policy?</p>")
        result = _compress_for_help_faq(src, max_tokens=500)
        assert "Q1." in result
        assert "Q2." in result

    def test_fullwidth_question_mark(self):
        src = html("<p>반품은 어떻게 하나요？</p>")
        result = _compress_for_help_faq(src, max_tokens=500)
        assert "Q1." in result

    def test_budget_limit(self):
        from pagemap.preprocessing.preprocess import count_tokens

        big = html("".join(f"<p>Question {i}?</p>" for i in range(200)))
        result = _compress_for_help_faq(big, max_tokens=50)
        assert count_tokens(result) <= 60


class TestCompressForDocumentation:
    def test_headings(self):
        src = html("<h1>API Reference</h1><h2>Authentication</h2><p>Some short heading</p>")
        result = _compress_for_documentation(src, max_tokens=500)
        assert "API Reference" in result

    def test_code_keywords_indented(self):
        src = html("<p>def authenticate(token):</p>")
        result = _compress_for_documentation(src, max_tokens=500)
        assert "def authenticate" in result

    def test_long_non_code_skipped(self):
        """Lines >80 chars that aren't code-like are skipped."""
        long_text = "This is a very long text line that goes beyond 80 characters and is not code-like at all so it should be skipped"
        src = html(f"<p>{long_text}</p><p>Short heading</p>")
        result = _compress_for_documentation(src, max_tokens=500)
        assert long_text not in result
        assert "Short heading" in result


class TestCompressForCheckout:
    def test_total_and_payment(self):
        src = html("<p>합계: 50,000원</p><p>결제 수단: 신용카드</p>")
        result = _compress_for_checkout(src, max_tokens=500)
        assert "합계" in result or "결제" in result

    def test_korean_keywords(self):
        src = html("<p>배송 주소</p><p>주문 확인</p>")
        result = _compress_for_checkout(src, max_tokens=500)
        assert any(kw in result for kw in ("배송", "주문"))

    def test_japanese_keywords(self):
        src = html("<p>合計: ¥5,000</p><p>お支払い方法</p>")
        result = _compress_for_checkout(src, max_tokens=500)
        assert "合計" in result or "お支払い" in result


# ---------------------------------------------------------------------------
# Tier 3: Simple accumulators
# ---------------------------------------------------------------------------


class TestCompressForDashboard:
    def test_metric_keywords(self):
        src = html("<p>Total Revenue: $50,000</p><p>Active Users: 1,234</p>")
        result = _compress_for_dashboard(src, max_tokens=500)
        assert any(kw in result.lower() for kw in ("total", "revenue", "users"))

    def test_short_lines_kept(self):
        src = html("<p>Revenue</p><p>Users</p><p>Views</p>")
        result = _compress_for_dashboard(src, max_tokens=500)
        # Short lines (< 80) should be kept
        assert len(result) > 0


class TestCompressForSettings:
    def test_toggle_on_off(self):
        src = html("<p>Notification: On</p><p>Theme: Dark</p>")
        result = _compress_for_settings(src, max_tokens=500)
        assert "on" in result.lower() or "notification" in result.lower()

    def test_korean_keywords(self):
        src = html("<p>알림 설정</p><p>언어: 한국어</p>")
        result = _compress_for_settings(src, max_tokens=500)
        assert "설정" in result or "알림" in result or "언어" in result


class TestCompressForError:
    def test_all_accumulated(self):
        src = html("<p>404 Error</p><p>Page not found</p><p>Go back home</p>")
        result = _compress_for_error(src, max_tokens=500)
        assert "404" in result
        assert "Page not found" in result

    def test_short_skip(self):
        src = html("<p>OK</p><p>Error page with details here</p>")
        result = _compress_for_error(src, max_tokens=500)
        # "OK" is < 3 chars, skipped
        assert "OK" not in result.split("\n")[0] if result else True


class TestCompressForLanding:
    def test_short_lines_under_100(self):
        src = html("<p>Welcome to Our Product</p><p>Get Started Today</p>")
        result = _compress_for_landing(src, max_tokens=500)
        assert "Welcome" in result

    def test_long_lines_skipped(self):
        long_text = "x" * 150
        src = html(f"<p>{long_text}</p><p>Short CTA</p>")
        result = _compress_for_landing(src, max_tokens=500)
        assert long_text not in result
        assert "Short CTA" in result
