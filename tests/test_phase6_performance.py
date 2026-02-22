# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Phase 6.2 (AOM xpath prefix optimization) and 6.3 (regex precompilation)."""

from __future__ import annotations

import re

import lxml.html

from pagemap.pruned_context_builder import _LI_SPLIT_RE
from pagemap.pruning.aom_filter import aom_filter
from pagemap.pruning.compressor import _EMPTY_TAG_RE

# ---------------------------------------------------------------------------
# 6.2 — AOM XPath Prefix Matching Optimization
# ---------------------------------------------------------------------------


def _html_doc(body_inner: str) -> lxml.html.HtmlElement:
    """Build an lxml document from body-inner HTML."""
    return lxml.html.document_fromstring(f"<html><body>{body_inner}</body></html>")


class TestAomPrefixOptimization:
    """Verify the O(n*d) ancestor prefix set lookup behaves correctly."""

    def test_nav_with_descendant_removal_count(self):
        """<nav> containing aria-hidden descendant — nav removed, descendant handled."""
        doc = _html_doc(
            "<div><nav><div aria-hidden='true'>hidden</div><a href='/'>link</a></nav></div><main><p>content</p></main>"
        )
        stats = aom_filter(doc)
        # Nav is removed first; aria-hidden div is also processed (counted separately
        # because lxml detaches the subtree, giving it relative xpaths).
        assert stats.removed_nodes >= 1
        assert "semantic-nav" in stats.removal_reasons

    def test_deeply_nested_under_aside(self):
        """<aside><div><div><div>...</div></div></div></aside> — single removal."""
        doc = _html_doc("<aside><div><div><div>deep content</div></div></div></aside>")
        stats = aom_filter(doc)
        assert stats.removed_nodes == 1

    def test_siblings_both_removed(self):
        """Two sibling <nav> elements — both removed independently."""
        doc = _html_doc("<nav>nav1</nav><nav>nav2</nav>")
        stats = aom_filter(doc)
        assert stats.removed_nodes == 2
        assert len(stats.removed_xpaths) == 2

    def test_mixed_depth_removals(self):
        """nav + aside + header at body level — all removed."""
        doc = _html_doc("<nav>nav</nav><aside>aside</aside><header>header</header><main><p>content</p></main>")
        stats = aom_filter(doc)
        assert stats.removed_nodes == 3

    def test_body_direct_child_no_ancestor_check(self):
        """<nav> at body-direct level: range(4, len(parts)) is empty, no false skip."""
        doc = _html_doc("<nav>direct child nav</nav><main><p>main</p></main>")
        stats = aom_filter(doc)
        assert stats.removed_nodes == 1

    def test_removed_xpaths_still_populated(self):
        """Regression: removed_xpaths set is populated and xpaths are valid."""
        doc = _html_doc("<nav>nav</nav><footer>footer</footer><main><p>content</p></main>")
        stats = aom_filter(doc)
        assert len(stats.removed_xpaths) > 0
        for xpath in stats.removed_xpaths:
            assert xpath.startswith("/html/body/")


# ---------------------------------------------------------------------------
# 6.3 — Regex Precompilation Sanity Tests
# ---------------------------------------------------------------------------


class TestRegexPrecompilation:
    """Sanity checks that precompiled patterns produce identical results to inline."""

    def test_empty_tag_removal_precompiled(self):
        """_EMPTY_TAG_RE.sub matches the inline pattern output."""
        html = '<div class="x"></div><p>keep</p><span></span>'
        # Inline equivalent
        inline_result = re.sub(
            r"<(div|span|p|section|article|aside|figure|figcaption|details|summary|"
            r"b|i|em|strong|small|sup|sub|a|abbr|cite|code|mark|u|s)\b[^>]*>\s*</\1>",
            "",
            html,
            flags=re.IGNORECASE,
        )
        precompiled_result = _EMPTY_TAG_RE.sub("", html)
        assert precompiled_result == inline_result

    def test_li_split_precompiled(self):
        """_LI_SPLIT_RE.split produces same result as inline re.split."""
        html = '<ul><li class="a">item1</li><li>item2</li></ul>'
        inline_result = re.split(r"<li[^>]*>", html, flags=re.IGNORECASE)
        precompiled_result = _LI_SPLIT_RE.split(html)
        assert precompiled_result == inline_result
