# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for product engine (Layer 1)."""

from __future__ import annotations

from pagemap.ecommerce.product_engine import analyze_product

from .conftest import PRODUCT_JSONLD


class TestProductEngine:
    def test_jsonld_product_extraction(self, sample_interactables):
        html = f"<html><body>{PRODUCT_JSONLD}<div>Product page content</div></body></html>"
        result = analyze_product(
            raw_html=html,
            html_lower=html.lower(),
            interactables=sample_interactables,
            metadata={},
            page_url="https://coupang.com/vp/products/123",
        )
        assert result.name == "오버핏 레더 자켓"
        assert result.price == 189000.0
        assert result.currency == "KRW"
        assert result.brand == "TestBrand"
        assert result.rating == 4.6
        assert result.review_count == 847
        assert result.availability == "in_stock"

    def test_options_extraction(self, sample_interactables):
        html = "<html><body><div>Product</div></body></html>"
        result = analyze_product(
            raw_html=html,
            html_lower=html.lower(),
            interactables=sample_interactables,
            metadata={},
            page_url="https://example.com/product/1",
        )
        assert len(result.options) >= 1
        size_opt = next((o for o in result.options if o.type == "size"), None)
        assert size_opt is not None
        assert "S" in size_opt.values
        assert "XL" in size_opt.values

    def test_metadata_fallback(self, make_interactable):
        html = "<html><body><h1>Test Product</h1><p>Price: ₩29,900</p></body></html>"
        metadata = {"title": "Test Product", "price": "29900", "brand": "TestBrand"}
        result = analyze_product(
            raw_html=html,
            html_lower=html.lower(),
            interactables=[],
            metadata=metadata,
            page_url="https://coupang.com/vp/products/456",
        )
        assert result.name == "Test Product"
        assert result.price == 29900.0

    def test_discount_calculation(self, make_interactable):
        jsonld = """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Jacket",
          "offers": {"price": "80", "highPrice": "100", "priceCurrency": "USD"}
        }
        </script>
        """
        html = f"<html><body>{jsonld}</body></html>"
        result = analyze_product(
            raw_html=html,
            html_lower=html.lower(),
            interactables=[],
            metadata={},
            page_url="https://example.com/product/1",
        )
        assert result.discount_pct == 20

    def test_out_of_stock(self, make_interactable):
        jsonld = """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Sold Out Item",
          "offers": {"price": "50", "availability": "https://schema.org/OutOfStock"}
        }
        </script>
        """
        html = f"<html><body>{jsonld}</body></html>"
        result = analyze_product(
            raw_html=html,
            html_lower=html.lower(),
            interactables=[],
            metadata={},
            page_url="https://example.com/product/2",
        )
        assert result.availability == "out_of_stock"

    def test_empty_product(self):
        result = analyze_product(
            raw_html="<html><body>empty</body></html>",
            html_lower="<html><body>empty</body></html>",
            interactables=[],
            metadata={},
            page_url="https://example.com/nothing",
        )
        assert result is not None
        assert result.name is None

    def test_never_raises(self):
        result = analyze_product(
            raw_html="",
            html_lower="",
            interactables=[],
            metadata={},
            page_url="",
        )
        assert result is not None
