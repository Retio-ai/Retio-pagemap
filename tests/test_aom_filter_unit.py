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
    _ARTICLE_ANCESTOR_TAGS,
    AomFilterStats,
    _compute_weight,
    _count_noise_matches,
    _detect_repeating_grids,
    _is_body_direct_child,
    _is_inside_article_or_main,
    aom_filter,
    derive_pruned_regions,
)
from tests._pruning_helpers import html, parse_doc, parse_el

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


# ---------------------------------------------------------------------------
# TestContentRescueDetachedParent (Fix 1)
# ---------------------------------------------------------------------------


class TestContentRescueDetachedParent:
    """Content rescue must skip elements whose parent was detached."""

    def test_detached_parent_no_rescue(self):
        """<nav> is removed (weight 0.0) → its descendants are detached.

        A child div with price text + high link density should NOT be rescued
        because the parent <nav> is no longer in the tree.
        """
        # Build DOM: nav contains a high-link-density div with price text
        # The nav will be removed by semantic weight 0.0, detaching all descendants.
        doc = lxml.html.document_fromstring(
            html(
                "<nav>"
                '  <div class="prices">'
                '    <a href="#">₩30,000 some padding text to exceed fifty characters of link content easily</a>'
                "  </div>"
                "</nav>"
                "<p>Short</p>"
            )
        )
        stats = aom_filter(doc, schema_name="Product")
        # nav should be removed
        assert doc.find(".//nav") is None
        # The div with price text should NOT be rescued (parent detached)
        assert stats.content_rescue_count == 0
        # The price div should not be in the final DOM
        assert doc.find('.//div[@class="prices"]') is None

    def test_attached_parent_rescue_succeeds(self):
        """When parent stays in the tree, rescue should still work."""
        # A link-density div directly under body with price text
        # Body is never removed, so rescue should succeed
        link_text = "Buy now click here see more details shop today " * 3  # long link text
        doc = lxml.html.document_fromstring(html(f"<div><a href='#'>{link_text}</a> ₩50,000</div><p>x</p>"))
        stats = aom_filter(doc, schema_name="Product")
        # Should rescue the div (parent=body is attached)
        assert stats.content_rescue_count == 1


# ---------------------------------------------------------------------------
# TestArticleMainPreCompute (Fix 2)
# ---------------------------------------------------------------------------


class TestArticleMainPreCompute:
    """Pre-computed set matches _is_inside_article_or_main() for all elements."""

    def test_set_matches_walk(self):
        """Pre-built set gives same result as ancestor walk for every element."""
        doc = lxml.html.document_fromstring(
            html(
                "<article>" + "".join(f"<p>Paragraph {i}</p>" for i in range(10)) + "</article>"
                "<div><p>Outside paragraph</p></div>"
            )
        )

        # Build the set the same way aom_filter() does
        descendants: set[lxml.html.HtmlElement] = set()
        for container in doc.iter():
            if isinstance(container.tag, str) and container.tag.lower() in _ARTICLE_ANCESTOR_TAGS:
                descendants.update(container.iterdescendants())

        # Check every element in the tree
        for el in doc.iter():
            set_result = el in descendants
            walk_result = _is_inside_article_or_main(el)
            assert set_result == walk_result, f"Mismatch for <{el.tag}>: set={set_result}, walk={walk_result}"

    def test_article_p_exemption_preserved(self):
        """<p> inside <article> with long non-link text survives aom_filter()."""
        long_text = "This is a very long paragraph with important content " * 3
        doc = lxml.html.document_fromstring(html(f"<article><p>{long_text} <a href='#'>ref</a></p></article>"))
        stats = aom_filter(doc)
        # The <p> should survive (article-content-p exemption)
        assert doc.find(".//article//p") is not None
        assert stats.removed_nodes == 0


# ---------------------------------------------------------------------------
# TestStatsAfterRescue (Fix 3)
# ---------------------------------------------------------------------------


class TestStatsAfterRescue:
    """removed_nodes stat must be corrected after content rescue."""

    def test_invariant_after_rescue(self):
        """sum(removal_reasons.values()) == removed_nodes after rescue."""
        # Build cards with link-heavy content containing price patterns
        cards = []
        for i in range(3):
            link_text = f"Product {i} details click here buy now view more info shop " * 2
            cards.append(f"<div><a href='#'>{link_text}</a> ₩{30 + i},000</div>")
        doc = lxml.html.document_fromstring(html("".join(cards) + "<p>x</p>"))
        stats = aom_filter(doc, schema_name="Product")

        # Ensure rescue actually happened (otherwise invariant is vacuously true)
        assert stats.content_rescue_count > 0, "Test precondition: rescue must occur"
        # Invariant: sum of per-reason counts == removed_nodes
        assert sum(stats.removal_reasons.values()) == stats.removed_nodes
        # All counts must be non-negative
        assert all(v >= 0 for v in stats.removal_reasons.values())
        assert stats.removed_nodes >= 0

    def test_invariant_no_rescue(self):
        """When no rescue happens, invariant still holds."""
        doc = lxml.html.document_fromstring(html("<nav>Navigation links</nav><p>Main content here</p>"))
        stats = aom_filter(doc)
        assert stats.content_rescue_count == 0
        assert sum(stats.removal_reasons.values()) == stats.removed_nodes
        assert stats.removed_nodes >= 1


# ---------------------------------------------------------------------------
# TestGridWhitelistAncestor — ancestor protection for grid containers
# ---------------------------------------------------------------------------


class TestGridWhitelistAncestor:
    """Grid whitelist must protect ancestors of whitelisted containers."""

    def test_grid_whitelist_ancestor_weight(self):
        """_compute_weight() returns (0.8, 'grid-whitelist-ancestor') for an
        element whose descendant is in the grid whitelist."""
        # Build a DOM: body > div > div > ul (whitelisted grid)
        doc, tree = parse_doc(
            html(
                "<div>"
                "  <div>"
                "    <ul>"
                + "".join(f'<li><a href="/p/{i}">Product {i} name text padding here</a></li>' for i in range(5))
                + "    </ul>"
                "  </div>"
                "</div>"
            )
        )
        # The outer div has high link density — would normally be penalized
        outer_div = doc.find(".//body/div")
        assert outer_div is not None

        # Simulate grid whitelist containing the <ul>
        ul = doc.find(".//ul")
        ul_xpath = tree.getpath(ul)
        grid_whitelist = {ul_xpath}

        weight, reason = _compute_weight(outer_div, grid_whitelist=grid_whitelist, tree=tree)
        assert weight == 0.8
        assert reason == "grid-whitelist-ancestor"

    def test_grid_whitelist_ancestor_survives_aom(self):
        """Integration: ancestor of whitelisted grid survives aom_filter()."""
        # Build a product listing: main > div (high link density) > ul (grid)
        items = "".join(
            f'<li><a href="/p/{i}">Product {i} with enough text to exceed threshold easily</a></li>' for i in range(5)
        )
        doc = lxml.html.document_fromstring(html(f"<main><div><ul>{items}</ul></div></main>"))
        # Detect grids and run AOM filter
        grid_whitelist = _detect_repeating_grids(doc)
        assert len(grid_whitelist) >= 1, "Precondition: grid should be detected"

        aom_filter(doc, grid_whitelist=grid_whitelist)

        # The outer div (ancestor of grid) should survive
        main_div = doc.find(".//main/div")
        assert main_div is not None, "Ancestor of whitelisted grid was incorrectly removed"
        # The ul grid itself should survive
        assert doc.find(".//ul") is not None


# ---------------------------------------------------------------------------
# TestMainDirectChildProtection — direct children of <main> are protected
# ---------------------------------------------------------------------------


class TestMainDirectChildProtection:
    """Direct children of <main> must not be removed by AOM filter."""

    def test_main_direct_child_not_removed(self):
        """Direct child of <main> with 100% link density survives AOM."""
        # Build a div directly under <main> with all-link content
        link_text = "Product listing link text " * 5  # long link text for high density
        doc = lxml.html.document_fromstring(html(f"<main><div><a href='/products'>{link_text}</a></div></main>"))
        aom_filter(doc)
        # The direct child div should survive
        main_div = doc.find(".//main/div")
        assert main_div is not None, "Direct child of <main> was incorrectly removed"

    def test_main_grandchild_nav_still_removed(self):
        """<nav> nested under <main>/<div> is still removed (not a direct child)."""
        doc = lxml.html.document_fromstring(
            html("<main><div><nav>Navigation links here</nav><p>Content</p></div></main>")
        )
        aom_filter(doc)
        # The nav is a grandchild of main (main > div > nav), not a direct child
        assert doc.find(".//nav") is None, "<nav> grandchild of <main> should still be removed"
        # The direct child div should survive
        assert doc.find(".//main/div") is not None
