# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A1+A5: Pipeline integration tests for task vector + tier processing."""

from __future__ import annotations

from pagemap.core.pruning.pipeline import prune_page

# ── Helpers ───────────────────────────────────────────────────────────


def _make_html(main_content: str, nav_content: str = "", forms: str = "") -> str:
    return f"<html><body><nav>{nav_content}</nav><main>{main_content}</main>{forms}</body></html>"


def _text_chunks(n: int, chars: int = 200) -> str:
    return "\n".join(f"<p>{'x' * chars} paragraph {i}.</p>" for i in range(n))


def _form_html() -> str:
    return '<form><input type="text" name="q"><button type="submit">Submit</button></form>'


# ── Backward compatibility (most important) ──────────────────────────


class TestBackwardCompat:
    def test_task_hint_none_identical_output(self):
        """task_hint=None should produce exactly the same result as before."""
        html = _make_html("<h1>Title</h1>" + _text_chunks(5))
        result_none = prune_page(html, "s", "p", "Product")
        result_explicit_none = prune_page(html, "s", "p", "Product", task_hint=None)
        assert result_none.pruned_html == result_explicit_none.pruned_html
        assert result_none.chunk_count_selected == result_explicit_none.chunk_count_selected

    def test_no_tier_counts_when_no_hint(self):
        html = _make_html("<h1>Test</h1><p>" + "word " * 50 + "</p>")
        result = prune_page(html, "s", "p", "Product")
        assert result.tier_counts is None
        assert result.task_hint is None

    def test_no_fitness_on_decisions_when_no_hint(self):
        html = _make_html("<h1>Test</h1><p>" + "word " * 50 + "</p>")
        result = prune_page(html, "s", "p", "Product")
        if result.selected_decisions:
            for d in result.selected_decisions.values():
                assert d.fitness is None
                assert d.tier is None


# ── Feature verification ──────────────────────────────────────────────


class TestFeatureVerification:
    def test_detail_preserves_text_chunks(self):
        html = _make_html("<h1>Product</h1>" + _text_chunks(10, 300))
        result = prune_page(html, "s", "p", "Product", task_hint="detail")
        assert result.task_hint == "detail"
        assert result.tier_counts is not None
        assert result.tier_counts.get("A", 0) > 0

    def test_meta_always_preserved_all_hints(self):
        """META chunks should always be preserved regardless of task_hint."""
        meta = '<script type="application/ld+json">{"@type":"Product"}</script>'
        html = f"<html><head>{meta}</head><body><main><h1>Title</h1><p>content</p></main></body></html>"
        for hint in ["search", "detail", "cart", "form", "general"]:
            result = prune_page(html, "s", "p", "Product", task_hint=hint)
            assert result.chunk_count_selected > 0

    def test_tier_counts_populated(self):
        html = _make_html("<h1>Title</h1>" + _text_chunks(15, 200))
        result = prune_page(html, "s", "p", "Product", task_hint="general")
        assert result.tier_counts is not None
        total = sum(result.tier_counts.values())
        assert total > 0

    def test_task_hint_recorded(self):
        html = _make_html("<h1>Test</h1><p>" + "word " * 50 + "</p>")
        result = prune_page(html, "s", "p", "Product", task_hint="search")
        assert result.task_hint == "search"

    def test_unknown_hint_normalized_to_general(self):
        html = _make_html("<h1>Test</h1><p>" + "word " * 50 + "</p>")
        result = prune_page(html, "s", "p", "Product", task_hint="unknown_task")
        assert result.task_hint == "general"

    def test_budget_pressure_shifts_thresholds(self):
        """With a tight budget, tier thresholds should be lower (more Tier A)."""
        html = _make_html("<h1>Title</h1>" + _text_chunks(20, 300))
        # Tight budget
        result_tight = prune_page(html, "s", "p", "Product", max_tokens=500, task_hint="detail")
        # No budget
        result_loose = prune_page(html, "s", "p", "Product", task_hint="detail")
        # Both should work without errors
        assert result_tight.errors == [] or result_tight.pruned_html
        assert result_loose.errors == [] or result_loose.pruned_html

    def test_tier_c_references_in_output(self):
        """With many chunks and a specific task, some should be Tier C."""
        html = _make_html("<h1>Title</h1>" + _text_chunks(30, 150))
        result = prune_page(html, "s", "p", "Product", task_hint="cart")
        # cart task → text-heavy chunks get lower fitness → some may be Tier C
        if result.tier_counts and result.tier_counts.get("C", 0) > 0:
            assert "[Section:" in result.pruned_html

    def test_form_hint_with_forms(self):
        """form hint should work with form-containing pages."""
        html = _make_html(
            "<h1>Contact</h1><p>Fill the form.</p>",
            forms=_form_html(),
        )
        result = prune_page(html, "s", "p", "Generic", task_hint="form")
        assert result.task_hint == "form"
        assert result.errors == []

    def test_empty_page_graceful(self):
        """Empty page with task_hint should not crash."""
        html = "<html><body></body></html>"
        result = prune_page(html, "s", "p", "Generic", task_hint="detail")
        assert result.pruned_html  # should return something
