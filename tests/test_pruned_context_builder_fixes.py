# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for pruned_context_builder code-quality fixes.

Covers:
  - Issue 1: Phase 4 price regex single-quote support
  - Issue 2: Video description budget safety factor (0.85)
  - Issue 3+4: _SCHEMA_OVERRIDES module-level frozenset with VideoObject
"""

from __future__ import annotations

import pytest

from pagemap.pruned_context_builder import (
    _PRICE_CLASS_RE,
    _SCHEMA_COMPRESSORS,
    _SCHEMA_OVERRIDES,
    PRICE_PATTERN,
    _compress_for_product,
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
