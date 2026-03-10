# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for site hints (Layer 2)."""

from __future__ import annotations

from pagemap.ecommerce.site_hints import _HINTS, apply_site_hints


class TestSiteHints:
    def test_hints_sorted_by_priority(self):
        """Registry must be sorted by priority."""
        priorities = [h.priority for h in _HINTS]
        assert priorities == sorted(priorities)

    def test_amazon_price_hint(self):
        """Amazon price fallback from a-price class."""
        html = '<span class="a-price"><span class="a-offscreen">$29.99</span></span>'
        data: dict = {"price_text": None, "currency": None}
        result, applied = apply_site_hints(
            url="https://www.amazon.com/dp/B08N5WRWNW",
            ecom_data=data,
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
        )
        assert "amazon_price" in applied
        assert result["price_text"] == "$29.99"

    def test_amazon_hint_skipped_when_price_exists(self):
        """Hint should not override existing price."""
        html = '<span class="a-price"><span class="a-offscreen">$29.99</span></span>'
        data: dict = {"price_text": "$19.99", "currency": "USD"}
        result, applied = apply_site_hints(
            url="https://www.amazon.com/dp/B08N5WRWNW",
            ecom_data=data,
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
        )
        assert "amazon_price" not in applied
        assert result["price_text"] == "$19.99"

    def test_coupang_currency_hint(self):
        """Coupang default currency KRW."""
        data: dict = {"currency": None}
        result, applied = apply_site_hints(
            url="https://www.coupang.com/vp/products/123",
            ecom_data=data,
            raw_html="<html>content</html>",
            html_lower="<html>content</html>",
            page_type="product_detail",
        )
        assert "coupang_currency" in applied
        assert result["currency"] == "KRW"

    def test_non_matching_domain_skipped(self):
        data: dict = {"price_text": None}
        result, applied = apply_site_hints(
            url="https://www.example.com/product/1",
            ecom_data=data,
            raw_html="<html>content</html>",
            html_lower="<html>content</html>",
            page_type="product_detail",
        )
        assert len(applied) == 0

    def test_wrong_page_type_skipped(self):
        data: dict = {"price_text": None}
        result, applied = apply_site_hints(
            url="https://www.amazon.com/dp/B08N5WRWNW",
            ecom_data=data,
            raw_html="<html>content</html>",
            html_lower="<html>content</html>",
            page_type="article",  # Not product_detail
        )
        assert "amazon_price" not in applied

    def test_never_raises(self):
        data: dict = {}
        result, applied = apply_site_hints(
            url="",
            ecom_data=data,
            raw_html="",
            html_lower="",
            page_type="",
        )
        assert isinstance(result, dict)
        assert isinstance(applied, list)

    def test_idempotency(self):
        """Applying hints twice should not change result."""
        html = '<span class="a-price"><span class="a-offscreen">$29.99</span></span>'
        data: dict = {"price_text": None}
        result1, _ = apply_site_hints(
            url="https://www.amazon.com/dp/B08N5WRWNW",
            ecom_data=data,
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
        )
        result2, applied2 = apply_site_hints(
            url="https://www.amazon.com/dp/B08N5WRWNW",
            ecom_data=result1,
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
        )
        assert "amazon_price" not in applied2  # Already has value
        assert result1["price_text"] == result2["price_text"]
