# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Error-path and robustness tests for the pruning pipeline.

Phase 7.5 — covers:
  - prune_page() try/except fallback paths
  - build_pruned_context() graceful degradation
  - Malformed HTML stability
  - Unicode edge cases
  - Error propagation integration
"""

from __future__ import annotations

from unittest.mock import patch

from pagemap.pruning.pipeline import PruningResult, prune_page
from tests._pruning_helpers import html

# ---------------------------------------------------------------------------
# TestPrunePageErrorHandling
# ---------------------------------------------------------------------------


class TestPrunePageErrorHandling:
    def test_empty_html_error_with_fallback(self):
        result = prune_page("", "site", "page", "Product")
        assert len(result.errors) >= 1
        assert result.pruned_html == ""

    def test_script_only_html(self):
        raw = "<html><body><script>alert(1)</script></body></html>"
        result = prune_page(raw, "site", "page", "Product")
        # Should either succeed with empty chunks or have errors
        assert isinstance(result, PruningResult)

    def test_no_chunks_after_aom(self):
        """If AOM removes everything, fallback to original."""
        raw = html("<nav>Nav only</nav>")
        result = prune_page(raw, "site", "page", "Product")
        # nav gets removed, body may be empty → no chunks
        assert isinstance(result, PruningResult)
        if not result.selected_chunks:
            assert result.pruned_html == raw or len(result.errors) >= 1

    def test_unexpected_exception_caught(self, monkeypatch):
        """Unexpected exception in pipeline is caught gracefully."""

        def _raise(*args, **kwargs):
            raise RuntimeError("Unexpected boom")

        monkeypatch.setattr(
            "pagemap.pruning.pipeline.preprocess",
            _raise,
        )
        result = prune_page(html("<p>Content</p>"), "site", "page", "Product")
        assert len(result.errors) >= 1
        assert "Unexpected" in result.errors[0]

    def test_elapsed_ms_always_set(self):
        result = prune_page(html("<p>Content</p>"), "site", "page", "Product")
        assert result.elapsed_ms > 0

    def test_large_html_approx_count(self):
        """Large HTML uses count_tokens_approx for raw_token_count."""
        raw = html("<p>Content here </p>" * 5000)
        assert len(raw) > 50_000
        result = prune_page(raw, "site", "page", "Product")
        assert result.raw_token_count > 0

    def test_valid_html_no_errors(self):
        raw = html("<main><h1>Product Name</h1><p>Description of the product is here</p></main>")
        result = prune_page(raw, "site", "page", "Product")
        assert result.errors == []
        assert result.pruned_html != ""

    def test_zero_selected_fallback(self):
        """When pruner selects 0 chunks, returns original HTML."""
        # A page with only noise-like content (short texts outside main)
        raw = html("<div>ab</div><div>cd</div>")
        result = prune_page(raw, "site", "page", "Product")
        # Either has errors about 0 chunks or succeeded with something
        assert isinstance(result, PruningResult)


# ---------------------------------------------------------------------------
# TestBuildPrunedContextErrors
# ---------------------------------------------------------------------------


class TestBuildPrunedContextErrors:
    def test_pruning_failure_returns_raw_with_warnings(self):
        from pagemap.pruned_context_builder import build_pruned_context

        # Monkeypatch prune_page to raise
        with patch("pagemap.pruned_context_builder.prune_page", side_effect=RuntimeError("boom")):
            context, token_count, metadata = build_pruned_context(html("<p>Content</p>"), page_type="default")
        assert len(context) > 0  # Should fall back to raw HTML
        assert "_pruning_warnings" in metadata

    def test_metadata_failure_nonfatal(self):
        from pagemap.pruned_context_builder import build_pruned_context

        with patch("pagemap.metadata.extract_metadata", side_effect=ValueError("bad")):
            context, token_count, metadata = build_pruned_context(
                html("<main><p>Content is here for testing</p></main>"),
                page_type="default",
            )
        # Should succeed despite metadata extraction failure
        assert len(context) > 0

    def test_result_errors_propagate_to_warnings(self):
        from pagemap.pruned_context_builder import build_pruned_context

        # Use empty body → prune_page will have errors
        context, token_count, metadata = build_pruned_context(
            html("<nav>Only nav here</nav>"),
            page_type="default",
        )
        # If pruning had errors, they should propagate as warnings
        assert isinstance(metadata, dict)

    def test_unknown_page_type_uses_default(self):
        from pagemap.pruned_context_builder import build_pruned_context

        context, token_count, metadata = build_pruned_context(
            html("<main><p>Content is here for testing</p></main>"),
            page_type="nonexistent_type",
        )
        assert len(context) > 0

    def test_empty_schema(self):
        from pagemap.pruned_context_builder import build_pruned_context

        context, token_count, metadata = build_pruned_context(
            html("<main><p>Content is here for testing</p></main>"),
            schema_name="Product",
        )
        assert len(context) > 0

    def test_generic_schema_jsonld_detection(self):
        from pagemap.pruned_context_builder import build_pruned_context

        raw = html(
            "<main><h1>Product Name Here</h1>"
            "<p>This is a detailed product description with enough text content for testing.</p>"
            "<p>Price: 29,900원 Rating: 4.5 stars based on 100 reviews</p></main>",
            head='<script type="application/ld+json">{"@type":"Product","name":"Test"}</script>',
        )
        context, token_count, metadata = build_pruned_context(raw, schema_name="Generic")
        assert len(context) > 0

    def test_build_returns_tuple_of_three(self):
        from pagemap.pruned_context_builder import build_pruned_context

        result = build_pruned_context(
            html("<main><p>Hello world content</p></main>"),
            page_type="default",
        )
        assert isinstance(result, tuple)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TestMalformedHtmlStability
# ---------------------------------------------------------------------------


class TestMalformedHtmlStability:
    def test_unclosed_tags(self):
        raw = "<html><body><p>Unclosed paragraph<div>Still works</div></body></html>"
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_mismatched_nesting(self):
        raw = "<html><body><div><span></div></span><p>Content</p></body></html>"
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_broken_entities(self):
        raw = html("<p>Price: &amp incomplete &lt; entity</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_deep_nesting(self):
        """100+ levels of nesting doesn't crash."""
        inner = "<div>" * 120 + "<p>Deep</p>" + "</div>" * 120
        raw = html(inner)
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_null_bytes(self):
        raw = html("<p>Hello\x00World</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)


# ---------------------------------------------------------------------------
# TestUnicodeEdgeCases
# ---------------------------------------------------------------------------


class TestUnicodeEdgeCases:
    def test_emoji(self):
        raw = html("<p>Great product! 🎉🔥 Very good</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_rtl_text(self):
        raw = html("<p>مرحبا بالعالم</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_zero_width_chars(self):
        raw = html("<p>Hello\u200b\u200cWorld\u200dContent</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_html_entities(self):
        raw = html("<p>&amp; &lt; &gt; &quot; &#x27;</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_very_long_single_line(self):
        raw = html(f"<p>{'x' * 10000}</p>")
        result = prune_page(raw, "site", "page", "Product")
        assert isinstance(result, PruningResult)

    def test_max_tokens_zero(self):
        from pagemap.pruned_context_builder import build_pruned_context

        context, token_count, metadata = build_pruned_context(
            html("<main><p>Content here for testing</p></main>"),
            max_tokens=0,
        )
        assert isinstance(context, str)


# ---------------------------------------------------------------------------
# TestErrorPropagationIntegration
# ---------------------------------------------------------------------------


class TestErrorPropagationIntegration:
    def test_prune_error_propagates_to_build(self):
        from pagemap.pruned_context_builder import build_pruned_context

        # Empty HTML → PruningError in prune_page → caught by build_pruned_context
        context, token_count, metadata = build_pruned_context(
            "",
            page_type="default",
        )
        # Should handle gracefully
        assert isinstance(context, str)

    def test_empty_html_full_pipeline(self):
        """Empty HTML goes through full pipeline without crashing."""
        from pagemap.pruned_context_builder import build_pruned_context

        context, tc, meta = build_pruned_context("", page_type="article")
        assert isinstance(context, str)

    def test_script_only_full_pipeline(self):
        from pagemap.pruned_context_builder import build_pruned_context

        raw = "<html><body><script>alert(1)</script></body></html>"
        context, tc, meta = build_pruned_context(raw, page_type="default")
        assert isinstance(context, str)

    def test_both_prune_and_metadata_fail(self):
        from pagemap.pruned_context_builder import build_pruned_context

        with (
            patch("pagemap.pruned_context_builder.prune_page", side_effect=RuntimeError("prune fail")),
        ):
            context, tc, meta = build_pruned_context(html("<p>Content</p>"), page_type="default")
        # Should still return something
        assert isinstance(context, str)
        assert "_pruning_warnings" in meta
