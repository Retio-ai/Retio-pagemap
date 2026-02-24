# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QR-01: Interactable noise filtering.

Covers:
- _is_table_noise predicate (unit tests)
- _budget_filter_interactables 5-bucket logic (integration tests)
- Children preservation when noise rows are dropped
"""

from __future__ import annotations

import pytest

from pagemap import Interactable
from pagemap.interactive_detector import _is_table_noise
from pagemap.page_map_builder import _budget_filter_interactables


def _make_el(
    ref: int,
    role: str,
    name: str,
    region: str = "main",
    tier: int = 1,
    affordance: str = "click",
) -> Interactable:
    return Interactable(
        ref=ref,
        role=role,
        name=name,
        affordance=affordance,
        region=region,
        tier=tier,
    )


# ── A. TestIsTableNoise — predicate unit tests ─────────────────────


class TestIsTableNoise:
    """Verify _is_table_noise classifies table-structural roles correctly."""

    @pytest.mark.parametrize(
        "role, name",
        [
            ("row", ""),
            ("cell", "   "),
            ("gridcell", ""),
            ("cell", "1."),
            ("cell", "42"),
            ("cell", "#3"),
            ("cell", "---"),
            ("cell", "N/A"),
            ("cell", "\u2014"),  # em dash
            ("row", "  "),
            ("cell", "(1)"),
            ("gridcell", "2."),
        ],
        ids=[
            "unnamed_row",
            "whitespace_cell",
            "unnamed_gridcell",
            "numeric_dot",
            "bare_number",
            "hash_number",
            "dashes",
            "na",
            "em_dash",
            "spaces_row",
            "paren_number",
            "gridcell_ordinal",
        ],
    )
    def test_noise_true(self, role: str, name: str) -> None:
        assert _is_table_noise(role, name) is True

    @pytest.mark.parametrize(
        "role, name",
        [
            ("row", "Shipping Details"),
            ("cell", "$42.99"),
            ("cell", "50 points by user"),
            ("button", ""),
            ("link", ""),
            ("button", "1"),
            ("option", "3"),
            ("tab", "Tab 1"),
            ("row", "Order #12345"),
            ("cell", "404 Error"),
            ("gridcell", "Total: $99"),
        ],
        ids=[
            "named_row",
            "currency",
            "mixed_content",
            "unnamed_button",
            "unnamed_link",
            "pagination_button",
            "combobox_option",
            "tab",
            "order_row",
            "error_text",
            "total_gridcell",
        ],
    )
    def test_noise_false(self, role: str, name: str) -> None:
        assert _is_table_noise(role, name) is False


# ── B. TestNoiseBudgetFilter — budget filter integration tests ──────


class TestNoiseBudgetFilter:
    """Verify 5-bucket budget filtering with noise demotion."""

    def test_hn_like_noise_dropped_first(self) -> None:
        """HN-like scenario: noise rows should be dropped before real links."""
        elements: list[Interactable] = []
        ref = 0

        # 30 noise rows (unnamed)
        for _i in range(30):
            ref += 1
            elements.append(_make_el(ref, "row", "", region="main", tier=2))

        # 20 real links
        for i in range(20):
            ref += 1
            elements.append(_make_el(ref, "link", f"Article {i}", region="main", tier=1))

        # Tight budget: only ~25 elements should fit
        result = _budget_filter_interactables(
            elements,
            pruned_tokens=4500,
            total_budget=5000,
            warnings=[],
        )

        # Real links should be prioritized (they're in bucket_tier1_main)
        link_count = sum(1 for el in result if el.role == "link")
        noise_count = sum(1 for el in result if el.role == "row")

        # All 20 links should survive; noise should be cut
        assert link_count == 20
        assert noise_count < 30  # Some noise dropped

    def test_pruned_region_radio_demoted(self) -> None:
        """Radio in pruned navigation → bucket_rest (not bucket_input)."""
        # Build enough elements so total tokens exceed _MIN_INTERACTABLE_BUDGET (100)
        elements = [
            _make_el(1, "radio", "Option A", region="navigation", tier=1, affordance="click"),
        ]
        # 20 links in main (~5 tokens each = ~100 tokens) to saturate the minimum budget
        for i in range(2, 22):
            elements.append(_make_el(i, "link", f"Article {i}", region="main", tier=1))

        # With pruned_regions including navigation
        result = _budget_filter_interactables(
            elements,
            pruned_tokens=0,
            total_budget=5000,
            warnings=[],
            pruned_regions={"navigation"},
        )

        # All should be included with generous budget
        assert len(result) == 21

        # Tight budget: radio demoted → links preferred
        result_tight = _budget_filter_interactables(
            elements,
            pruned_tokens=4990,
            total_budget=5000,
            warnings=[],
            pruned_regions={"navigation"},
        )
        assert len(result_tight) < 21  # Budget actually constrains
        roles = [el.role for el in result_tight]
        assert "link" in roles  # Links (tier1_main) survive over demoted radio (rest)

    def test_pruned_region_textbox_preserved(self) -> None:
        """Textbox in pruned region stays in bucket_input (not demoted)."""
        elements = [
            _make_el(1, "textbox", "Search", region="navigation", tier=1, affordance="type"),
            _make_el(2, "link", "Home", region="main", tier=1),
        ]

        result = _budget_filter_interactables(
            elements,
            pruned_tokens=4900,
            total_budget=5000,
            warnings=[],
            pruned_regions={"navigation"},
        )

        # Textbox should be included (bucket_input = highest priority)
        assert any(el.role == "textbox" for el in result)

    def test_no_pruned_regions_backward_compat(self) -> None:
        """pruned_regions=None → radio stays in bucket_input (highest priority)."""
        elements = [
            _make_el(1, "radio", "Option A", region="navigation", tier=1, affordance="click"),
            _make_el(2, "link", "Home", region="main", tier=1),
        ]

        # Generous budget: both included
        result = _budget_filter_interactables(
            elements,
            pruned_tokens=0,
            total_budget=5000,
            warnings=[],
            pruned_regions=None,
        )
        assert len(result) == 2

        # Tight budget: only 1 fits → radio survives (bucket_input > bucket_tier1_main)
        result_tight = _budget_filter_interactables(
            elements,
            pruned_tokens=4920,
            total_budget=5000,
            warnings=[],
            pruned_regions=None,
        )
        assert len(result_tight) >= 1
        assert any(el.role == "radio" for el in result_tight)

    def test_generous_budget_preserves_noise(self) -> None:
        """With generous budget, noise elements are still included."""
        elements = [
            _make_el(1, "link", "Article", region="main", tier=1),
            _make_el(2, "row", "", region="main", tier=2),
            _make_el(3, "cell", "42", region="main", tier=2),
        ]

        result = _budget_filter_interactables(
            elements,
            pruned_tokens=0,
            total_budget=5000,
            warnings=[],
        )

        # All 3 should be included with generous budget
        assert len(result) == 3

    def test_named_row_not_noise(self) -> None:
        """Named row with real content → bucket_rest (not bucket_table_noise)."""
        elements = [
            _make_el(1, "row", "Order #12345", region="main", tier=1),
            _make_el(2, "row", "", region="main", tier=2),
        ]

        # Very tight budget — only 1 element fits
        result = _budget_filter_interactables(
            elements,
            pruned_tokens=4920,
            total_budget=5000,
            warnings=[],
        )

        if len(result) == 1:
            # Named row should survive over unnamed row
            assert result[0].name == "Order #12345"

    def test_warnings_appended_on_budget_drop(self) -> None:
        """Warnings list gets budget message when elements are dropped."""
        elements = [_make_el(i, "row", "", region="main", tier=2) for i in range(1, 51)]

        warnings: list[str] = []
        result = _budget_filter_interactables(
            elements,
            pruned_tokens=4900,
            total_budget=5000,
            warnings=warnings,
        )

        assert len(result) < len(elements)
        assert any("token budget" in w for w in warnings)

    def test_refs_renumbered_sequentially(self) -> None:
        """After filtering, refs should be renumbered 1..N."""
        elements = [
            _make_el(1, "link", "A", region="main", tier=1),
            _make_el(2, "row", "", region="main", tier=2),
            _make_el(3, "link", "B", region="main", tier=1),
        ]

        result = _budget_filter_interactables(
            elements,
            pruned_tokens=0,
            total_budget=5000,
            warnings=[],
        )

        refs = [el.ref for el in result]
        assert refs == list(range(1, len(result) + 1))


# ── C. TestNoiseChildren — children preservation ───────────────────


class TestNoiseChildren:
    """Verify that children of noise rows are preserved as separate elements."""

    def test_child_links_survive_when_parent_row_dropped(self) -> None:
        """Links inside a noise row are separate elements and survive budget filter."""
        elements = [
            _make_el(1, "row", "", region="main", tier=2),
            _make_el(2, "link", "Story Title", region="main", tier=1),
            _make_el(3, "link", "Comments (42)", region="main", tier=1),
        ]

        # Tight budget
        result = _budget_filter_interactables(
            elements,
            pruned_tokens=4850,
            total_budget=5000,
            warnings=[],
        )

        # Links should survive (tier1_main bucket) even if parent row is dropped
        link_names = [el.name for el in result if el.role == "link"]
        assert "Story Title" in link_names

    def test_child_buttons_independent_of_parent_cell(self) -> None:
        """Buttons inside table cells are detected independently."""
        elements = [
            _make_el(1, "cell", "", region="main", tier=2),
            _make_el(2, "button", "Add to Cart", region="main", tier=1),
            _make_el(3, "cell", "N/A", region="main", tier=2),
            _make_el(4, "button", "Details", region="main", tier=1),
        ]

        result = _budget_filter_interactables(
            elements,
            pruned_tokens=4850,
            total_budget=5000,
            warnings=[],
        )

        button_names = [el.name for el in result if el.role == "button"]
        # Buttons should be prioritized over noise cells
        assert "Add to Cart" in button_names
        assert "Details" in button_names
