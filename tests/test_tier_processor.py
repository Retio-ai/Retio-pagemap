# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A5: Unit tests for 3-tier chunk processing."""

from __future__ import annotations

import pytest

from pagemap.core.pruning import ChunkType, HtmlChunk, PruneReason
from pagemap.core.pruning.pruner import PruneDecision
from pagemap.core.pruning.tier_processor import (
    ChunkTier,
    apply_tier_processing,
    classify_tier,
    compress_chunk_tier_b,
    compute_tier_thresholds,
    generate_dropped_references,
    summarize_chunk_tier_c,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _chunk(
    text: str = "",
    html: str = "",
    tag: str = "div",
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    parent_xpath: str = "/html/body",
    attrs: dict | None = None,
) -> HtmlChunk:
    return HtmlChunk(
        xpath="/html/body/div",
        html=html or text,
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        parent_xpath=parent_xpath,
        attrs=attrs or {},
    )


def _decision(
    keep: bool = True,
    reason: PruneReason = PruneReason.IN_MAIN_TEXT,
    fitness: float | None = 0.5,
    score: float = 0.7,
) -> PruneDecision:
    return PruneDecision(keep=keep, reason=reason, score=score, fitness=fitness)


# ── Tier thresholds ───────────────────────────────────────────────────


class TestTierThresholds:
    def test_no_pressure(self):
        a, b = compute_tier_thresholds(0.0)
        assert a == pytest.approx(0.8)
        assert b == pytest.approx(0.4)

    def test_max_pressure(self):
        a, b = compute_tier_thresholds(1.0)
        assert a == pytest.approx(0.65)
        assert b == pytest.approx(0.25)

    def test_monotonic(self):
        a0, b0 = compute_tier_thresholds(0.0)
        a1, b1 = compute_tier_thresholds(1.0)
        assert a0 > a1
        assert b0 > b1


# ── Tier classification ──────────────────────────────────────────────


class TestClassifyTier:
    def test_meta_always_tier_a(self):
        d = _decision(reason=PruneReason.META_ALWAYS, fitness=0.1)
        assert classify_tier(d, 0.8, 0.4) == ChunkTier.A

    def test_schema_match_lowered_threshold(self):
        d = _decision(reason=PruneReason.SCHEMA_MATCH, fitness=0.65)
        # tier_a_min=0.8, lowered = 0.8*0.75 = 0.6, fitness=0.65 >= 0.6
        assert classify_tier(d, 0.8, 0.4) == ChunkTier.A

    def test_heading_minimum_tier_b(self):
        d = _decision(reason=PruneReason.IN_MAIN_HEADING, fitness=0.3)
        # fitness < tier_a but heading -> minimum B
        assert classify_tier(d, 0.8, 0.4) == ChunkTier.B

    def test_high_fitness_tier_a(self):
        d = _decision(reason=PruneReason.IN_MAIN_TEXT, fitness=0.9)
        assert classify_tier(d, 0.8, 0.4) == ChunkTier.A

    def test_medium_fitness_tier_b(self):
        d = _decision(reason=PruneReason.IN_MAIN_TEXT, fitness=0.5)
        assert classify_tier(d, 0.8, 0.4) == ChunkTier.B

    def test_low_fitness_tier_c(self):
        d = _decision(reason=PruneReason.IN_MAIN_TEXT, fitness=0.2)
        assert classify_tier(d, 0.8, 0.4) == ChunkTier.C


# ── Tier B compression ───────────────────────────────────────────────


class TestCompressChunkTierB:
    def test_text_truncated_to_sentences(self):
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        c = _chunk(text=text, html=text)
        result = compress_chunk_tier_b(c)
        assert result.text.endswith("...")
        assert "Fourth" not in result.text

    def test_short_text_unchanged(self):
        text = "Short. Text."
        c = _chunk(text=text, html=text)
        result = compress_chunk_tier_b(c)
        assert result.text == text

    def test_table_header_plus_three_rows(self):
        rows = "".join(f"<tr><td>Row {i}</td></tr>" for i in range(10))
        html = f"<table>{rows}</table>"
        c = _chunk(text="table data", html=html, chunk_type=ChunkType.TABLE)
        result = compress_chunk_tier_b(c)
        assert "+7 rows" in result.html

    def test_list_first_three_items(self):
        items = "".join(f"<li>Item {i}</li>" for i in range(8))
        html = f"<ul>{items}</ul>"
        c = _chunk(text="list data", html=html, chunk_type=ChunkType.LIST)
        result = compress_chunk_tier_b(c)
        assert "+5 items" in result.html

    def test_heading_unchanged(self):
        c = _chunk(text="Title", html="<h1>Title</h1>", chunk_type=ChunkType.HEADING)
        result = compress_chunk_tier_b(c)
        assert result.text == "Title"

    def test_cjk_sentence_boundary_korean(self):
        text = "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
        c = _chunk(text=text, html=text)
        result = compress_chunk_tier_b(c)
        assert result.text.endswith("...")
        assert "세 번째" not in result.text


# ── Tier C metadata ──────────────────────────────────────────────────


class TestTierCMetadata:
    def test_format_starts_with_section_bracket(self):
        c = _chunk(text="long content here")
        result = summarize_chunk_tier_c(c, "Products")
        assert result.text.startswith("[Section:")
        assert "Products" in result.text
        assert result.text.endswith("]")

    def test_label_preserved(self):
        c = _chunk(text="content")
        result = summarize_chunk_tier_c(c, "Navigation")
        assert "Navigation" in result.text


# ── Safety fallback ──────────────────────────────────────────────────


class TestSafetyFallback:
    def test_all_tier_c_falls_back_to_tier_a(self):
        decisions = [
            (_chunk(text="content 1"), _decision(fitness=0.1)),
            (_chunk(text="content 2"), _decision(fitness=0.05)),
        ]
        result = apply_tier_processing(decisions, budget_pressure=0.0)
        kept = [(c, d) for c, d in result if d.keep]
        assert all(d.tier == "A" for _, d in kept)


# ── Dropped references ───────────────────────────────────────────────


class TestDroppedReferences:
    def test_budget_dropped_get_reference(self):
        decisions = [
            (_chunk(text="kept"), _decision(keep=True)),
            (
                _chunk(text="dropped"),
                _decision(keep=False, reason=PruneReason.IN_MAIN_TEXT, fitness=0.3),
            ),
        ]
        decisions[1][1].reason_detail = "budget-drop(score=0.50)"
        refs = generate_dropped_references(decisions)
        assert len(refs) >= 1
        assert "[Section:" in refs[0].text

    def test_no_match_excluded(self):
        decisions = [
            (_chunk(text="kept"), _decision(keep=True)),
            (
                _chunk(text="rejected"),
                PruneDecision(keep=False, reason=PruneReason.NO_MATCH, score=0.0, reason_detail=""),
            ),
        ]
        refs = generate_dropped_references(decisions)
        assert len(refs) == 0

    def test_group_count_in_reference(self):
        c1 = _chunk(text="drop1", parent_xpath="/html/body/main")
        c2 = _chunk(text="drop2", parent_xpath="/html/body/main")
        d1 = PruneDecision(
            keep=False, reason=PruneReason.IN_MAIN_TEXT, score=0.5, reason_detail="budget-drop(score=0.50)"
        )
        d2 = PruneDecision(
            keep=False, reason=PruneReason.IN_MAIN_TEXT, score=0.4, reason_detail="budget-drop(score=0.40)"
        )
        refs = generate_dropped_references([(c1, d1), (c2, d2)])
        assert len(refs) == 1
        assert "2 items" in refs[0].text


# ── apply_tier_processing ────────────────────────────────────────────


class TestApplyTierProcessing:
    def test_kept_chunks_processed(self):
        decisions = [
            (_chunk(text="high fitness"), _decision(fitness=0.9)),
            (_chunk(text="low fitness"), _decision(fitness=0.1)),
        ]
        result = apply_tier_processing(decisions, budget_pressure=0.0)
        kept = [(c, d) for c, d in result if d.keep]
        tiers = {d.tier for _, d in kept}
        assert "A" in tiers or "C" in tiers  # at least some classification happened

    def test_rejected_chunks_unchanged(self):
        d = PruneDecision(keep=False, reason=PruneReason.NO_MATCH, score=0.0)
        decisions = [(_chunk(text="rejected"), d)]
        result = apply_tier_processing(decisions)
        assert not result[0][1].keep
