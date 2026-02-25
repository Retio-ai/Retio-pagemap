# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for news portal detection and compression in dashboard-classified pages."""

from __future__ import annotations

import lxml.html

from pagemap.pruned_context_builder import (
    _compress_for_dashboard,
    _compress_for_news_portal,
    _is_news_portal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADLINES = [
    "UK economy grows faster than expected in latest data",
    "Scientists discover high high high new species in deep ocean expedition near underwater volcano",
    "Global leaders meet for emergency climate summit in Paris",
    "Tech giant announces major restructuring affecting thousands of jobs worldwide",
    "Historic peace agreement signed after decades of conflict in the region",
    "New study reveals surprising link between sleep patterns and memory formation",
]


def _build_bbc_html(num_stories: int = 5) -> str:
    stories = []
    for i in range(min(num_stories, len(_HEADLINES))):
        stories.append(
            f'<article><h2><a href="/news/{i + 1}">{_HEADLINES[i]}</a></h2>'
            f"<p>Summary for story {i + 1} with additional context.</p></article>"
        )
    return f"<html><body><main><section>{''.join(stories)}</section></main></body></html>"


def _parse(html_str: str) -> lxml.html.HtmlElement:
    return lxml.html.fromstring(html_str)


# ---------------------------------------------------------------------------
# TestNewsPortalDetection
# ---------------------------------------------------------------------------


class TestNewsPortalDetection:
    def test_bbc_like_articles_detected(self):
        html = _build_bbc_html(5)
        doc = _parse(html)
        assert _is_news_portal(html, doc=doc) is True

    def test_headline_links_without_articles(self):
        """4 h2>a combos, no <article> wrappers -> detected via headline-link path."""
        html = "<html><body>"
        for i in range(4):
            html += f'<h2><a href="/story/{i}">Headline number {i} is here</a></h2>'
        html += "</body></html>"
        doc = _parse(html)
        assert _is_news_portal(html, doc=doc) is True

    def test_h3_headline_links_detected(self):
        """h3>a combos also trigger detection."""
        html = "<html><body>"
        for i in range(3):
            html += f'<h3><a href="/story/{i}">H3 headline {i}</a></h3>'
        html += "</body></html>"
        doc = _parse(html)
        assert _is_news_portal(html, doc=doc) is True

    def test_standard_dashboard_not_detected(self):
        html = (
            "<html><body>"
            "<p>Total Revenue: $50,000</p>"
            "<p>Active Users: 1,234</p>"
            "<table><tr><th>Metric</th><th>Value</th></tr></table>"
            "</body></html>"
        )
        doc = _parse(html)
        assert _is_news_portal(html, doc=doc) is False

    def test_below_threshold(self):
        """2 articles is below the threshold of 3."""
        html = _build_bbc_html(2)
        doc = _parse(html)
        assert _is_news_portal(html, doc=doc) is False

    def test_nested_articles_counted_correctly(self):
        """Nested articles are each counted — 1 outer + 3 inner = 4 >= 3."""
        html = (
            "<html><body>"
            "<article>"
            '  <article><h2><a href="/1">Inner story one</a></h2></article>'
            '  <article><h2><a href="/2">Inner story two</a></h2></article>'
            '  <article><h2><a href="/3">Inner story three</a></h2></article>'
            "</article>"
            "</body></html>"
        )
        doc = _parse(html)
        assert _is_news_portal(html, doc=doc) is True

    def test_fallback_string_counting(self):
        """Without doc, falls back to string counting."""
        html = _build_bbc_html(4)
        assert _is_news_portal(html, doc=None) is True

    def test_fallback_below_threshold(self):
        html = _build_bbc_html(2)
        assert _is_news_portal(html, doc=None) is False


# ---------------------------------------------------------------------------
# TestNewsPortalCompression
# ---------------------------------------------------------------------------


class TestNewsPortalCompression:
    def test_extracts_headlines(self):
        html = _build_bbc_html(5)
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        # Should have numbered headlines
        assert "1." in result
        assert "2." in result
        assert "3." in result
        # Check actual headline text appears
        for headline in _HEADLINES[:3]:
            assert headline in result

    def test_extracts_summaries(self):
        html = _build_bbc_html(3)
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        # Summaries should be indented
        assert "   Summary for story 1" in result

    def test_summary_after_headline_only(self):
        """<p> before the headline should be skipped; only <p> after h2 is summary."""
        html = (
            "<html><body>"
            "<article><p>Byline text before headline here</p>"
            '<h2><a href="/1">The actual headline</a></h2>'
            "<p>The real summary that should appear in output.</p></article>"
            '<article><h2><a href="/2">Second headline story</a></h2>'
            "<p>Second summary text here for testing.</p></article>"
            '<article><h2><a href="/3">Third headline story</a></h2>'
            "<p>Third summary text here for testing.</p></article>"
            "</body></html>"
        )
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        assert "The real summary" in result
        assert "Byline text before" not in result

    def test_h3_headlines_extracted(self):
        """h3 elements inside articles are also extracted as headlines."""
        html = (
            "<html><body>"
            '<article><h3><a href="/1">First h3 headline text</a></h3>'
            "<p>Summary for first h3 story text.</p></article>"
            '<article><h3><a href="/2">Second h3 headline text</a></h3>'
            "<p>Summary for second h3 story text.</p></article>"
            '<article><h3><a href="/3">Third h3 headline text</a></h3>'
            "<p>Summary for third h3 story text.</p></article>"
            "</body></html>"
        )
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        assert "1. First h3 headline text" in result
        assert "2. Second h3 headline text" in result

    def test_deduplicates(self):
        """Duplicate headlines should not be repeated."""
        dup_html = (
            "<html><body>"
            '<article><h2><a href="/1">Same headline text here</a></h2></article>'
            '<article><h2><a href="/2">Same headline text here</a></h2></article>'
            '<article><h2><a href="/3">Different headline entirely</a></h2></article>'
            '<article><h2><a href="/4">Another unique headline now</a></h2></article>'
            "</body></html>"
        )
        doc = _parse(dup_html)
        result = _compress_for_news_portal(dup_html, max_tokens=500, doc=doc)
        assert result.count("Same headline text here") == 1

    def test_respects_budget(self):
        html = _build_bbc_html(6)
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=50, doc=doc)
        # Should produce output but not all 6 headlines
        assert len(result) > 0
        assert "6." not in result

    def test_long_headline_truncated(self):
        """Headlines longer than 200 chars are truncated."""
        long_title = "A" * 250
        html = (
            "<html><body>"
            f'<article><h2><a href="/1">{long_title}</a></h2></article>'
            '<article><h2><a href="/2">Normal headline two</a></h2></article>'
            '<article><h2><a href="/3">Normal headline three</a></h2></article>'
            "</body></html>"
        )
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        # First headline should be truncated to 200 chars (not full 250)
        assert "A" * 200 in result
        assert "A" * 201 not in result

    def test_fallback_no_headlines(self):
        """Articles without headlines fall back to _compress_default."""
        html = (
            "<html><body>"
            "<article><p>Just some text</p></article>"
            "<article><p>More text</p></article>"
            "<article><p>Even more text</p></article>"
            "</body></html>"
        )
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        # Falls back to _compress_default — should still produce output
        assert len(result) > 0
        # No numbered headline format (fallback doesn't produce "1. ...")
        assert not result.startswith("1.")

    def test_standalone_headings_fallback(self):
        """h2/h3 with links but no article wrappers -> Strategy B."""
        html = "<html><body>"
        for i in range(4):
            html += f'<h2><a href="/story/{i}">Standalone headline {i + 1}</a></h2>'
        html += "</body></html>"
        doc = _parse(html)
        result = _compress_for_news_portal(html, max_tokens=500, doc=doc)
        assert "1. Standalone headline 1" in result
        assert "2. Standalone headline 2" in result


# ---------------------------------------------------------------------------
# TestDashboardIntegration
# ---------------------------------------------------------------------------


class TestDashboardIntegration:
    def test_news_portal_through_dashboard(self):
        """BBC-like HTML routed through _compress_for_dashboard produces headlines."""
        html = _build_bbc_html(5)
        doc = _parse(html)
        result = _compress_for_dashboard(html, max_tokens=500, doc=doc)
        # Should have numbered headlines from news portal compressor
        assert "1." in result
        assert "2." in result
        assert "3." in result

    def test_standard_dashboard_unaffected(self):
        """Dashboard with metric keywords still uses original logic."""
        html = "<html><body><p>Total Revenue: $50,000</p><p>Active Users: 1,234</p><p>Views: 5,678</p></body></html>"
        doc = _parse(html)
        result = _compress_for_dashboard(html, max_tokens=500, doc=doc)
        # Original dashboard logic — should contain metric text
        assert "total" in result.lower() or "revenue" in result.lower()
        # Should NOT have numbered list format
        assert "1." not in result
