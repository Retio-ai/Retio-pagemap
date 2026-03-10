# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A2: Pipeline integration tests for context-aware pruning intensity."""

from __future__ import annotations

from pagemap.core.pruning.context import StageAlphas
from pagemap.core.pruning.pipeline import prune_page


def _make_html(body_content: str, *, with_main: bool = True) -> str:
    """Build a minimal valid HTML page."""
    if with_main:
        return f"<html><body><main>{body_content}</main></body></html>"
    return f"<html><body>{body_content}</body></html>"


def _large_body(n_paragraphs: int = 200, chars_per_p: int = 500) -> str:
    """Generate a large body with many paragraphs."""
    return "\n".join(f"<p>{'Lorem ipsum dolor sit amet. ' * (chars_per_p // 28)}</p>" for _ in range(n_paragraphs))


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_no_max_tokens_identical_output(self):
        """When max_tokens=None, alphas should all be 1.0 (existing behavior)."""
        html = _make_html("<h1>Test</h1><p>" + "word " * 50 + "</p>")
        result = prune_page(html, "site1", "page1", "Product")
        assert result.stage_alphas == StageAlphas()
        assert result.errors == []

    def test_max_tokens_zero_graceful(self):
        """max_tokens=0 should not crash — 0 chunks fallback."""
        html = _make_html("<h1>Title</h1><p>" + "content " * 30 + "</p>")
        result = prune_page(html, "site1", "page1", "Product", max_tokens=0)
        assert result.pruned_html  # should return something (original or pruned)
        assert result.stage_alphas is not None


# ---------------------------------------------------------------------------
# Alpha activation
# ---------------------------------------------------------------------------


class TestAlphaActivation:
    def test_small_page_moderate_alpha(self):
        """Small page + generous budget -> alphas near 1.0."""
        html = _make_html("<h1>Title</h1><p>" + "word " * 100 + "</p>")
        result = prune_page(html, "s", "p", "Product", max_tokens=5000)
        assert result.stage_alphas is not None
        # With generous budget, pressure is low, alphas should be near 1.0
        if result.stage_alphas.budget > 1.0:
            # Some pressure exists
            assert result.stage_alphas.budget < 2.0

    def test_large_page_tight_budget_aggressive(self):
        """Large page + tight budget -> elevated alphas."""
        body = _large_body(n_paragraphs=100)
        html = _make_html(body)
        result = prune_page(html, "s", "p", "Generic", max_tokens=200)
        assert result.stage_alphas is not None
        assert result.stage_alphas.budget > 1.0
        assert result.stage_alphas.grouping < 1.0

    def test_stage_alphas_on_result(self):
        """PruningResult should always have stage_alphas set."""
        html = _make_html("<h1>Test</h1><p>Hello world content</p>")
        result = prune_page(html, "s", "p", "Product")
        assert result.stage_alphas is not None
        assert isinstance(result.stage_alphas, StageAlphas)


# ---------------------------------------------------------------------------
# Schema / META preservation
# ---------------------------------------------------------------------------


class TestSchemaPreservation:
    def test_schema_match_always_kept(self):
        """META/SCHEMA_MATCH chunks must survive even under extreme alpha."""
        html = (
            "<html><head>"
            '<script type="application/ld+json">{"@type":"Product","name":"Widget"}</script>'
            '<meta property="og:title" content="Widget"/>'
            "</head><body><main>"
            "<h1>Widget</h1>" + "".join(f"<p>{'filler ' * 100}</p>" for _ in range(50)) + "</main></body></html>"
        )
        result = prune_page(html, "s", "p", "Product", max_tokens=100)
        # JSON-LD and OG meta should be in pruned output
        assert "application/ld+json" in result.pruned_html
        assert "Widget" in result.pruned_html


# ---------------------------------------------------------------------------
# Budget selection activation
# ---------------------------------------------------------------------------


class TestAomAlpha:
    def test_aom_threshold_affected_by_alpha(self):
        """Under budget pressure, AOM filter should use elevated threshold."""
        body = _large_body(n_paragraphs=50)
        html = _make_html(body)
        r_no_budget = prune_page(html, "s", "p", "Generic")
        r_budget = prune_page(html, "s", "p", "Generic", max_tokens=200)
        # With pressure, AOM removes more nodes → fewer chunks total (or equal)
        assert r_budget.aom_filter_stats.removed_nodes >= r_no_budget.aom_filter_stats.removed_nodes


class TestBudgetSelection:
    def test_budget_selection_activated(self):
        """When max_tokens is set, budget selection should reduce chunks."""
        body = _large_body(n_paragraphs=50)
        html = _make_html(body)
        result_no_budget = prune_page(html, "s", "p", "Generic")
        result_with_budget = prune_page(html, "s", "p", "Generic", max_tokens=500)
        # With budget, fewer chunks should be selected
        assert result_with_budget.chunk_count_selected <= result_no_budget.chunk_count_selected

    def test_budget_selection_cjk(self):
        """Korean content + budget → pruned_token_count should respect budget."""
        korean_p = "<p>" + "안녕하세요 이것은 한국어 테스트 문장입니다. " * 20 + "</p>"
        body = "\n".join([korean_p] * 10)
        html = _make_html(body)
        result = prune_page(html, "s", "p", "Generic", max_tokens=5000)
        assert result.pruned_token_count > 0
        # Pruned output should be within ~2x of budget (HTML overhead)
        assert result.pruned_token_count <= 5000 * 2
        unexpected = [e for e in result.errors if "Unexpected" in e]
        assert unexpected == []


# ---------------------------------------------------------------------------
# Per-stage independence
# ---------------------------------------------------------------------------


class TestPerStageIndependence:
    def test_per_stage_alphas_differ(self):
        """Under pressure, different stages should get different alpha values."""
        body = _large_body(n_paragraphs=80)
        html = _make_html(body)
        result = prune_page(html, "s", "p", "Generic", max_tokens=300)
        a = result.stage_alphas
        assert a is not None
        # budget and compress should differ from grouping (inverse relationship)
        if a.budget > 1.0:
            assert a.grouping < 1.0
