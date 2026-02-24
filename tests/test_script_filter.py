# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.script_filter — Unicode script-based language filtering."""

from __future__ import annotations

from pagemap.script_filter import (
    FilterResult,
    Script,
    classify_char,
    detect_page_script,
    filter_lines,
    profile_text,
)

# ── classify_char ────────────────────────────────────────────────


class TestClassifyChar:
    def test_latin_uppercase(self):
        assert classify_char(ord("A")) == Script.LATIN

    def test_latin_lowercase(self):
        assert classify_char(ord("z")) == Script.LATIN

    def test_hangul_syllable(self):
        assert classify_char(ord("가")) == Script.HANGUL

    def test_hangul_jamo(self):
        assert classify_char(ord("ㄱ")) == Script.HANGUL

    def test_cjk_ideograph(self):
        assert classify_char(ord("中")) == Script.CJK

    def test_hiragana(self):
        assert classify_char(ord("あ")) == Script.HIRAGANA

    def test_katakana(self):
        assert classify_char(ord("ア")) == Script.KATAKANA

    def test_cyrillic(self):
        assert classify_char(ord("Д")) == Script.CYRILLIC

    def test_arabic(self):
        assert classify_char(ord("ع")) == Script.ARABIC

    def test_digit_is_common(self):
        assert classify_char(ord("5")) == Script.COMMON

    def test_space_is_common(self):
        assert classify_char(ord(" ")) == Script.COMMON

    def test_currency_won_is_common(self):
        assert classify_char(ord("₩")) == Script.COMMON

    def test_beyond_unicode_max(self):
        assert classify_char(0x110000) == Script.UNKNOWN

    def test_latin_extended(self):
        assert classify_char(ord("é")) == Script.LATIN

    def test_fullwidth_digit(self):
        assert classify_char(ord("１")) == Script.COMMON


# ── profile_text ─────────────────────────────────────────────────


class TestProfileText:
    def test_pure_korean(self):
        prof = profile_text("안녕하세요 반갑습니다")
        assert prof.dominant == Script.HANGUL
        assert prof.dominant_ratio > 0.9

    def test_pure_english(self):
        prof = profile_text("Hello World")
        assert prof.dominant == Script.LATIN

    def test_mixed_korean_english(self):
        prof = profile_text("Nike 나이키 에어맥스")
        assert prof.dominant == Script.HANGUL

    def test_empty_string(self):
        prof = profile_text("")
        assert prof.total_classified == 0
        assert prof.dominant == Script.COMMON

    def test_only_digits(self):
        prof = profile_text("12345")
        assert prof.total_classified == 0
        assert prof.dominant == Script.COMMON


# ── detect_page_script ───────────────────────────────────────────


class TestDetectPageScript:
    def test_korean_page(self):
        lines = ["상품명: 나이키 에어맥스", "가격: ₩129,000", "리뷰 100개"]
        assert detect_page_script(lines) == Script.HANGUL

    def test_english_page(self):
        lines = ["Nike Air Max", "Price: $129", "100 reviews"]
        assert detect_page_script(lines) == Script.LATIN

    def test_empty_lines(self):
        assert detect_page_script([]) == Script.COMMON

    def test_numbers_only(self):
        assert detect_page_script(["12345", "67890"]) == Script.COMMON

    def test_japanese_page(self):
        lines = ["商品名：ナイキエアマックス", "価格：¥12,900", "レビューを書く"]
        script = detect_page_script(lines)
        # Japanese uses CJK + Katakana + Hiragana mixed
        assert script in (Script.CJK, Script.HIRAGANA, Script.KATAKANA)


# ── filter_lines ─────────────────────────────────────────────────


class TestFilterLines:
    def test_no_filtering_when_common(self):
        lines = ["12345", "67890"]
        result = filter_lines(lines)
        assert result.lines == lines
        assert result.removed_count == 0
        assert result.tagged_count == 0

    def test_removes_short_foreign_noise_on_korean_page(self):
        lines = [
            "나이키 에어맥스",
            "Envío gratis",  # Spanish UI noise, short
            "가격: ₩129,000",
            "리뷰 100개",
        ]
        result = filter_lines(lines, page_script=Script.HANGUL)
        assert result.removed_count == 1
        assert "Envío gratis" not in result.lines
        assert "나이키 에어맥스" in result.lines

    def test_tags_long_foreign_content(self):
        lines = [
            "나이키 에어맥스 상품 설명 페이지",
            "This is a long product description that contains enough English text to exceed the threshold for tagging rather than removal",
            "가격: ₩129,000",
        ]
        result = filter_lines(lines, page_script=Script.HANGUL)
        assert result.tagged_count == 1
        assert any(line.startswith("[en]") for line in result.lines)

    def test_passthrough_url(self):
        lines = [
            "나이키 에어맥스",
            "https://www.nike.com/product/air-max",
            "가격: ₩129,000",
        ]
        result = filter_lines(lines, page_script=Script.HANGUL)
        assert result.removed_count == 0
        assert "https://www.nike.com/product/air-max" in result.lines

    def test_passthrough_short_text(self):
        lines = ["나이키", "Nike", "가격"]
        result = filter_lines(lines, page_script=Script.HANGUL)
        assert result.removed_count == 0
        assert "Nike" in result.lines

    def test_passthrough_numeric(self):
        lines = ["나이키", "42.5mm", "가격"]
        result = filter_lines(lines, page_script=Script.HANGUL)
        assert result.removed_count == 0

    def test_brand_name_in_cjk_page_passes(self):
        """CJK page with Latin brand names should not remove them."""
        lines = [
            "나이키 에어맥스",
            "Nike",  # Brand name, <= 5 chars passes
            "₩129,000",
        ]
        result = filter_lines(lines, page_script=Script.HANGUL)
        assert result.removed_count == 0

    def test_auto_detect_page_script(self):
        lines = [
            "나이키 에어맥스 상품 설명 페이지",
            "상품 설명을 확인하세요",
            "가격 정보 및 배송 안내",
            "리뷰 보기와 장바구니 담기",
            "배송 안내 무료배송 가능",
            "Envío gratis",
        ]
        result = filter_lines(lines)
        assert result.page_script == Script.HANGUL

    def test_custom_thresholds(self):
        lines = [
            "나이키 에어맥스",
            "Free shipping",  # short English — 100% Latin
            "가격: ₩129,000",
        ]
        # With threshold at 1.0, 100% foreign (1.0 > 1.0 = False) → not removed
        result = filter_lines(lines, page_script=Script.HANGUL, remove_threshold=1.0)
        assert result.removed_count == 0

    def test_result_type(self):
        result = filter_lines(["Hello"], page_script=Script.LATIN)
        assert isinstance(result, FilterResult)
        assert isinstance(result.page_script, Script)

    def test_mixed_cjk_hangul_not_filtered(self):
        """Hangul page with CJK chars — CJK is NOT in HANGUL group."""
        lines = [
            "한국어 테스트",
            "价格信息这是测试文本",  # Chinese — long enough to not be passthrough (> 5 chars)
        ]
        result = filter_lines(lines, page_script=Script.HANGUL)
        # CJK is NOT in HANGUL group, so Chinese should be filtered
        assert result.removed_count == 1

    def test_japanese_page_keeps_cjk_and_kana(self):
        """Japanese page should keep CJK + Hiragana + Katakana."""
        lines = [
            "商品説明ページ",  # CJK
            "これは商品の説明です",  # Hiragana + CJK
            "ナイキエアマックス",  # Katakana
        ]
        result = filter_lines(lines, page_script=Script.HIRAGANA)
        assert result.removed_count == 0

    def test_empty_lines(self):
        result = filter_lines([], page_script=Script.LATIN)
        assert result.lines == []
        assert result.removed_count == 0

    def test_all_foreign_short_lines_removed(self):
        lines = [
            "Hello World",
            "가격 정보",
            "배송 안내",
            "Test text here",
        ]
        result = filter_lines(lines, page_script=Script.HANGUL)
        # "Hello World" and "Test text here" are short foreign
        assert result.removed_count == 2
        assert "가격 정보" in result.lines
        assert "배송 안내" in result.lines


# ── Integration with _extract_text_lines_filtered ────────────────


class TestExtractTextLinesFiltered:
    def test_disabled_by_default(self):
        from pagemap.pruned_context_builder import _extract_text_lines_filtered

        html = "<p>Hello World</p><p>가격 정보</p>"
        lines = _extract_text_lines_filtered(html, enable_lang_filter=False)
        # No filtering when disabled
        assert any("Hello" in line for line in lines)

    def test_enabled_filters_foreign(self):
        from pagemap.i18n import get_locale
        from pagemap.pruned_context_builder import _extract_text_lines_filtered

        html = "<p>나이키 에어맥스</p><p>가격: ₩129,000</p><p>리뷰 보기</p><p>Envío gratis</p>"
        lc = get_locale("ko")
        lines = _extract_text_lines_filtered(html, lc=lc, enable_lang_filter=True)
        assert not any("Envío" in line for line in lines)
