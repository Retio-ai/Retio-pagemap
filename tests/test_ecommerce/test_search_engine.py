# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for search results engine (Layer 1)."""

from __future__ import annotations

from pagemap.ecommerce.search_engine import analyze_search_results

from .conftest import ITEMLIST_JSONLD


class TestSearchEngine:
    def test_basic_search(self, sample_interactables):
        html = f"<html><body>{ITEMLIST_JSONLD}<div>Results</div></body></html>"
        result = analyze_search_results(
            raw_html=html,
            html_lower=html.lower(),
            interactables=sample_interactables,
            metadata={},
            page_url="https://coupang.com/np/search?q=자켓&channel=user",
            navigation_hints={},
        )
        assert result.query == "자켓"
        assert len(result.cards) >= 3
        assert result.cards[0].name == "Product A"

    def test_query_extraction_from_url(self, sample_interactables):
        result = analyze_search_results(
            raw_html="<html><body>empty</body></html>",
            html_lower="<html><body>empty</body></html>",
            interactables=sample_interactables,
            metadata={},
            page_url="https://example.com/search?q=leather+jacket&page=1",
            navigation_hints={},
        )
        assert result.query == "leather jacket"

    def test_sort_control_detected(self, sample_interactables):
        result = analyze_search_results(
            raw_html="<html><body>content</body></html>",
            html_lower="<html><body>content</body></html>",
            interactables=sample_interactables,
            metadata={},
            page_url="https://example.com/search?q=test",
            navigation_hints={},
        )
        assert result.sort_ref == 6  # "Sort by" combobox
        assert len(result.sort_options) == 3

    def test_filter_refs_detected(self, sample_interactables):
        result = analyze_search_results(
            raw_html="<html><body>content</body></html>",
            html_lower="<html><body>content</body></html>",
            interactables=sample_interactables,
            metadata={},
            page_url="https://example.com/search?q=test",
            navigation_hints={},
        )
        assert 8 in result.filter_refs  # "필터" link

    def test_empty_on_no_query_param(self, sample_interactables):
        result = analyze_search_results(
            raw_html="<html><body>no items</body></html>",
            html_lower="<html><body>no items</body></html>",
            interactables=[],
            metadata={},
            page_url="https://example.com/catalog",
            navigation_hints={},
        )
        assert result.query is None
        assert len(result.cards) == 0

    def test_never_raises(self):
        """analyze_search_results must never raise."""
        result = analyze_search_results(
            raw_html="",
            html_lower="",
            interactables=[],
            metadata={},
            page_url="invalid-url",
            navigation_hints={},
        )
        assert result is not None
        assert len(result.cards) == 0
