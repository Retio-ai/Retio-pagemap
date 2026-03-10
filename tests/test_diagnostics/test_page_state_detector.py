# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 page state detector."""

from __future__ import annotations

from pagemap.diagnostics import PageFailureState
from pagemap.diagnostics.page_state_detector import detect_page_state


class TestBotBlocked:
    def test_page_type_blocked(self, make_interactable):
        result = detect_page_state(
            raw_html="<html><body>blocked</body></html>",
            html_lower="<html><body>blocked</body></html>",
            page_type="blocked",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
            http_status=403,
        )
        assert result is not None
        assert result.state == PageFailureState.BOT_BLOCKED
        assert result.confidence >= 0.90
        assert "page_type=blocked" in result.signals

    def test_text_pattern_bot_detected(self, make_interactable):
        html = "<html><body>bot detected please verify</body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="unknown",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
        )
        assert result is not None
        assert result.state == PageFailureState.BOT_BLOCKED

    def test_korean_bot_blocked(self, make_interactable):
        html = "<html><body>접근 차단 비정상적인 접근입니다</body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="unknown",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
        )
        assert result is not None
        assert result.state == PageFailureState.BOT_BLOCKED

    def test_japanese_bot_blocked(self, make_interactable):
        html = "<html><body>アクセスが拒否されました</body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="unknown",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
        )
        assert result is not None
        assert result.state == PageFailureState.BOT_BLOCKED


class TestErrorPage:
    def test_http_404(self, make_interactable):
        html = "<html><body>Not Found</body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="unknown",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
            http_status=404,
        )
        assert result is not None
        assert result.state == PageFailureState.ERROR_PAGE

    def test_text_error_page(self, make_interactable):
        html = "<html><body><h1>404 Not Found</h1><p>Page not found</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="unknown",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
        )
        assert result is not None
        assert result.state == PageFailureState.ERROR_PAGE


class TestOutOfStock:
    def test_sold_out_product(self, make_interactable):
        html = "<html><body><h1>Product</h1><p>Sold Out</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com/product/1",
        )
        assert result is not None
        assert result.state == PageFailureState.OUT_OF_STOCK

    def test_korean_out_of_stock(self, make_interactable):
        html = "<html><body><h1>상품명</h1><p>품절</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://coupang.com/product/1",
        )
        assert result is not None
        assert result.state == PageFailureState.OUT_OF_STOCK

    def test_out_of_stock_not_on_non_product(self, make_interactable):
        """Out of stock should only trigger on product_detail pages."""
        html = "<html><body><h1>Search</h1><p>Sold Out item</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="search_results",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com/search",
        )
        # Should NOT be OUT_OF_STOCK on search pages
        assert result is None or result.state != PageFailureState.OUT_OF_STOCK


class TestEmptyResults:
    def test_no_results_found(self, make_interactable):
        html = "<html><body><h1>Search</h1><p>No results found</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="search_results",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com/search?q=test",
        )
        assert result is not None
        assert result.state == PageFailureState.EMPTY_RESULTS

    def test_korean_no_results(self, make_interactable):
        html = "<html><body><h1>검색</h1><p>검색 결과가 없습니다</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="search_results",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://coupang.com/search?q=test",
        )
        assert result is not None
        assert result.state == PageFailureState.EMPTY_RESULTS


class TestHealthyPage:
    def test_normal_product_page(self, make_interactable):
        html = "<html><body><h1>Great Product</h1><p>$29.99 - In Stock</p><button>Buy Now</button></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="product_detail",
            barrier=None,
            interactables=[make_interactable(ref=i) for i in range(20)],
            metadata={},
            url="https://example.com/product/1",
        )
        assert result is None

    def test_normal_search_page(self, make_interactable):
        html = "<html><body><h1>Search Results</h1><p>Showing 50 results</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="search_results",
            barrier=None,
            interactables=[make_interactable(ref=i) for i in range(30)],
            metadata={},
            url="https://example.com/search?q=shoes",
        )
        assert result is None


class TestPriority:
    def test_bot_blocked_takes_priority_over_error(self, make_interactable):
        """Bot blocked has higher priority than error page."""
        html = "<html><body><h1>403 Forbidden</h1><p>bot detected</p></body></html>"
        result = detect_page_state(
            raw_html=html,
            html_lower=html.lower(),
            page_type="blocked",
            barrier=None,
            interactables=[make_interactable()],
            metadata={},
            url="https://example.com",
            http_status=403,
        )
        assert result is not None
        assert result.state == PageFailureState.BOT_BLOCKED


class TestNeverRaises:
    def test_empty_html(self, make_interactable):
        result = detect_page_state(
            raw_html="",
            html_lower="",
            page_type="unknown",
            barrier=None,
            interactables=[],
            metadata={},
            url="",
        )
        # Should not raise, returns None
        assert result is None

    def test_none_values_handled(self, make_interactable):
        result = detect_page_state(
            raw_html="<html></html>",
            html_lower="<html></html>",
            page_type="unknown",
            barrier=None,
            interactables=[],
            metadata={},
            url="https://example.com",
            http_status=None,
        )
        assert result is None
