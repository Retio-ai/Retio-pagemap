# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for pruning/aom_filter.py.

Phase 7.3 — covers:
  - _is_body_direct_child helper
  - _count_noise_matches (16 noise patterns)
  - _compute_weight (uncovered weight paths)
  - aom_filter() full DOM mutation integration
  - AomFilterStats dataclass
"""

from __future__ import annotations

import lxml.html
import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from pagemap.pruning.aom_filter import (
    AomFilterStats,
    _compute_weight,
    _count_noise_matches,
    _is_body_direct_child,
    aom_filter,
    derive_pruned_regions,
)
from tests._pruning_helpers import html, parse_el

# ---------------------------------------------------------------------------
# TestIsBodyDirectChild
# ---------------------------------------------------------------------------


class TestIsBodyDirectChild:
    def test_direct_child_true(self):
        doc = lxml.html.document_fromstring(html("<nav>Nav</nav>"))
        nav = doc.find(".//nav")
        assert _is_body_direct_child(nav) is True

    def test_nested_false(self):
        doc = lxml.html.document_fromstring(html("<div><nav>Nav</nav></div>"))
        nav = doc.find(".//nav")
        assert _is_body_direct_child(nav) is False

    def test_orphan_false(self):
        """Element with no parent returns False."""
        from lxml.html import HtmlElement

        el = HtmlElement()
        el.tag = "div"
        el.text = "orphan"
        assert _is_body_direct_child(el) is False

    def test_body_itself_false(self):
        doc = lxml.html.document_fromstring(html("<p>Text</p>"))
        body = doc.find(".//body")
        # body's parent is html, not body
        assert _is_body_direct_child(body) is False


# ---------------------------------------------------------------------------
# TestCountNoiseMatches
# ---------------------------------------------------------------------------

_NOISE_KEYWORDS = [
    "ad-container",
    "ad box",
    "advertisement-box",
    "sponsor-block",
    "banner-top",
    "recommend-section",
    "related-posts",
    "sidebar-widget",
    "popup-overlay",
    "modal-dialog",
    "cookie-banner",
    "tracking-pixel",
    "overlay-bg",
    "promo-bar",
    "widget-area",
    "toast-notification",
]


class TestCountNoiseMatches:
    @pytest.mark.parametrize("cls", _NOISE_KEYWORDS)
    def test_noise_pattern_match(self, cls):
        el = parse_el(f'<div class="{cls}">Content</div>')
        assert _count_noise_matches(el) >= 1

    def test_multiple_hits(self):
        el = parse_el('<div class="ad-container banner overlay">Content</div>')
        assert _count_noise_matches(el) >= 3

    def test_no_noise(self):
        el = parse_el('<div class="main-content article-body">Content</div>')
        assert _count_noise_matches(el) == 0

    def test_empty_attrs(self):
        el = parse_el("<div>No class or id</div>")
        assert _count_noise_matches(el) == 0

    def test_id_only(self):
        el = parse_el('<div id="sidebar">Content</div>')
        assert _count_noise_matches(el) >= 1

    def test_case_insensitive(self):
        el = parse_el('<div class="AD-Container">Content</div>')
        assert _count_noise_matches(el) >= 1


# ---------------------------------------------------------------------------
# TestComputeWeightGaps — uncovered weight paths
# ---------------------------------------------------------------------------


class TestComputeWeightGaps:
    @pytest.mark.parametrize(
        "role,expected_weight",
        [
            ("navigation", 0.0),
            ("banner", 0.0),
            ("contentinfo", 0.0),
            ("main", 1.0),
            ("article", 1.0),
            ("region", 0.8),
        ],
    )
    def test_explicit_roles(self, role, expected_weight):
        el = parse_el(f'<div role="{role}">Content</div>')
        weight, reason = _compute_weight(el)
        assert weight == expected_weight

    def test_header_body_direct_removed(self):
        doc = lxml.html.document_fromstring(html("<header>Site Header</header>"))
        header = doc.find(".//header")
        weight, reason = _compute_weight(header)
        assert weight == 0.0
        assert "semantic-header" in reason

    def test_header_nested_kept(self):
        doc = lxml.html.document_fromstring(html("<article><header>Article Header</header></article>"))
        header = doc.find(".//header")
        weight, reason = _compute_weight(header)
        assert weight == 0.8
        assert "nested" in reason

    def test_footer_body_direct_removed(self):
        doc = lxml.html.document_fromstring(html("<footer>Site Footer</footer>"))
        footer = doc.find(".//footer")
        weight, reason = _compute_weight(footer)
        assert weight == 0.0
        assert "semantic-footer" in reason

    def test_footer_nested_kept(self):
        doc = lxml.html.document_fromstring(html("<article><footer>Article Footer</footer></article>"))
        footer = doc.find(".//footer")
        weight, reason = _compute_weight(footer)
        assert weight == 0.8

    def test_section_labeled(self):
        el = parse_el('<section aria-label="Features">Content</section>')
        weight, reason = _compute_weight(el)
        assert weight == 0.8
        assert "labeled" in reason

    def test_section_unlabeled(self):
        el = parse_el("<section>Content</section>")
        weight, reason = _compute_weight(el)
        assert weight == 0.6
        assert "unlabeled" in reason

    def test_aria_hidden(self):
        el = parse_el('<div aria-hidden="true">Hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert "aria-hidden" in reason

    def test_display_none(self):
        el = parse_el('<div style="display: none;">Hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert "display-none" in reason

    def test_visibility_hidden(self):
        el = parse_el('<div style="visibility: hidden;">Hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert "visibility-hidden" in reason

    def test_government_footer_exception(self):
        doc = lxml.html.document_fromstring(html("<footer>Contact Info</footer>"))
        footer = doc.find(".//footer")
        weight, reason = _compute_weight(footer, schema_name="GovernmentPage")
        assert weight == 0.6
        assert "gov-exception" in reason

    def test_government_footer_exception_via_role(self):
        doc = lxml.html.document_fromstring(html('<div role="contentinfo">Contact</div>'))
        el = doc.find('.//div[@role="contentinfo"]')
        weight, reason = _compute_weight(el, schema_name="GovernmentPage")
        assert weight == 0.6

    def test_role_priority_over_semantic_tag(self):
        """Explicit role overrides semantic tag weight."""
        el = parse_el('<nav role="main">Content</nav>')
        weight, reason = _compute_weight(el)
        assert weight == 1.0
        assert "role=main" in reason

    def test_aside_with_interactive_descendants(self):
        el = parse_el("<aside><input type='text'/><select><option>A</option></select></aside>")
        weight, reason = _compute_weight(el)
        assert weight == 0.7
        assert "filter-sidebar" in reason

    def test_aside_without_interactive(self):
        el = parse_el("<aside><p>Just text</p></aside>")
        weight, reason = _compute_weight(el)
        assert weight == 0.3

    def test_content_pattern_boost(self):
        el = parse_el('<div class="article-content">Long text content</div>')
        weight, reason = _compute_weight(el)
        assert weight == 1.0
        assert "content-pattern" in reason

    def test_noise_override_by_content(self):
        el = parse_el('<div class="ad-banner sidebar article content">Text</div>')
        weight, reason = _compute_weight(el)
        # Has both noise (>=2) and content patterns → override
        assert "content-override" in reason or "content-pattern" in reason


# ---------------------------------------------------------------------------
# TestAomFilterIntegration — full DOM mutation
# ---------------------------------------------------------------------------


class TestAomFilterIntegration:
    def test_nav_removed(self):
        doc = lxml.html.document_fromstring(html("<nav>Nav links</nav><p>Content</p>"))
        stats = aom_filter(doc)
        assert doc.find(".//nav") is None
        assert stats.removed_nodes >= 1

    def test_header_removed(self):
        doc = lxml.html.document_fromstring(html("<header>Header</header><p>Content</p>"))
        aom_filter(doc)
        assert doc.find(".//header") is None

    def test_footer_removed(self):
        doc = lxml.html.document_fromstring(html("<footer>Footer</footer><p>Content</p>"))
        aom_filter(doc)
        assert doc.find(".//footer") is None

    def test_aside_removed(self):
        doc = lxml.html.document_fromstring(html("<aside>Sidebar</aside><p>Content</p>"))
        aom_filter(doc)
        assert doc.find(".//aside") is None

    def test_main_never_removed(self):
        doc = lxml.html.document_fromstring(html("<main><p>Main content</p></main>"))
        aom_filter(doc)
        assert doc.find(".//main") is not None

    def test_body_never_removed(self):
        doc = lxml.html.document_fromstring(html("<p>Content</p>"))
        aom_filter(doc)
        assert doc.find(".//body") is not None

    def test_html_never_removed(self):
        doc = lxml.html.document_fromstring(html("<p>Content</p>"))
        aom_filter(doc)
        # doc itself is the html element
        assert doc.tag == "html"

    def test_aria_hidden_removed(self):
        doc = lxml.html.document_fromstring(html('<div aria-hidden="true">Hidden</div><p>Visible</p>'))
        aom_filter(doc)
        hidden = doc.find('.//div[@aria-hidden="true"]')
        assert hidden is None

    def test_stats_populated(self):
        doc = lxml.html.document_fromstring(html("<nav>Nav</nav><header>Header</header><p>Content</p>"))
        stats = aom_filter(doc)
        assert stats.total_nodes > 0
        assert stats.removed_nodes >= 2
        assert len(stats.removal_reasons) >= 1

    def test_descendant_not_double_counted(self):
        """When a parent is removed, its children should not be separately counted."""
        doc = lxml.html.document_fromstring(html("<nav><ul><li>Link 1</li><li>Link 2</li></ul></nav><p>Content</p>"))
        stats = aom_filter(doc)
        # nav is removed, its children should not be separately removed
        # removed_nodes should be 1 (just the nav)
        assert stats.removed_nodes == 1

    def test_nested_header_preserved(self):
        doc = lxml.html.document_fromstring(html("<article><header>Article Header</header><p>Content</p></article>"))
        aom_filter(doc)
        # Nested header has weight 0.8 (>= 0.5 threshold), should be kept
        assert doc.find(".//header") is not None

    def test_custom_threshold(self):
        doc = lxml.html.document_fromstring(html("<section>Section</section><p>Content</p>"))
        # Unlabeled section has weight 0.6
        # With threshold=0.7, it should be removed
        aom_filter(doc, threshold=0.7)
        assert doc.find(".//section") is None

    def test_empty_doc(self):
        doc = lxml.html.document_fromstring(html(""))
        stats = aom_filter(doc)
        assert stats.removed_nodes == 0

    def test_government_footer_preserved(self):
        doc = lxml.html.document_fromstring(html("<footer>Contact: 123-456</footer><p>Content</p>"))
        aom_filter(doc, schema_name="GovernmentPage")
        # Footer with GovernmentPage has weight 0.6 (>= 0.5 threshold)
        assert doc.find(".//footer") is not None

    @settings(max_examples=30, deadline=5000)
    @given(st.text(alphabet=st.characters(whitelist_categories=("L", "N", "P")), min_size=0, max_size=50))
    def test_protected_tags_never_removed(self, attr_val):
        """body/html/main are never removed regardless of attributes."""
        doc = lxml.html.document_fromstring(
            f'<html class="{attr_val}"><body class="{attr_val}">'
            f'<main class="{attr_val}"><p>Content</p></main></body></html>'
        )
        aom_filter(doc)
        assert doc.tag == "html"
        assert doc.find(".//body") is not None
        assert doc.find(".//main") is not None

    def test_derive_pruned_regions(self):
        doc = lxml.html.document_fromstring(
            html("<nav>Nav</nav><footer>Foot</footer><aside>Side</aside><p>Content</p>")
        )
        stats = aom_filter(doc)
        regions = derive_pruned_regions(stats)
        assert "navigation" in regions
        assert "footer" in regions
        assert "complementary" in regions


# ---------------------------------------------------------------------------
# TestAomFilterStats
# ---------------------------------------------------------------------------


class TestAomFilterStats:
    def test_defaults(self):
        stats = AomFilterStats()
        assert stats.total_nodes == 0
        assert stats.removed_nodes == 0
        assert stats.removal_reasons == {}

    def test_record_increments(self):
        stats = AomFilterStats()
        stats.record("semantic-nav")
        assert stats.removed_nodes == 1
        assert stats.removal_reasons["semantic-nav"] == 1

    def test_multiple_same_reason(self):
        stats = AomFilterStats()
        stats.record("semantic-nav")
        stats.record("semantic-nav")
        assert stats.removed_nodes == 2
        assert stats.removal_reasons["semantic-nav"] == 2
