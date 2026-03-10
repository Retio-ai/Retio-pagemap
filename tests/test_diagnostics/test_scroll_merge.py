# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 scroll merge deduplication."""

from __future__ import annotations

from dataclasses import dataclass

from pagemap.diagnostics import ScrollMergeState
from pagemap.diagnostics.scroll_merge import merge_scroll_results


@dataclass
class MockCard:
    name: str = ""
    price: float | None = None
    url: str | None = None


class TestScrollMerge:
    def test_first_scroll(self):
        state = ScrollMergeState()
        cards = [MockCard(name="Product A", price=10.0), MockCard(name="Product B", price=20.0)]
        result = merge_scroll_results(
            state=state,
            new_cards=cards,
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result is not None
        assert result.total_accumulated == 2
        assert result.new_this_scroll == 2
        assert result.duplicates_removed == 0

    def test_second_scroll_with_duplicates(self):
        state = ScrollMergeState()
        cards1 = [MockCard(name="Product A", price=10.0), MockCard(name="Product B", price=20.0)]
        merge_scroll_results(
            state=state,
            new_cards=cards1,
            page_url="https://example.com/search",
            page_type="search_results",
        )

        # Second scroll with one duplicate and one new
        cards2 = [MockCard(name="Product A", price=10.0), MockCard(name="Product C", price=30.0)]
        result = merge_scroll_results(
            state=state,
            new_cards=cards2,
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result is not None
        assert result.total_accumulated == 3
        assert result.new_this_scroll == 1
        assert result.duplicates_removed == 1

    def test_url_dedup(self):
        state = ScrollMergeState()
        cards = [
            MockCard(name="A", url="https://example.com/p/1"),
            MockCard(name="B", url="https://example.com/p/2"),
        ]
        merge_scroll_results(
            state=state,
            new_cards=cards,
            page_url="https://example.com/search",
            page_type="search_results",
        )

        cards2 = [MockCard(name="Different Name", url="https://example.com/p/1")]
        result = merge_scroll_results(
            state=state,
            new_cards=cards2,
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result is not None
        assert result.duplicates_removed == 1

    def test_url_change_resets_state(self):
        state = ScrollMergeState()
        cards = [MockCard(name="Product A", price=10.0)]
        merge_scroll_results(
            state=state,
            new_cards=cards,
            page_url="https://example.com/page1",
            page_type="search_results",
        )
        assert state.total_seen == 1

        # Different URL → reset
        cards2 = [MockCard(name="Product A", price=10.0)]
        result = merge_scroll_results(
            state=state,
            new_cards=cards2,
            page_url="https://example.com/page2",
            page_type="search_results",
        )
        assert result is not None
        assert result.total_accumulated == 1  # reset, so only new card counted
        assert result.duplicates_removed == 0

    def test_empty_cards(self):
        state = ScrollMergeState()
        result = merge_scroll_results(
            state=state,
            new_cards=[],
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result is None

    def test_state_reset(self):
        state = ScrollMergeState()
        state.accumulated_keys.add("test")
        state.total_seen = 5
        state.scroll_count = 3
        state.reset()
        assert state.total_seen == 0
        assert state.scroll_count == 0
        assert len(state.accumulated_keys) == 0

    def test_dict_cards(self):
        """Cards from ecommerce engine are dicts, not dataclass objects."""
        state = ScrollMergeState()
        cards = [
            {"name": "Product A", "price": 10.0, "url": "https://example.com/p/1"},
            {"name": "Product B", "price": 20.0, "url": "https://example.com/p/2"},
        ]
        result = merge_scroll_results(
            state=state,
            new_cards=cards,
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result is not None
        assert result.total_accumulated == 2
        assert result.new_this_scroll == 2

        # Same URL → duplicate
        cards2 = [{"name": "X", "url": "https://example.com/p/1"}]
        result2 = merge_scroll_results(
            state=state,
            new_cards=cards2,
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result2 is not None
        assert result2.duplicates_removed == 1

    def test_dict_cards_name_price_dedup(self):
        """Dict cards without URL use name+price for dedup."""
        state = ScrollMergeState()
        cards = [{"name": "Nike Air Max", "price": 129.99}]
        merge_scroll_results(
            state=state,
            new_cards=cards,
            page_url="https://example.com/search",
            page_type="search_results",
        )

        cards2 = [{"name": "Nike Air Max", "price": 129.99}]
        result = merge_scroll_results(
            state=state,
            new_cards=cards2,
            page_url="https://example.com/search",
            page_type="search_results",
        )
        assert result is not None
        assert result.duplicates_removed == 1
        assert result.total_accumulated == 1
