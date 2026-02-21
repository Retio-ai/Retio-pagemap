# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.refresh_urls module."""

from __future__ import annotations

import pytest

pytest.importorskip("pagemap.refresh_urls", reason="refresh_urls requires collect module (excluded from release)")

from pagemap.refresh_urls import RefreshResult, UrlChange, extract_product_urls


class TestExtractProductUrls:
    """Tests for extract_product_urls."""

    def test_basic_extraction(self) -> None:
        """Extract matching product links from HTML."""
        html = """
        <html><body>
        <a href="/products/12345">Product A</a>
        <a href="/products/67890">Product B</a>
        <a href="/about">About Us</a>
        </body></html>
        """
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"/products/\d+",
        )
        assert len(results) == 2
        assert "https://www.example.com/products/12345" in results
        assert "https://www.example.com/products/67890" in results

    def test_deduplication(self) -> None:
        """Same link appearing multiple times -> single result."""
        html = """
        <html><body>
        <a href="/products/12345">Product A</a>
        <a href="/products/12345">Product A again</a>
        <a href="/products/12345">Product A third time</a>
        </body></html>
        """
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"/products/\d+",
        )
        assert len(results) == 1

    def test_cross_domain_filtered(self) -> None:
        """Links to other domains excluded."""
        html = """
        <html><body>
        <a href="https://www.example.com/products/12345">Same domain</a>
        <a href="https://www.other.com/products/67890">Other domain</a>
        <a href="https://cdn.example.com/products/11111">CDN subdomain</a>
        </body></html>
        """
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"/products/\d+",
        )
        assert len(results) == 1
        assert "example.com" in results[0]

    def test_exclude_existing(self) -> None:
        """Already-valid URLs excluded from candidates."""
        html = """
        <html><body>
        <a href="/products/12345">Already valid</a>
        <a href="/products/67890">New product</a>
        </body></html>
        """
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"/products/\d+",
            exclude={"https://www.example.com/products/12345"},
        )
        assert len(results) == 1
        assert "67890" in results[0]

    def test_max_results(self) -> None:
        """Respects max_results limit."""
        links = "\n".join(f'<a href="/products/{i}">Product {i}</a>' for i in range(100))
        html = f"<html><body>{links}</body></html>"
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"/products/\d+",
            max_results=5,
        )
        assert len(results) == 5

    def test_preserves_document_order(self) -> None:
        """Results preserve document order."""
        html = """
        <html><body>
        <a href="/products/111">First</a>
        <a href="/products/222">Second</a>
        <a href="/products/333">Third</a>
        </body></html>
        """
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"/products/\d+",
        )
        assert results[0].endswith("/111")
        assert results[1].endswith("/222")
        assert results[2].endswith("/333")

    def test_invalid_pattern_returns_empty(self) -> None:
        """Invalid regex pattern returns empty list."""
        html = '<html><body><a href="/products/123">Link</a></body></html>'
        results = extract_product_urls(
            html,
            base_url="https://www.example.com/listing",
            product_pattern=r"[invalid",
        )
        assert results == []

    @pytest.mark.parametrize(
        "site,pattern,href,should_match",
        [
            ("musinsa", r"/products/\d+", "/products/3714962", True),
            ("musinsa", r"/products/\d+", "/ranking/best", False),
            ("29cm", r"/product/catalog/\d+", "/product/catalog/3761081", True),
            ("wconcept", r"/Product/\d+", "/Product/309876543", True),
            ("zara", r"/[\w.-]+-p\d+\.html", "/oversize-tshirt-p00722300.html", True),
            ("zara", r"/[\w.-]+-p\d+\.html", "/man-shirts-l737.html", False),
            ("cos", r"/product\.[\w.-]+\.\d+\.html", "/product.oversized-t-shirt.9876543001.html", True),
            ("hm", r"/productpage\.\d+\.html", "/productpage.9876543001.html", True),
            ("uniqlo", r"/products/E\d+-\d+", "/products/E475377-000/00", True),
            ("nike", r"/t/[\w-]+", "/t/air-force-1-07-shoe-NMmm1B", True),
            ("nike", r"/t/[\w-]+", "/kr/w/men-shoes-nik1zy7ok", False),
            ("coupang", r"/vp/products/\d+", "/vp/products/8796873601", True),
        ],
    )
    def test_per_site_patterns(self, site: str, pattern: str, href: str, should_match: bool) -> None:
        """Product URL patterns match expected URLs per site."""
        html = f'<html><body><a href="{href}">Link</a></body></html>'
        results = extract_product_urls(
            html,
            base_url=f"https://www.{site}.com/listing",
            product_pattern=pattern,
        )
        if should_match:
            assert len(results) == 1, f"Expected match for {site}: {href}"
        else:
            assert len(results) == 0, f"Expected no match for {site}: {href}"


class TestRefreshResult:
    """Tests for data types."""

    def test_frozen_url_change(self) -> None:
        """UrlChange is immutable."""
        change = UrlChange(
            site_id="musinsa",
            page_type="product_detail",
            index=0,
            old_url="https://old.com",
            new_url="https://new.com",
            source_listing_url="https://listing.com",
            reason="auto-replaced",
        )
        with pytest.raises(AttributeError):
            change.new_url = "https://modified.com"  # type: ignore[misc]

    def test_refresh_result_defaults(self) -> None:
        """RefreshResult has sensible defaults."""
        result = RefreshResult(site_id="test")
        assert result.changes == []
        assert result.skipped_reason == ""
        assert result.candidate_count == 0
        assert result.listing_url_used == ""

    def test_dry_run_preserves_original(self) -> None:
        """UrlChange records preserve original URL info."""
        change = UrlChange(
            site_id="musinsa",
            page_type="product_detail",
            index=0,
            old_url="https://old.com/products/123",
            new_url="https://old.com/products/456",
            source_listing_url="https://old.com/listing",
            reason="auto-replaced",
        )
        assert change.old_url == "https://old.com/products/123"
        assert change.new_url == "https://old.com/products/456"
