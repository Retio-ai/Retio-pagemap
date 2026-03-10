# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A2: Unit tests for PruningContext, StageAlphas, and compute_stage_alphas."""

from __future__ import annotations

import math

import pytest  # noqa: F401 — used by importorskip below

from pagemap.core.pruning.context import (
    PruningContext,
    StageAlphas,
    build_pruning_context,
    compute_stage_alphas,
)

# ---------------------------------------------------------------------------
# PruningContext dataclass tests
# ---------------------------------------------------------------------------


class TestPruningContext:
    def test_defaults(self):
        ctx = PruningContext()
        assert ctx.budget_pressure == 1.0
        assert ctx.content_density == 0.0
        assert ctx.page_complexity == 0.0

    def test_frozen(self):
        ctx = PruningContext()
        with pytest.raises(AttributeError):
            ctx.budget_pressure = 0.5  # type: ignore[misc]

    def test_nan_sanitized(self):
        ctx = PruningContext(budget_pressure=float("nan"), content_density=float("nan"))
        assert ctx.budget_pressure == 1.0
        assert ctx.content_density == 0.0

    def test_inf_sanitized(self):
        ctx = PruningContext(budget_pressure=float("inf"), content_density=float("-inf"))
        assert ctx.budget_pressure == 1.0
        assert ctx.content_density == 0.0

    def test_normal_values_preserved(self):
        ctx = PruningContext(page_complexity=0.5, content_density=0.3, budget_pressure=0.7)
        assert ctx.page_complexity == 0.5
        assert ctx.content_density == 0.3
        assert ctx.budget_pressure == 0.7


# ---------------------------------------------------------------------------
# StageAlphas dataclass tests
# ---------------------------------------------------------------------------


class TestStageAlphas:
    def test_defaults_all_one(self):
        a = StageAlphas()
        for name in ("aom", "grouping", "rule", "budget", "compress"):
            assert getattr(a, name) == 1.0

    def test_frozen(self):
        a = StageAlphas()
        with pytest.raises(AttributeError):
            a.aom = 2.0  # type: ignore[misc]

    def test_nan_sanitized(self):
        a = StageAlphas(aom=float("nan"), budget=float("inf"))
        assert a.aom == 1.0
        assert a.budget == 1.0

    def test_zero_sanitized(self):
        a = StageAlphas(rule=0.0, compress=-1.0)
        assert a.rule == 1.0
        assert a.compress == 1.0

    def test_positive_values_preserved(self):
        a = StageAlphas(aom=0.9, grouping=0.5)
        assert a.aom == 0.9
        assert a.grouping == 0.5


# ---------------------------------------------------------------------------
# compute_stage_alphas tests
# ---------------------------------------------------------------------------


class TestComputeStageAlphas:
    def test_no_pressure_all_ones(self):
        ctx = PruningContext(budget_pressure=1.0)
        alphas = compute_stage_alphas(ctx)
        assert alphas == StageAlphas()

    def test_above_one_pressure_all_ones(self):
        ctx = PruningContext(budget_pressure=1.5)
        alphas = compute_stage_alphas(ctx)
        assert alphas == StageAlphas()

    def test_tight_budget_elevated(self):
        ctx = PruningContext(budget_pressure=0.02, content_density=0.3)
        alphas = compute_stage_alphas(ctx)
        assert alphas.rule > 1.0
        assert alphas.budget > 1.0
        assert alphas.compress > 1.0
        assert alphas.grouping < 1.0

    def test_aom_threshold_safe(self):
        """AOM alpha max should keep threshold <= 0.575 (protect weight=0.6 sections)."""
        ctx = PruningContext(budget_pressure=0.01, content_density=0.0)
        alphas = compute_stage_alphas(ctx)
        effective_threshold = 0.5 * alphas.aom
        assert effective_threshold <= 0.575

    def test_grouping_inverse_relationship(self):
        """Higher pressure -> lower grouping alpha."""
        ctx_low = PruningContext(budget_pressure=0.8)
        ctx_high = PruningContext(budget_pressure=0.1)
        a_low = compute_stage_alphas(ctx_low)
        a_high = compute_stage_alphas(ctx_high)
        assert a_high.grouping <= a_low.grouping

    def test_clamped_within_range(self):
        """All alphas stay within documented ranges for extreme inputs."""
        for bp in (0.001, 0.01, 0.1, 0.5, 0.99):
            for cd in (0.0, 0.5, 1.0):
                ctx = PruningContext(budget_pressure=bp, content_density=cd)
                alphas = compute_stage_alphas(ctx)
                assert 0.8 <= alphas.aom <= 1.15
                assert 0.4 <= alphas.grouping <= 1.0
                assert 0.8 <= alphas.rule <= 1.5
                assert 1.0 <= alphas.budget <= 3.0
                assert 1.0 <= alphas.compress <= 2.0

    @pytest.mark.parametrize(
        "bp,cd,expected_direction",
        [
            (0.5, 0.7, "moderate"),
            (0.02, 0.3, "aggressive"),
            (0.06, 0.1, "very_aggressive"),
            (1.0, 0.5, "neutral"),
        ],
    )
    def test_roadmap_scenarios(self, bp, cd, expected_direction):
        ctx = PruningContext(budget_pressure=bp, content_density=cd)
        alphas = compute_stage_alphas(ctx)
        if expected_direction == "neutral":
            assert alphas == StageAlphas()
        elif expected_direction in ("aggressive", "very_aggressive"):
            assert alphas.budget > 1.5
            assert alphas.grouping < 0.8
        else:  # moderate
            assert alphas.budget > 1.0


# ---------------------------------------------------------------------------
# build_pruning_context tests
# ---------------------------------------------------------------------------


class TestBuildPruningContext:
    def _make_doc(self, html: str):
        import lxml.html

        return lxml.html.document_fromstring(html.encode())

    def test_simple_page(self):
        html = "<html><body><main><p>Hello world</p></main></body></html>"
        doc = self._make_doc(html)
        ctx = build_pruning_context(doc, html, raw_token_count=100, max_tokens=None)
        assert ctx.budget_pressure == 1.0
        assert 0.0 <= ctx.page_complexity <= 1.0
        assert 0.0 <= ctx.content_density <= 1.0

    def test_budget_pressure_with_max_tokens(self):
        html = "<html><body><p>x</p></body></html>"
        doc = self._make_doc(html)
        ctx = build_pruning_context(doc, html, raw_token_count=1000, max_tokens=100)
        assert ctx.budget_pressure == pytest.approx(0.1, abs=0.01)

    def test_no_max_tokens(self):
        html = "<html><body><p>x</p></body></html>"
        doc = self._make_doc(html)
        ctx = build_pruning_context(doc, html, raw_token_count=1000, max_tokens=None)
        assert ctx.budget_pressure == 1.0

    def test_zero_max_tokens(self):
        html = "<html><body><p>x</p></body></html>"
        doc = self._make_doc(html)
        ctx = build_pruning_context(doc, html, raw_token_count=1000, max_tokens=0)
        assert ctx.budget_pressure == 1.0


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402, I001
from hypothesis import strategies as st  # noqa: E402, I001


@given(bp=st.floats(0.001, 1.0), cd=st.floats(0.0, 1.0))
@settings(max_examples=200)
def test_monotonicity_budget_pressure(bp, cd):
    """Higher bp -> lower or equal budget alpha (less aggressive)."""
    bp_low = bp * 0.5
    ctx_tight = PruningContext(budget_pressure=bp_low, content_density=cd)
    ctx_loose = PruningContext(budget_pressure=bp, content_density=cd)
    a_tight = compute_stage_alphas(ctx_tight)
    a_loose = compute_stage_alphas(ctx_loose)
    # tighter budget => higher or equal budget alpha
    assert a_tight.budget >= a_loose.budget - 1e-9


@given(bp=st.floats(0.001, 1.0), cd=st.floats(0.0, 1.0))
@settings(max_examples=200)
def test_alphas_always_positive(bp, cd):
    """All alphas must be > 0."""
    ctx = PruningContext(budget_pressure=bp, content_density=cd)
    alphas = compute_stage_alphas(ctx)
    for name in ("aom", "grouping", "rule", "budget", "compress"):
        val = getattr(alphas, name)
        assert val > 0, f"{name}={val} is not positive"
        assert math.isfinite(val), f"{name}={val} is not finite"
