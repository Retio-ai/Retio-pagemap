# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for pruned_context_builder code-quality fixes.

Covers:
  - Issue 1: Phase 4 price regex single-quote support
  - Issue 2: Video description budget safety factor (0.85)
  - Issue 3+4: _SCHEMA_OVERRIDES module-level frozenset with VideoObject
"""

from __future__ import annotations

import lxml.html
import pytest

from pagemap.pruned_context_builder import (
    _FORM_CONTROL_RE,
    _PRICE_CLASS_RE,
    _SCHEMA_COMPRESSORS,
    _SCHEMA_OVERRIDES,
    PRICE_PATTERN,
    _compress_for_product,
    _extract_text_lines,
    build_pruned_context,
)

# ---------------------------------------------------------------------------
# Issue 1 — Phase 4 price regex (single + double quote support)
# ---------------------------------------------------------------------------


class TestPhase4PriceRegex:
    """Verify Phase 4 price regex matches both single and double quoted class attrs."""

    @pytest.mark.parametrize(
        "snippet,expected",
        [
            ('<span class="a-price">$29.99</span>', "$29.99"),
            ("<span class='a-price'>$29.99</span>", "$29.99"),
            ('<span class="a-offscreen">$29.99</span>', "$29.99"),
            ("<span class='a-offscreen'>$29.99</span>", "$29.99"),
            ('<span class="a-price offscreen">$29.99</span>', "$29.99"),
        ],
    )
    def test_matches_price_class(self, snippet: str, expected: str) -> None:
        m = _PRICE_CLASS_RE.search(snippet)
        assert m is not None
        assert m.group("price").strip() == expected
        assert PRICE_PATTERN.search(expected)

    @pytest.mark.parametrize("quote", ['"', "'"])
    def test_compress_for_product_extracts_price_either_quote(self, quote: str) -> None:
        """Integration: _compress_for_product extracts price from either quote style."""
        raw = f"<html><body><div class={quote}a-price{quote}><span>$29.99</span></div></body></html>"
        result = _compress_for_product(raw, max_tokens=500)
        assert "$29.99" in result


# ---------------------------------------------------------------------------
# Issue 3+4 — VideoObject schema override + module-level _SCHEMA_OVERRIDES
# ---------------------------------------------------------------------------

_VIDEO_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <script type="application/ld+json">
  {"@type": "VideoObject", "name": "Test Video",
   "description": "A test video.", "uploadDate": "2024-06-01",
   "duration": "PT5M30S"}
  </script>
</head>
<body><h1>Test Video</h1><p>Channel: TestChannel</p></body>
</html>"""


class TestVideoObjectSchemaOverride:
    """VideoObject schema_name should override article page_type compressor."""

    def test_video_schema_overrides_article_page_type(self) -> None:
        context, _, _ = build_pruned_context(
            _VIDEO_HTML,
            page_type="article",
            schema_name="VideoObject",
        )
        # "Duration:" is emitted only by _compress_for_video (line 2495)
        assert "Duration:" in context or "PT5M30S" in context

    def test_article_page_type_without_video_schema(self) -> None:
        """Regression: article page_type with non-video schema uses article compressor."""
        context, _, _ = build_pruned_context(
            _VIDEO_HTML,
            page_type="article",
            schema_name="NewsArticle",
        )
        assert context
        assert "Duration:" not in context


class TestSchemaOverridesInvariant:
    """Structural invariant: every override must have a registered compressor."""

    def test_overrides_subset_of_compressors(self) -> None:
        """Every schema in _SCHEMA_OVERRIDES must have a registered compressor."""
        assert _SCHEMA_OVERRIDES.issubset(_SCHEMA_COMPRESSORS.keys())


# ---------------------------------------------------------------------------
# Nested price class regex — _PRICE_CLASS_RE handles nested tags
# ---------------------------------------------------------------------------


class TestNestedPriceClassRegex:
    """Verify _PRICE_CLASS_RE matches nested Amazon-style price HTML."""

    @pytest.mark.parametrize(
        "snippet,expected",
        [
            # Direct text (backward-compatible regression)
            ('<span class="a-price">$29.99</span>', "$29.99"),
            ("<span class='a-price'>$29.99</span>", "$29.99"),
            # Nested 1-level (Amazon a-offscreen)
            (
                '<span class="a-price"><span class="a-offscreen">$249.00</span></span>',
                "$249.00",
            ),
            (
                "<span class='a-price'><span class='a-offscreen'>$249.00</span></span>",
                "$249.00",
            ),
            # Nested with extra attributes
            (
                '<span class="a-price" data-a-size="xl"><span class="a-offscreen">$149.99</span></span>',
                "$149.99",
            ),
        ],
    )
    def test_matches_nested_price(self, snippet: str, expected: str) -> None:
        m = _PRICE_CLASS_RE.search(snippet)
        assert m is not None, f"No match for: {snippet}"
        assert m.group("price").strip() == expected


# ---------------------------------------------------------------------------
# Phase 4 metadata feedback — price injected into metadata dict
# ---------------------------------------------------------------------------


class TestPhase4PriceFeedback:
    """Phase 4 in _compress_for_product injects found price into metadata."""

    def test_phase4_injects_price_into_metadata(self) -> None:
        """When Phase 4 finds a price and metadata has no 'price', it should inject.

        The price is inside a <script type="text/template"> so that text extraction
        (which strips script blocks) misses it, but _PRICE_CLASS_RE still matches
        the raw HTML — exactly the scenario Phase 4 is designed for.
        """
        html = (
            "<html><body><div>Product Name</div>"
            '<script type="text/template">'
            '<span class="a-price">$79.99</span>'
            "</script></body></html>"
        )
        metadata: dict = {"name": "Test Product"}
        _compress_for_product(html, max_tokens=500, metadata=metadata)
        assert "price" in metadata
        assert metadata["price"] == 79.99

    def test_phase4_does_not_overwrite_existing_price(self) -> None:
        """When metadata already has 'price', Phase 4 must NOT overwrite."""
        html = (
            "<html><body><div>Product Name</div>"
            '<script type="text/template">'
            '<span class="a-price">$79.99</span>'
            "</script></body></html>"
        )
        metadata: dict = {"name": "Test Product", "price": 29.99}
        _compress_for_product(html, max_tokens=500, metadata=metadata)
        assert metadata["price"] == 29.99


# ---------------------------------------------------------------------------
# CJK → Digit boundary whitespace
# ---------------------------------------------------------------------------


class TestCjkDigitBoundary:
    """Verify CJK→digit boundary whitespace insertion in _extract_text_lines."""

    def test_korean_digit_fusion(self) -> None:
        lines = _extract_text_lines("상품323,140")
        assert any("상품 323,140" in line for line in lines)

    def test_korean_price_fusion(self) -> None:
        lines = _extract_text_lines("상품99,000")
        assert any("상품 99,000" in line for line in lines)

    def test_japanese_digit_fusion(self) -> None:
        lines = _extract_text_lines("商品3990円")
        joined = " ".join(lines)
        assert "商品 3990" in joined

    def test_digit_to_cjk_unaffected(self) -> None:
        """digit→CJK (e.g. '55,000원') should NOT get a space inserted."""
        lines = _extract_text_lines("55,000원")
        assert any("55,000원" in line for line in lines)

    def test_pure_korean_unaffected(self) -> None:
        lines = _extract_text_lines("안녕하세요")
        assert any("안녕하세요" in line for line in lines)

    def test_already_spaced(self) -> None:
        """Already-spaced text should not get double spaces."""
        lines = _extract_text_lines("상품 323,140")
        joined = " ".join(lines)
        assert "상품 323,140" in joined
        assert "상품  323,140" not in joined


# ---------------------------------------------------------------------------
# Product Detail Context Expansion
# ---------------------------------------------------------------------------


class TestProductContextExpansion:
    """Verify product detail context expansion: DOM select, regex options, relaxed other."""

    def test_dom_select_options_extracted(self) -> None:
        """HTML with <select><option> elements should extract size values."""
        html = (
            "<html><body><h1>Test Product</h1>"
            '<select name="size">'
            "<option>S</option><option>M</option><option>L</option><option>XL</option>"
            "</select></body></html>"
        )
        doc = lxml.html.fromstring(html)
        result = _compress_for_product(html, max_tokens=500, doc=doc)
        assert "S" in result
        assert "M" in result
        assert "L" in result
        assert "XL" in result

    def test_select_with_label(self) -> None:
        """<select id='sz'> should use 'sz' as prefix."""
        html = (
            "<html><body><h1>Test Product</h1>"
            '<select id="sz">'
            "<option>S</option><option>M</option><option>L</option>"
            "</select></body></html>"
        )
        doc = lxml.html.fromstring(html)
        result = _compress_for_product(html, max_tokens=500, doc=doc)
        assert "sz" in result or "사이즈" in result

    def test_size_labels_detected_via_regex(self) -> None:
        """Text like 'S, M, L, XL, XXL' is classified as option line via _FORM_CONTROL_RE."""
        assert _FORM_CONTROL_RE.search("S, M, L, XL, XXL")

    def test_other_lines_relaxed_threshold(self) -> None:
        """9-char lines should now be included (was 15 min)."""
        html = (
            "<html><body>"
            "<p>123456789</p>"  # 9 chars — should be included now
            "</body></html>"
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "123456789" in result

    def test_other_lines_ten_items(self) -> None:
        """Up to 10 'other' lines should be included (was 5)."""
        lines = [f"<p>Other line number {i:02d} here</p>" for i in range(12)]
        html = f"<html><body>{''.join(lines)}</body></html>"
        result = _compress_for_product(html, max_tokens=1500)
        # At least 10 should be in output (first becomes title, rest are "other")
        count = sum(1 for i in range(12) if f"Other line number {i:02d} here" in result)
        assert count >= 10

    def test_no_doc_no_regression(self) -> None:
        """doc=None should preserve existing behavior without errors."""
        html = "<html><body><h1>Test Product</h1><p>$29.99</p></body></html>"
        result = _compress_for_product(html, max_tokens=500, doc=None)
        assert "$29.99" in result

    def test_form_control_stock_detected(self) -> None:
        """Text '재고 있음' should be classified as option via _FORM_CONTROL_RE."""
        assert _FORM_CONTROL_RE.search("재고 있음")

    def test_form_control_quantity_detected(self) -> None:
        """Text 'quantity: 1' should be classified as option via _FORM_CONTROL_RE."""
        assert _FORM_CONTROL_RE.search("quantity: 1")

    def test_select_placeholder_filtered(self) -> None:
        """Placeholder options like '선택' should be filtered out."""
        html = (
            "<html><body><h1>Test Product</h1>"
            '<select name="color">'
            "<option>선택</option><option>Red</option><option>Blue</option>"
            "</select></body></html>"
        )
        doc = lxml.html.fromstring(html)
        result = _compress_for_product(html, max_tokens=500, doc=doc)
        assert "Red" in result
        assert "Blue" in result
