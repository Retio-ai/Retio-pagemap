# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.check_urls module."""

from __future__ import annotations

import pytest

pytest.importorskip("pagemap.check_urls", reason="check_urls requires collect module (excluded from release)")

from pagemap.check_urls import (
    HealthStatus,
    _classify,
    _detect_redirect,
    _is_dummy_url,
)


class TestIsDummyUrl:
    """Tests for _is_dummy_url pre-filter."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            # COS: known dummy ID
            ("https://www.cos.com/kr/en/men/menswear/t-shirts/product.oversized-t-shirt.1234567001.html", True),
            # Musinsa: real product ID
            ("https://www.musinsa.com/products/3714962", False),
            # Naver Shopping: known dummy
            ("https://smartstore.naver.com/main/products/10000000001", True),
            # Coupang: real product
            ("https://www.coupang.com/vp/products/8796873601", False),
            # H&M: dummy pattern
            ("https://www2.hm.com/ko_kr/productpage.1234567001.html", True),
            # 29cm: real product
            ("https://www.29cm.co.kr/product/catalog/3761081", False),
            # Gmarket: known dummy
            ("https://item.gmarket.co.kr/Item?goodsCode=3500000001", True),
            # W Concept: known dummy
            ("https://www.wconcept.co.kr/Product/303123456", True),
            # SSF Shop: known dummy
            ("https://www.ssfshop.com/goods/MWGC012B0002", True),
            # SSF Shop: real-looking code (not in dummy set)
            ("https://www.ssfshop.com/goods/MWGC011A0001", False),
            # Search/listing URLs should never be dummy
            ("https://www.musinsa.com/search?keyword=%EC%B2%AD%EB%B0%94%EC%A7%80", False),
            ("https://www.musinsa.com/ranking/best", False),
        ],
    )
    def test_known_cases(self, url: str, expected: bool) -> None:
        is_dummy, reason = _is_dummy_url(url)
        assert is_dummy == expected
        if expected:
            assert reason  # should have a reason


class TestDetectRedirect:
    """Tests for _detect_redirect."""

    @pytest.mark.parametrize(
        "original,final,expected",
        [
            # Zara: product -> search
            (
                "https://www.zara.com/kr/ko/oversize-tshirt-p00722300.html",
                "https://www.zara.com/kr/ko/search",
                True,
            ),
            # Nike: product -> home
            (
                "https://www.nike.com/kr/t/air-force-1-07-shoe-NMmm1B",
                "https://www.nike.com/kr/",
                True,
            ),
            # Minor: www normalization (not a redirect)
            (
                "https://example.com/p/1",
                "https://www.example.com/p/1",
                False,
            ),
            # Trailing slash normalization (not a redirect)
            (
                "https://www.musinsa.com/products/123",
                "https://www.musinsa.com/products/123/",
                False,
            ),
            # Same URL (not a redirect)
            (
                "https://www.cos.com/kr/en/product.abc.123.html",
                "https://www.cos.com/kr/en/product.abc.123.html",
                False,
            ),
            # Product -> root (is a redirect)
            (
                "https://www.example.com/products/long-path/details",
                "https://www.example.com/",
                True,
            ),
        ],
    )
    def test_redirect_cases(self, original: str, final: str, expected: bool) -> None:
        is_redirect, reason = _detect_redirect(original, final)
        assert is_redirect == expected
        if expected:
            assert reason


class TestClassify:
    """Tests for _classify."""

    def test_dummy_url(self) -> None:
        """Dummy URL classified without HTML."""
        result = _classify(
            "https://www.cos.com/kr/en/product.oversized-t-shirt.1234567001.html",
            None,
            "https://www.cos.com/kr/en/product.oversized-t-shirt.1234567001.html",
        )
        assert result.status == HealthStatus.DUMMY
        assert "dummy" in result.reason.lower()

    def test_akamai_blocked(self) -> None:
        """H&M pattern: errors.edgesuite.net in small page."""
        html = "<html><body><p>errors.edgesuite.net reference</p></body></html>"
        result = _classify(
            "https://www2.hm.com/ko_kr/productpage.9876543001.html",
            html,
            "https://www2.hm.com/ko_kr/productpage.9876543001.html",
        )
        assert result.status == HealthStatus.BLOCKED
        assert "errors.edgesuite.net" in result.reason

    def test_expired_js_alert(self) -> None:
        """W Concept pattern: alert("존재하지않는") in a small-ish page."""
        # Need enough content to not be "minimal" (<200 tokens) but small enough to trigger expired patterns
        padding = "상품 정보 내용 " * 300  # ~1200 tokens (enough to pass minimal threshold)
        html = f'<html><body>{padding}<script>alert("존재하지않는상품입니다");history.go(-1);</script></body></html>'
        result = _classify(
            "https://www.wconcept.co.kr/Product/999888777",
            html,
            "https://www.wconcept.co.kr/Product/999888777",
        )
        assert result.status == HealthStatus.EXPIRED

    def test_expired_minimal_page(self) -> None:
        """Very small page with JS alert classified as EXPIRED (via minimal)."""
        html = '<html><body><script>alert("존재하지않는상품입니다");history.go(-1);</script></body></html>'
        result = _classify(
            "https://www.wconcept.co.kr/Product/999888777",
            html,
            "https://www.wconcept.co.kr/Product/999888777",
        )
        assert result.status == HealthStatus.EXPIRED

    def test_valid_large_page(self) -> None:
        """Large page with error strings in templates -> still valid."""
        # Generate a large page (>5000 tokens ~ >15000 chars)
        content = "상품 정보 " * 5000  # ~50K chars
        html = f'<html><body>{content}<script>var err = "alert(존재하지않는)";</script></body></html>'
        result = _classify(
            "https://www.musinsa.com/products/3714962",
            html,
            "https://www.musinsa.com/products/3714962",
        )
        assert result.status == HealthStatus.VALID

    def test_redirect_to_search(self) -> None:
        """Zara pattern: silent redirect to search page."""
        html = "<html><body><div>Search results for...</div></body></html>" + "x" * 5000
        result = _classify(
            "https://www.zara.com/kr/ko/product-p123.html",
            html,
            "https://www.zara.com/kr/ko/search",
        )
        assert result.status == HealthStatus.REDIRECT

    def test_no_html_blocked(self) -> None:
        """Navigation failure (None html) -> BLOCKED."""
        result = _classify(
            "https://www.example.com/product/1",
            None,
            "https://www.example.com/product/1",
        )
        assert result.status == HealthStatus.BLOCKED
        assert "Navigation failed" in result.reason

    def test_not_found_page(self) -> None:
        """Small page with 'page not found' -> EXPIRED."""
        html = "<html><body><h1>Page not found</h1><p>Sorry</p></body></html>"
        result = _classify(
            "https://www.example.com/product/1",
            html,
            "https://www.example.com/product/1",
        )
        assert result.status == HealthStatus.EXPIRED

    def test_result_is_immutable(self) -> None:
        """UrlHealthResult is frozen dataclass."""
        result = _classify(
            "https://www.musinsa.com/products/3714962",
            "<html><body>" + "x" * 10000 + "</body></html>",
            "https://www.musinsa.com/products/3714962",
        )
        with pytest.raises(AttributeError):
            result.status = HealthStatus.BLOCKED  # type: ignore[misc]
