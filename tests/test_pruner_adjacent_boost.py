"""Tests for Phase A-3: Adjacent chunk boosting with precision guard.

Covers:
  - Boost range (±2 neighbours)
  - Flip logic (score ≥ 0.45 && text ≥ 5 chars)
  - parent_xpath guard (only same-parent neighbours boosted)
  - Min text guard
  - Precision guard flip count
"""

from __future__ import annotations

from pagemap.pruning import ChunkType, HtmlChunk, PruneReason
from pagemap.pruning.pruner import (
    _REASON_SCORES,
    PruneDecision,
    boost_adjacent_chunks,
)


def _make_chunk(
    text: str,
    xpath: str,
    parent_xpath: str = "/html/body/main",
    tag: str = "div",
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    depth: int = 3,
) -> HtmlChunk:
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs={},
        parent_xpath=parent_xpath,
        depth=depth,
        in_main=True,
    )


def _make_decision(keep: bool, reason: PruneReason, score: float | None = None) -> PruneDecision:
    if score is None:
        score = _REASON_SCORES.get(reason, 0.0)
    return PruneDecision(keep=keep, reason=reason, score=score)


class TestAdjacentBoost:
    """boost_adjacent_chunks() tests."""

    def test_no_schema_match_no_boost(self):
        results = [
            (_make_chunk("text", "/html/body/main/div[1]"), _make_decision(True, PruneReason.IN_MAIN_TEXT)),
            (_make_chunk("text", "/html/body/main/div[2]"), _make_decision(False, PruneReason.NO_MATCH)),
        ]
        count = boost_adjacent_chunks(results)
        assert count == 0

    def test_adjacent_chunks_get_boosted(self):
        """Chunks ±1 from SCHEMA_MATCH get +0.15 boost."""
        parent = "/html/body/main"
        results = [
            (
                _make_chunk("rejected but enough text here", "/html/body/main/div[1]", parent),
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            ),
            (
                _make_chunk("Product $100", "/html/body/main/div[2]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
            (
                _make_chunk("more text that was rejected", "/html/body/main/div[3]", parent),
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            ),
        ]
        count = boost_adjacent_chunks(results)
        # Both neighbours get +0.15, reaching 0.50 ≥ 0.45 → flipped
        assert count == 2
        assert results[0][1].keep is True
        assert results[0][1].reason == PruneReason.ADJACENT_BOOST
        assert results[2][1].keep is True
        assert results[2][1].reason == PruneReason.ADJACENT_BOOST

    def test_distance_2_gets_half_boost(self):
        """Chunks ±2 from SCHEMA_MATCH get +0.075 boost."""
        parent = "/html/body/main"
        results = [
            (
                _make_chunk("far text", "/html/body/main/div[1]", parent),
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            ),
            (
                _make_chunk("near text enough text", "/html/body/main/div[2]", parent),
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            ),
            (
                _make_chunk("Product $100", "/html/body/main/div[3]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
        ]
        count = boost_adjacent_chunks(results)
        # div[1] is distance 2: +0.075 → 0.425 < 0.45 → NOT flipped
        # div[2] is distance 1: +0.15 → 0.50 ≥ 0.45 → flipped
        assert results[0][1].keep is False  # not enough boost
        assert results[1][1].keep is True  # flipped
        assert count == 1

    def test_different_parent_not_boosted(self):
        """Chunks with different parent_xpath are not boosted."""
        results = [
            (
                _make_chunk("rejected text here", "/html/body/aside/div[1]", parent_xpath="/html/body/aside"),
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            ),
            (
                _make_chunk("Product $100", "/html/body/main/div[1]", parent_xpath="/html/body/main"),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
        ]
        count = boost_adjacent_chunks(results)
        assert count == 0
        assert results[0][1].keep is False

    def test_min_text_guard(self):
        """Chunks with text < 5 chars are not flipped."""
        parent = "/html/body/main"
        results = [
            (
                _make_chunk("hi", "/html/body/main/div[1]", parent),  # only 2 chars
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            ),
            (
                _make_chunk("Product $100", "/html/body/main/div[2]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
        ]
        count = boost_adjacent_chunks(results)
        # Score boosted to 0.50, but text too short → not flipped
        assert count == 0
        assert results[0][1].keep is False
        # Score should still be boosted though
        assert results[0][1].score > 0.35

    def test_already_kept_not_counted_as_flip(self):
        """Already-kept chunks are not counted as flips."""
        parent = "/html/body/main"
        results = [
            (
                _make_chunk("already kept text", "/html/body/main/div[1]", parent),
                _make_decision(True, PruneReason.IN_MAIN_TEXT),
            ),
            (
                _make_chunk("Product $100", "/html/body/main/div[2]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
        ]
        count = boost_adjacent_chunks(results)
        assert count == 0  # no flips, was already kept

    def test_flip_count_matches_actual_flips(self):
        """Returned flip count matches actual number of keep=True transitions."""
        parent = "/html/body/main"
        results = [
            (
                _make_chunk("some rejected text here", f"/html/body/main/div[{i}]", parent),
                _make_decision(False, PruneReason.NO_MATCH, score=0.35),
            )
            for i in range(1, 4)
        ] + [
            (
                _make_chunk("Product $100", "/html/body/main/div[4]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
        ]
        count = boost_adjacent_chunks(results)
        actual_flips = sum(1 for _, d in results if d.reason == PruneReason.ADJACENT_BOOST)
        assert count == actual_flips

    def test_score_capped_at_1(self):
        """Boosted score should not exceed 1.0."""
        parent = "/html/body/main"
        results = [
            (
                _make_chunk("already high score text", "/html/body/main/div[1]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH, score=0.95),
            ),
            (
                _make_chunk("Product $100", "/html/body/main/div[2]", parent),
                _make_decision(True, PruneReason.SCHEMA_MATCH),
            ),
        ]
        boost_adjacent_chunks(results)
        assert results[0][1].score <= 1.0
