# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 pruning confidence assessment."""

from __future__ import annotations

from dataclasses import dataclass

from pagemap.diagnostics.pruning_confidence import assess_pruning_confidence


@dataclass
class MockAomStats:
    total_nodes: int = 100
    removed_nodes: int = 40


@dataclass
class MockChunk:
    in_main: bool = True


@dataclass
class MockPruningResult:
    chunk_count_total: int = 20
    chunk_count_selected: int = 12
    selected_chunks: list = None
    aom_filter_stats: MockAomStats = None

    def __post_init__(self):
        if self.selected_chunks is None:
            self.selected_chunks = [MockChunk(in_main=True)]
        if self.aom_filter_stats is None:
            self.aom_filter_stats = MockAomStats()


class TestPruningConfidence:
    def test_good_pruning(self):
        result = assess_pruning_confidence(
            pruning_result=MockPruningResult(),
            page_type="product_detail",
            pruned_regions=set(),
            interactable_count=20,
        )
        assert result is not None
        assert 0.5 <= result.overall_confidence <= 1.0
        assert result.has_main_content is True

    def test_aggressive_removal(self):
        result = assess_pruning_confidence(
            pruning_result=MockPruningResult(
                aom_filter_stats=MockAomStats(total_nodes=100, removed_nodes=95),
            ),
            page_type="unknown",
            pruned_regions=set(),
            interactable_count=5,
        )
        assert result is not None
        # Aggressive removal should lower confidence
        assert result.removal_rate > 0.9

    def test_missed_regions_penalty(self):
        result_no_miss = assess_pruning_confidence(
            pruning_result=MockPruningResult(),
            page_type="product_detail",
            pruned_regions=set(),
            interactable_count=20,
        )
        result_with_miss = assess_pruning_confidence(
            pruning_result=MockPruningResult(),
            page_type="product_detail",
            pruned_regions={"header", "footer", "complementary"},
            interactable_count=20,
        )
        assert result_no_miss is not None
        assert result_with_miss is not None
        assert result_with_miss.overall_confidence <= result_no_miss.overall_confidence

    def test_no_main_content(self):
        result = assess_pruning_confidence(
            pruning_result=MockPruningResult(
                selected_chunks=[MockChunk(in_main=False)],
            ),
            page_type="product_detail",
            pruned_regions=set(),
            interactable_count=10,
        )
        assert result is not None
        assert result.has_main_content is False

    def test_none_pruning_result(self):
        result = assess_pruning_confidence(
            pruning_result=None,
            page_type="unknown",
            pruned_regions=set(),
            interactable_count=0,
        )
        assert result is None

    def test_score_clamped(self):
        """Score should always be between 0.0 and 1.0."""
        result = assess_pruning_confidence(
            pruning_result=MockPruningResult(
                chunk_count_total=100,
                chunk_count_selected=100,
                aom_filter_stats=MockAomStats(total_nodes=100, removed_nodes=50),
            ),
            page_type="product_detail",
            pruned_regions=set(),
            interactable_count=50,
        )
        assert result is not None
        assert 0.0 <= result.overall_confidence <= 1.0
