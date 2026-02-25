# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Hacker News content regression (table-based grid detection).

Covers:
- Table/tbody grid detection for HN-style content listings
- AOM filter whitelist integration with table grids
- Landing compressor output for HN content
"""

from __future__ import annotations

import lxml.html
import pytest

from pagemap.preprocessing.preprocess import count_tokens
from pagemap.pruned_context_builder import _compress_for_landing
from pagemap.pruning.aom_filter import _detect_repeating_grids, aom_filter


def _parse(html: str) -> lxml.html.HtmlElement:
    return lxml.html.document_fromstring(html)


def _build_hn_html(num_stories: int = 5) -> str:
    """Generate HN-like HTML with real table structure."""
    rows = []
    for i in range(1, num_stories + 1):
        rows.append(f"""\
  <tr class="athing" id="{40000000 + i}">
    <td align="right" class="title"><span class="rank">{i}.</span></td>
    <td class="votelinks"><center><a href="vote?id={40000000 + i}"><div class="votearrow"></div></a></center></td>
    <td class="title"><span class="titleline">
      <a href="https://example{i}.com">Story Title Number {i}: An Interesting Development in Technology</a>
      <span class="sitebit comhead"> (<a href="from?site=example{i}.com"><span class="sitestr">example{i}.com</span></a>)</span>
    </span></td>
  </tr>
  <tr>
    <td colspan="2"></td>
    <td class="subtext"><span class="subline">
      <span class="score" id="score_{40000000 + i}">142 points</span> by <a class="hnuser" href="user?id=user{i}">user{i}</a>
      <span class="age" title="2026-02-25T10:00:00"><a href="item?id={40000000 + i}">3 hours ago</a></span> | <a href="hide?id={40000000 + i}">hide</a> | <a href="item?id={40000000 + i}">85 comments</a>
    </span></td>
  </tr>
  <tr class="spacer" style="height:5px"></tr>""")

    return f"""\
<html><body>
<table class="itemlist" cellpadding="0" cellspacing="0">
{"".join(rows)}
</table>
</body></html>"""


class TestTableGridDetection:
    """Unit tests for table/tbody grid detection."""

    def test_hn_table_detected_as_grid(self):
        """HN itemlist table should be whitelisted as a grid container."""
        doc = _parse(_build_hn_html(5))
        whitelist = _detect_repeating_grids(doc)
        assert len(whitelist) >= 1, f"Expected HN table grid detection, got {whitelist}"

    def test_explicit_tbody_detected(self):
        """Table with explicit <tbody> containing >= 3 repeating <tr> should be detected."""
        html = """<html><body>
        <table class="listing">
          <tbody>
            <tr class="row"><td><a href="/1">Item One: A lengthy description of this particular item</a></td></tr>
            <tr class="row"><td><a href="/2">Item Two: A lengthy description of this particular item</a></td></tr>
            <tr class="row"><td><a href="/3">Item Three: A lengthy description of this particular item</a></td></tr>
            <tr class="row"><td><a href="/4">Item Four: A lengthy description of this particular item</a></td></tr>
          </tbody>
        </table>
        </body></html>"""
        doc = _parse(html)
        whitelist = _detect_repeating_grids(doc)
        assert len(whitelist) >= 1, f"Expected tbody grid detection, got {whitelist}"

    def test_nav_table_not_whitelisted(self):
        """Short navigation table (text < 50 chars) should not be whitelisted."""
        html = """<html><body>
        <table class="nav">
          <tr><td><a href="/">Home</a></td></tr>
          <tr><td><a href="/about">About</a></td></tr>
          <tr><td><a href="/help">Help</a></td></tr>
        </table>
        </body></html>"""
        doc = _parse(html)
        whitelist = _detect_repeating_grids(doc)
        assert len(whitelist) == 0, f"Nav table should not be whitelisted, got {whitelist}"

    def test_layout_table_not_whitelisted(self):
        """Mixed-content layout table (low link density) should not be whitelisted."""
        html = """<html><body>
        <table class="layout">
          <tr><td>This is a large block of regular text content without any links at all,
              used for layout purposes in an older website design pattern.</td></tr>
          <tr><td>Another block of plain text content with no links, just providing
              information in a table-based layout that was common in early web.</td></tr>
          <tr><td>A third row of plain content text that is also just regular text,
              no anchor tags or links of any kind present here.</td></tr>
        </table>
        </body></html>"""
        doc = _parse(html)
        whitelist = _detect_repeating_grids(doc)
        assert len(whitelist) == 0, f"Layout table should not be whitelisted, got {whitelist}"


class TestAomFilterHnContent:
    """Integration tests: AOM filter with HN table content."""

    def test_stories_preserved_with_whitelist(self):
        """After grid detection + aom_filter, all 5 story titles should be present."""
        doc = _parse(_build_hn_html(5))
        grid_whitelist = _detect_repeating_grids(doc)
        assert len(grid_whitelist) >= 1, "HN table should be detected as grid"

        aom_filter(doc, schema_name=None, grid_whitelist=grid_whitelist)

        text = doc.text_content()
        for i in range(1, 6):
            assert f"Story Title Number {i}" in text, f"Story {i} missing from AOM-filtered output"

    def test_points_and_comments_preserved(self):
        """Points and comment counts should survive AOM filtering."""
        doc = _parse(_build_hn_html(5))
        grid_whitelist = _detect_repeating_grids(doc)
        aom_filter(doc, schema_name=None, grid_whitelist=grid_whitelist)

        text = doc.text_content()
        assert "142 points" in text, "Points should be preserved"
        assert "85 comments" in text, "Comment count should be preserved"

    def test_stories_removed_without_whitelist(self):
        """Without whitelist, aom_filter removes link-density content (regression baseline).

        This documents the *pre-fix* behaviour: without grid-whitelist protection,
        HN's link-heavy <td> cells get penalised by link-density scoring and at
        least some stories are stripped.  If AOM thresholds change in the future
        and this test breaks, it is safe to adjust or remove — the important
        assertions are in test_stories_preserved_with_whitelist above.
        """
        doc = _parse(_build_hn_html(5))
        # Deliberately pass no whitelist — simulates the regression
        aom_filter(doc, schema_name=None, grid_whitelist=None)

        text = doc.text_content()
        # At least some stories should be stripped by link-density penalty
        present = sum(1 for i in range(1, 6) if f"Story Title Number {i}" in text)
        assert present < 5, f"Expected some stories removed without whitelist, but {present}/5 survived"


class TestLandingCompressorHnOutput:
    """Integration tests: landing compressor with HN HTML."""

    @pytest.fixture()
    def hn_html(self):
        """Pre-filtered HN HTML (grid-whitelisted, AOM-filtered)."""
        doc = _parse(_build_hn_html(5))
        grid_whitelist = _detect_repeating_grids(doc)
        aom_filter(doc, schema_name=None, grid_whitelist=grid_whitelist)
        return lxml.html.tostring(doc, encoding="unicode")

    def test_compressed_output_contains_stories(self, hn_html):
        """Landing compressor output should contain story titles."""
        result = _compress_for_landing(hn_html, 1500)
        found = sum(1 for i in range(1, 6) if f"Story Title Number {i}" in result)
        assert found >= 5, f"Expected all 5 stories in compressed output, got {found}"

    def test_compressed_output_contains_metadata(self, hn_html):
        """Landing compressor output should contain points and comment counts."""
        result = _compress_for_landing(hn_html, 1500)
        assert "points" in result, "Expected points in compressed output"
        assert "comments" in result, "Expected comments in compressed output"

    def test_token_count_above_threshold(self, hn_html):
        """Compressed output should have meaningful content (> 100 tokens)."""
        result = _compress_for_landing(hn_html, 1500)
        tokens = count_tokens(result)
        assert tokens > 100, f"Expected > 100 tokens for HN content, got {tokens}"
