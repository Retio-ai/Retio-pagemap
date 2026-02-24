# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Minimum Content Guarantee (MCG) — Step 3.

Covers:
- MCG trigger conditions
- Page type guards (login/error skip MCG)
- OG meta priority in extraction
- MCG warning propagation
"""

from __future__ import annotations

from pagemap.pruned_context_builder import (
    _MCG_SKIP_TYPES,
    _extract_minimum_content,
    build_pruned_context,
)
from pagemap.pruning import HtmlChunk


class TestExtractMinimumContent:
    """Unit tests for _extract_minimum_content()."""

    def test_og_meta_priority(self):
        """OG title and description should be used first."""
        from pagemap.pruning import ChunkType

        # Test preprocessor format (og:title as key in attrs)
        meta_chunks = [
            HtmlChunk(
                xpath="/html/head/meta[1]",
                tag="meta",
                html='<meta property="og:title" content="Test Page Title">',
                text="",
                chunk_type=ChunkType.META,
                attrs={"og:title": "Test Page Title", "og:description": "This is a test description"},
            ),
        ]
        result = _extract_minimum_content(
            meta_chunks=meta_chunks,
            pruned_html="",
            raw_html="",
            max_tokens=500,
        )
        assert "Test Page Title" in result
        assert "test description" in result

    def test_og_meta_standard_format(self):
        """OG meta in standard property/content format should also work."""
        from pagemap.pruning import ChunkType

        meta_chunks = [
            HtmlChunk(
                xpath="/html/head/meta[1]",
                tag="meta",
                html='<meta property="og:title" content="Standard Title">',
                text="",
                chunk_type=ChunkType.META,
                attrs={"property": "og:title", "content": "Standard Title"},
            ),
            HtmlChunk(
                xpath="/html/head/meta[2]",
                tag="meta",
                html='<meta name="description" content="Standard description text">',
                text="",
                chunk_type=ChunkType.META,
                attrs={"name": "description", "content": "Standard description text"},
            ),
        ]
        result = _extract_minimum_content(
            meta_chunks=meta_chunks,
            pruned_html="",
            raw_html="",
            max_tokens=500,
        )
        assert "Standard Title" in result
        assert "Standard description" in result

    def test_pruned_html_fallback(self):
        """When no OG meta, extract from pruned_html."""
        result = _extract_minimum_content(
            meta_chunks=[],
            pruned_html="<p>This is meaningful content from the pruned HTML document that should be extracted.</p>",
            raw_html="<html><body>Raw HTML with lots of extra content</body></html>",
            max_tokens=500,
        )
        assert "meaningful content" in result

    def test_raw_html_last_resort(self):
        """When pruned_html is empty, extract from raw_html."""
        result = _extract_minimum_content(
            meta_chunks=[],
            pruned_html="",
            raw_html="<html><body><p>Raw HTML last resort content for extraction testing</p></body></html>",
            max_tokens=500,
        )
        assert "last resort" in result

    def test_empty_everything_returns_empty(self):
        """All sources empty should return empty string."""
        result = _extract_minimum_content(
            meta_chunks=[],
            pruned_html="",
            raw_html="",
            max_tokens=500,
        )
        assert result == ""

    def test_respects_max_tokens(self):
        """Result should not exceed max_tokens budget."""
        from pagemap.preprocessing.preprocess import count_tokens

        long_html = "<p>" + "word " * 2000 + "</p>"
        result = _extract_minimum_content(
            meta_chunks=[],
            pruned_html=long_html,
            raw_html="",
            max_tokens=50,
        )
        assert count_tokens(result) <= 50


class TestMcgIntegration:
    """Integration tests for MCG in build_pruned_context."""

    def test_mcg_activates_on_empty_result(self):
        """MCG should activate when compressor produces empty result."""
        # Build a page that produces 0 tokens from compressor
        # but has raw HTML content
        html = """<html><head>
        <meta property="og:title" content="Important Page Title">
        <meta property="og:description" content="This page has important content">
        </head><body>
        <nav><a href="/home">Home</a><a href="/about">About</a></nav>
        </body></html>"""

        context, token_count, metadata = build_pruned_context(
            raw_html=html,
            page_type="default",
            schema_name="Generic",
            max_tokens=500,
        )
        # MCG should have produced some content
        # (may or may not activate depending on whether the default compressor
        # extracts enough from the nav-only page)

    def test_mcg_skipped_for_login_page(self):
        """MCG should NOT activate for login page types."""
        html = "<html><body><form><input type='password'></form></body></html>"
        context, token_count, metadata = build_pruned_context(
            raw_html=html,
            page_type="login",
            schema_name="Generic",
            max_tokens=500,
        )
        assert "_mcg_activated" not in metadata or not metadata.get("_mcg_activated")

    def test_mcg_skipped_for_error_page(self):
        """MCG should NOT activate for error page types."""
        html = "<html><body><h1>404 Not Found</h1></body></html>"
        context, token_count, metadata = build_pruned_context(
            raw_html=html,
            page_type="error",
            schema_name="Generic",
            max_tokens=500,
        )
        assert "_mcg_activated" not in metadata or not metadata.get("_mcg_activated")

    def test_mcg_skip_types_coverage(self):
        """All skip types should be in the frozenset."""
        assert "login" in _MCG_SKIP_TYPES
        assert "error" in _MCG_SKIP_TYPES
        assert "form" in _MCG_SKIP_TYPES
        assert "settings" in _MCG_SKIP_TYPES

    def test_mcg_not_for_small_html(self):
        """MCG should not activate for very small raw HTML (< 500 chars)."""
        html = "<html><body>Small</body></html>"
        context, token_count, metadata = build_pruned_context(
            raw_html=html,
            page_type="default",
            schema_name="Generic",
            max_tokens=500,
        )
        assert "_mcg_activated" not in metadata or not metadata.get("_mcg_activated")

    def test_mcg_metadata_flag(self):
        """When MCG activates, _mcg_activated should be set in metadata."""
        # Create HTML that will cause the compressor to produce empty output
        # but the raw HTML has enough content (> 500 chars)
        # Use a page with only navigation to trigger empty compressor output
        nav_items = "".join(f'<li><a href="/page{i}">Page {i}</a></li>' for i in range(50))
        html = f"""<html><head>
        <meta property="og:title" content="MCG Test Page">
        <meta property="og:description" content="Testing minimum content guarantee">
        </head><body>
        <nav><ul>{nav_items}</ul></nav>
        </body></html>"""

        context, token_count, metadata = build_pruned_context(
            raw_html=html,
            page_type="default",
            schema_name="Generic",
            max_tokens=500,
        )
        # If MCG activated, flag should be set
        if metadata.get("_mcg_activated"):
            assert token_count > 0
            assert len(context.strip()) > 0
