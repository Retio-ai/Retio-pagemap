"""Tests for Phase A: Score-based pruning, budget selection, and backward compat.

Covers:
  A-1: score assignment via _REASON_SCORES
  A-2: apply_budget_selection() — greedy deletion, META protection, depth tiebreak
  Backward compat: prune_chunks() without max_tokens still works
"""

from __future__ import annotations

from pagemap.pruning import ChunkType, HtmlChunk, PruneReason
from pagemap.pruning.pruner import (
    _REASON_SCORES,
    PruneDecision,
    apply_budget_selection,
    prune_chunks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    in_main: bool = True,
    tag: str = "div",
    attrs: dict | None = None,
    xpath: str = "",
    parent_xpath: str = "",
    depth: int = 3,
) -> HtmlChunk:
    if not xpath:
        xpath = "/html/body/main/div[1]" if in_main else "/html/body/div[1]"
    if not parent_xpath:
        parent_xpath = "/html/body/main" if in_main else "/html/body"
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs=attrs or {},
        parent_xpath=parent_xpath,
        depth=depth,
        in_main=in_main,
    )


# ---------------------------------------------------------------------------
# A-1: Score assignment
# ---------------------------------------------------------------------------


class TestScoreAssignment:
    """Verify that prune_chunks assigns correct scores."""

    def test_meta_always_gets_score_1(self):
        chunk = _make_chunk('{"@type":"Product"}', ChunkType.META, tag="script")
        results = prune_chunks([chunk], "Product")
        _, decision = results[0]
        assert decision.score == 1.0
        assert decision.reason == PruneReason.META_ALWAYS

    def test_schema_match_gets_score_09(self):
        chunk = _make_chunk("Samsung Galaxy $599", tag="h1")
        results = prune_chunks([chunk], "Product", has_main=True)
        _, decision = results[0]
        assert decision.reason == PruneReason.SCHEMA_MATCH
        assert decision.score == 0.9

    def test_in_main_heading_gets_score_085(self):
        chunk = _make_chunk("Section Title", ChunkType.HEADING, tag="h2")
        results = prune_chunks([chunk], "Generic", has_main=True)
        _, decision = results[0]
        assert decision.reason == PruneReason.IN_MAIN_HEADING
        assert decision.score == 0.85

    def test_no_match_gets_score_0(self):
        chunk = _make_chunk("x", in_main=False)
        results = prune_chunks([chunk], "Generic", has_main=True)
        _, decision = results[0]
        assert decision.reason == PruneReason.NO_MATCH
        assert decision.score == 0.0

    def test_all_reasons_have_scores(self):
        """Every PruneReason must have a score mapping."""
        for reason in PruneReason:
            assert reason in _REASON_SCORES, f"Missing score for {reason}"

    def test_scores_are_between_0_and_1(self):
        for reason, score in _REASON_SCORES.items():
            assert 0.0 <= score <= 1.0, f"{reason} has invalid score {score}"


# ---------------------------------------------------------------------------
# A-2: Budget selection
# ---------------------------------------------------------------------------


class TestBudgetSelection:
    """Verify apply_budget_selection greedy deletion."""

    def _make_results(self, items: list[tuple[str, float, bool, int]]) -> list[tuple[HtmlChunk, PruneDecision]]:
        """Create results from (text, score, keep, depth) tuples."""
        results = []
        for i, (text, score, keep, depth) in enumerate(items):
            chunk = _make_chunk(
                text,
                xpath=f"/html/body/div[{i}]",
                depth=depth,
            )
            decision = PruneDecision(
                keep=keep,
                reason=PruneReason.IN_MAIN_TEXT if keep else PruneReason.NO_MATCH,
                score=score,
            )
            results.append((chunk, decision))
        return results

    def test_no_drop_when_under_budget(self):
        results = self._make_results(
            [
                ("short text", 0.7, True, 3),
                ("another text", 0.5, True, 3),
            ]
        )
        apply_budget_selection(results, max_tokens=100)
        assert all(d.keep for _, d in results)

    def test_drops_lowest_score_first(self):
        results = self._make_results(
            [
                ("A" * 100, 0.9, True, 3),  # ~28 tok
                ("B" * 100, 0.5, True, 3),  # ~28 tok
                ("C" * 100, 0.3, True, 3),  # ~28 tok
            ]
        )
        # Budget = 60 tok, total ~84 tok → must drop ~24 tok
        apply_budget_selection(results, max_tokens=60)
        # C (score 0.3) should be dropped
        assert results[0][1].keep is True
        assert results[1][1].keep is True
        assert results[2][1].keep is False
        assert "budget-drop" in results[2][1].reason_detail

    def test_meta_always_never_dropped(self):
        results = []
        meta_chunk = _make_chunk('{"@type":"Product"}', ChunkType.META, tag="script")
        meta_dec = PruneDecision(keep=True, reason=PruneReason.META_ALWAYS, score=1.0)
        results.append((meta_chunk, meta_dec))

        text_chunk = _make_chunk("B" * 100, xpath="/html/body/div[2]")
        text_dec = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.5)
        results.append((text_chunk, text_dec))

        # Tiny budget — only META survives
        apply_budget_selection(results, max_tokens=5)
        assert results[0][1].keep is True  # META protected
        assert results[1][1].keep is False

    def test_depth_tiebreak(self):
        """Same score → deeper node dropped first."""
        results = self._make_results(
            [
                ("A" * 100, 0.7, True, 2),  # shallow
                ("B" * 100, 0.7, True, 5),  # deep
            ]
        )
        apply_budget_selection(results, max_tokens=30)
        # Should drop B (deeper) first
        assert results[0][1].keep is True
        assert results[1][1].keep is False

    def test_already_rejected_not_affected(self):
        results = self._make_results(
            [
                ("A" * 100, 0.9, True, 3),
                ("B" * 100, 0.0, False, 3),  # already rejected
            ]
        )
        apply_budget_selection(results, max_tokens=20)
        # B was already rejected, shouldn't be double-counted
        assert results[1][1].keep is False


# ---------------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """prune_chunks still works without scoring / budget features."""

    def test_decisions_have_score_field(self):
        chunk = _make_chunk("Hello world product $100", tag="h1")
        results = prune_chunks([chunk], "Product")
        _, decision = results[0]
        assert hasattr(decision, "score")
        assert isinstance(decision.score, float)

    def test_default_prune_chunks_no_crash(self):
        chunks = [
            _make_chunk("Product $99", tag="h1"),
            _make_chunk("Description " * 20),
            _make_chunk("x", in_main=False),
        ]
        results = prune_chunks(chunks, "Product", has_main=True)
        assert len(results) == 3
