# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A1: Unit tests for task preference vector and fitness scoring."""

from __future__ import annotations

import pytest

from pagemap.core.pruning import ChunkType, HtmlChunk, PruneReason
from pagemap.core.pruning.pruner import PruneDecision
from pagemap.core.pruning.task_vector import (
    ChunkVector,
    TaskHint,
    compute_chunk_vector,
    compute_fitness_scores,
    cosine_similarity,
    validate_task_hint,
)

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import (  # noqa: E402
    given,
    settings,
    strategies as st,  # noqa: E402
)

# ── Helpers ───────────────────────────────────────────────────────────


def _chunk(
    text: str = "",
    html: str = "",
    tag: str = "div",
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    attrs: dict | None = None,
    in_main: bool = False,
) -> HtmlChunk:
    return HtmlChunk(
        xpath="/html/body/div",
        html=html or text,
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs=attrs or {},
        in_main=in_main,
    )


# ── Cosine similarity property-based tests ────────────────────────────


class TestCosineSimilarity:
    @given(
        a=st.tuples(*[st.floats(0.01, 10)] * 4),
        b=st.tuples(*[st.floats(0.01, 10)] * 4),
    )
    @settings(max_examples=200)
    def test_range(self, a, b):
        sim = cosine_similarity(a, b)
        assert -1e-9 <= sim <= 1.0 + 1e-9

    @given(v=st.tuples(*[st.floats(0.01, 10)] * 4))
    @settings(max_examples=200)
    def test_self_similarity_is_one(self, v):
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-9)

    @given(
        a=st.tuples(*[st.floats(0.01, 10)] * 4),
        b=st.tuples(*[st.floats(0.01, 10)] * 4),
    )
    @settings(max_examples=200)
    def test_symmetry(self, a, b):
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a), abs=1e-9)

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity((0, 0, 0, 0), (1, 2, 3, 4)) == 0.0
        assert cosine_similarity((1, 2, 3, 4), (0, 0, 0, 0)) == 0.0


# ── ChunkVector unit tests ───────────────────────────────────────────


class TestChunkVector:
    def test_text_block_high_text_density(self):
        long_text = "x" * 200
        c = _chunk(text=long_text, html=f"<p>{long_text}</p>")
        vec = compute_chunk_vector(c)
        assert vec.text_density > 0.5

    def test_form_chunk_high_interactive(self):
        c = _chunk(
            text="Submit form",
            html='<form><input type="text"><button>Go</button></form>',
            chunk_type=ChunkType.FORM,
        )
        vec = compute_chunk_vector(c)
        assert vec.interactive_ratio >= 0.5  # base 0.5 for FORM

    def test_list_with_links_high_link_density(self):
        c = _chunk(
            text="Link1 Link2 Link3",
            html='<ul><li><a href="#">Link1</a></li><li><a href="#">Link2</a></li><li><a href="#">Link3</a></li></ul>',
            chunk_type=ChunkType.LIST,
        )
        vec = compute_chunk_vector(c)
        assert vec.link_density > 0.3

    def test_empty_chunk_zero_vector(self):
        c = _chunk(text="", html="")
        vec = compute_chunk_vector(c)
        assert vec == ChunkVector(0.0, 0.0, 0.0, 0.0)

    def test_nan_inf_sanitized(self):
        vec = ChunkVector(float("nan"), float("inf"), float("-inf"), float("nan"))
        assert vec.text_density == 0.0
        assert vec.link_density == 0.0
        assert vec.interactive_ratio == 0.0
        assert vec.semantic_weight == 0.0

    def test_semantic_weight_itemprop(self):
        c = _chunk(text="Product", html="<span>Product</span>", attrs={"itemprop": "name"}, in_main=True)
        vec = compute_chunk_vector(c)
        assert vec.semantic_weight >= 0.6  # itemprop(0.4) + in_main(0.2)

    def test_heading_semantic(self):
        c = _chunk(text="Title", html="<h1>Title</h1>", tag="h1", chunk_type=ChunkType.HEADING, in_main=True)
        vec = compute_chunk_vector(c)
        assert vec.semantic_weight >= 0.3  # in_main(0.2) + HEADING(0.1)


# ── compute_fitness_scores ────────────────────────────────────────────


class TestFitnessScores:
    def test_meta_always_fitness_one(self):
        c = _chunk(text="meta", chunk_type=ChunkType.META)
        d = PruneDecision(keep=True, reason=PruneReason.META_ALWAYS, score=1.0)
        compute_fitness_scores([(c, d)], "search")
        assert d.fitness == 1.0

    def test_schema_match_floor(self):
        c = _chunk(text="x", html="<span>x</span>")
        d = PruneDecision(keep=True, reason=PruneReason.SCHEMA_MATCH, score=0.9)
        compute_fitness_scores([(c, d)], "detail")
        assert d.fitness is not None
        assert d.fitness >= 0.5

    def test_search_favors_link_heavy_chunks(self):
        link_chunk = _chunk(
            text="nav link1 link2",
            html='<nav><a href="#">link1</a> <a href="#">link2</a></nav>',
        )
        text_chunk = _chunk(text="x" * 200, html=f"<p>{'x' * 200}</p>")
        d_link = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7)
        d_text = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7)
        compute_fitness_scores([(link_chunk, d_link), (text_chunk, d_text)], "search")
        assert d_link.fitness is not None
        assert d_text.fitness is not None
        assert d_link.fitness > d_text.fitness

    def test_detail_favors_text_chunks(self):
        text_chunk = _chunk(text="x" * 200, html=f"<p>{'x' * 200}</p>", in_main=True, attrs={"itemprop": "desc"})
        link_chunk = _chunk(
            text="nav",
            html='<nav><a href="#">link</a> <a href="#">link</a></nav>',
        )
        d_text = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7)
        d_link = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7)
        compute_fitness_scores([(text_chunk, d_text), (link_chunk, d_link)], "detail")
        assert d_text.fitness is not None
        assert d_link.fitness is not None
        assert d_text.fitness > d_link.fitness


# ── validate_task_hint ────────────────────────────────────────────────


class TestValidateTaskHint:
    @pytest.mark.parametrize("hint", ["search", "detail", "cart", "form", "general"])
    def test_valid_hints_passthrough(self, hint):
        assert validate_task_hint(hint) == hint

    def test_unknown_falls_to_general(self):
        assert validate_task_hint("unknown_task") == "general"

    def test_none_returns_none(self):
        assert validate_task_hint(None) is None

    def test_case_insensitive(self):
        assert validate_task_hint("SEARCH") == "search"
        assert validate_task_hint("Detail") == "detail"

    def test_whitespace_stripped(self):
        assert validate_task_hint("  cart  ") == "cart"


# ── TaskHint enum ─────────────────────────────────────────────────────


class TestTaskHintEnum:
    def test_all_values(self):
        assert set(TaskHint) == {"search", "detail", "cart", "form", "general"}

    def test_str_enum(self):
        assert TaskHint.SEARCH == "search"
        assert isinstance(TaskHint.DETAIL, str)


# ── _effective_score unit tests ───────────────────────────────────────


class TestEffectiveScore:
    def test_fitness_none_returns_score(self):
        from pagemap.core.pruning.pruner import _effective_score

        d = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7, fitness=None)
        assert _effective_score(d) == 0.7

    def test_fitness_one_returns_score(self):
        from pagemap.core.pruning.pruner import _effective_score

        d = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7, fitness=1.0)
        assert _effective_score(d) == pytest.approx(0.7)

    def test_fitness_zero_reduces_score(self):
        from pagemap.core.pruning.pruner import _effective_score

        d = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7, fitness=0.0)
        assert _effective_score(d) == pytest.approx(0.7 * 0.7)

    def test_high_fitness_higher_than_low(self):
        from pagemap.core.pruning.pruner import _effective_score

        d_high = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7, fitness=0.9)
        d_low = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.7, fitness=0.2)
        assert _effective_score(d_high) > _effective_score(d_low)

    def test_formula_matches_spec(self):
        from pagemap.core.pruning.pruner import _effective_score

        # score * (BASE + (1-BASE) * fitness), BASE=0.7
        d = PruneDecision(keep=True, reason=PruneReason.IN_MAIN_TEXT, score=0.9, fitness=0.3)
        expected = 0.9 * (0.7 + 0.3 * 0.3)
        assert _effective_score(d) == pytest.approx(expected)
