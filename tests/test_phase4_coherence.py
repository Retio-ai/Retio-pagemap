"""Phase 4: Interactable-Pruning Coherence tests.

Tests for:
- 4.3: Tier 3 trust indicator ([CDP-detected])
- 4.4: Pruning failure signal (agent warnings)
- 4.2: Budget filter transparency
- 4.1: Pruning-interactable context coherence
"""

from __future__ import annotations

import json

from pagemap import Interactable, PageMap
from pagemap.pruning.aom_filter import (
    _REASON_TO_REGION,
    AomFilterStats,
    derive_pruned_regions,
)
from pagemap.serializer import (
    _render_interactable_line,
    to_agent_prompt,
    to_agent_prompt_diff,
    to_json,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_interactable(
    ref: int = 1,
    role: str = "button",
    name: str = "Submit",
    affordance: str = "click",
    region: str = "main",
    tier: int = 1,
    value: str = "",
    options: list[str] | None = None,
) -> Interactable:
    return Interactable(
        ref=ref,
        role=role,
        name=name,
        affordance=affordance,
        region=region,
        tier=tier,
        value=value,
        options=options or [],
    )


def _make_page_map(
    interactables: list[Interactable] | None = None,
    warnings: list[str] | None = None,
    pruned_regions: set[str] | None = None,
    pruned_context: str = "Some info",
) -> PageMap:
    return PageMap(
        url="https://example.com",
        title="Test",
        page_type="product_detail",
        interactables=interactables or [],
        pruned_context=pruned_context,
        pruned_tokens=100,
        generation_ms=50.0,
        warnings=warnings or [],
        pruned_regions=pruned_regions or set(),
    )


# ══════════════════════════════════════════════════════════════════════
# 4.3: Tier 3 Trust Indicator
# ══════════════════════════════════════════════════════════════════════


class TestTier3Indicator:
    """Tier 3 (CDP-based) interactables show [CDP-detected] annotation."""

    def test_tier3_shows_cdp_detected(self):
        el = _make_interactable(tier=3)
        pm = _make_page_map(interactables=[el])
        prompt = to_agent_prompt(pm)
        assert "[CDP-detected]" in prompt

    def test_tier1_no_annotation(self):
        el = _make_interactable(tier=1)
        pm = _make_page_map(interactables=[el])
        prompt = to_agent_prompt(pm)
        assert "[CDP-detected]" not in prompt

    def test_tier2_no_annotation(self):
        el = _make_interactable(tier=2)
        pm = _make_page_map(interactables=[el])
        prompt = to_agent_prompt(pm)
        assert "[CDP-detected]" not in prompt

    def test_tier3_with_value_and_options(self):
        """Annotation order: value → options → [CDP-detected]."""
        el = _make_interactable(
            tier=3,
            role="combobox",
            name="Size",
            affordance="select",
            value="M",
            options=["S", "M", "L"],
        )
        line = _render_interactable_line(el)
        # Check ordering: value before options before [CDP-detected]
        val_pos = line.index('value="M"')
        opts_pos = line.index("options=[")
        cdp_pos = line.index("[CDP-detected]")
        assert val_pos < opts_pos < cdp_pos

    def test_tier3_in_diff_format(self):
        old = _make_page_map(interactables=[_make_interactable(ref=1, tier=1)])
        new = _make_page_map(interactables=[_make_interactable(ref=1, tier=3, name="New")])
        diff = to_agent_prompt_diff(old, new, savings_threshold=0.0)
        assert diff is not None
        assert "[CDP-detected]" in diff

    def test_interactable_str_unchanged(self):
        """__str__() must not include [CDP-detected] to avoid budget filter impact."""
        el = _make_interactable(tier=3)
        s = str(el)
        assert "[CDP-detected]" not in s

    def test_json_no_cdp_detected_text(self):
        """JSON format should not have [CDP-detected] text, but tier field stays."""
        el = _make_interactable(tier=3)
        pm = _make_page_map(interactables=[el])
        j = json.loads(to_json(pm))
        # tier=3 in JSON
        assert j["interactables"][0]["tier"] == 3
        # No [CDP-detected] text in JSON
        assert "[CDP-detected]" not in to_json(pm)


# ══════════════════════════════════════════════════════════════════════
# 4.4: Pruning Failure Signal
# ══════════════════════════════════════════════════════════════════════


class TestPruningFailureSignal:
    """Pruning errors produce agent-visible warnings."""

    def test_pruning_warning_in_prompt(self):
        pm = _make_page_map(
            warnings=["Page content extraction encountered issues; displayed content may be incomplete"]
        )
        prompt = to_agent_prompt(pm)
        assert "content extraction encountered issues" in prompt

    def test_no_warning_on_clean_pruning(self):
        pm = _make_page_map(warnings=[])
        prompt = to_agent_prompt(pm)
        assert "## Warnings" not in prompt

    def test_pruning_warning_is_agent_friendly(self):
        """Warning should be a generic, user-readable message — no raw stack traces."""
        msg = "Page content extraction encountered issues; displayed content may be incomplete"
        pm = _make_page_map(warnings=[msg])
        prompt = to_agent_prompt(pm)
        # Agent-friendly: no Traceback, no Exception class names
        assert "Traceback" not in prompt
        assert "Exception" not in prompt
        # But the friendly message is present
        assert "content extraction encountered issues" in prompt


# ══════════════════════════════════════════════════════════════════════
# 4.2: Budget Filter Transparency
# ══════════════════════════════════════════════════════════════════════


class TestBudgetFilterTransparency:
    """Budget filter adds warnings when elements are dropped."""

    def test_warning_when_elements_filtered(self):
        from pagemap.page_map_builder import _budget_filter_interactables

        # Create many large-name interactables that will exceed budget
        els = [_make_interactable(ref=i, name=f"Button {'X' * 100} {i}") for i in range(1, 100)]
        warnings: list[str] = []
        result = _budget_filter_interactables(els, pruned_tokens=4000, warnings=warnings)
        assert len(result) < len(els)
        assert len(warnings) == 1
        assert "interactable elements shown" in warnings[0]

    def test_no_warning_when_all_fit(self):
        from pagemap.page_map_builder import _budget_filter_interactables

        els = [_make_interactable(ref=i, name=f"Btn{i}") for i in range(1, 4)]
        warnings: list[str] = []
        result = _budget_filter_interactables(els, pruned_tokens=100, warnings=warnings)
        assert len(result) == len(els)
        assert warnings == []

    def test_warning_message_format(self):
        from pagemap.page_map_builder import _budget_filter_interactables

        els = [_make_interactable(ref=i, name=f"Button {'X' * 100} {i}") for i in range(1, 100)]
        warnings: list[str] = []
        result = _budget_filter_interactables(els, pruned_tokens=4000, warnings=warnings)
        # Format: "N of M interactable elements shown (token budget)"
        assert f"{len(result)} of {len(els)} interactable elements shown" in warnings[0]
        assert "(token budget)" in warnings[0]

    def test_warnings_none_no_crash(self):
        """warnings=None (default) should not crash even when filtering occurs."""
        from pagemap.page_map_builder import _budget_filter_interactables

        els = [_make_interactable(ref=i, name=f"Button {'X' * 100} {i}") for i in range(1, 100)]
        # No crash when warnings=None (default)
        result = _budget_filter_interactables(els, pruned_tokens=4000)
        assert len(result) < len(els)


# ══════════════════════════════════════════════════════════════════════
# 4.1: Pruning-Interactable Context Coherence
# ══════════════════════════════════════════════════════════════════════


class TestContextCoherence:
    """AOM-pruned regions annotate affected interactables."""

    # ── AomFilterStats.removed_xpaths ──────────────────────────────

    def test_removed_xpaths_populated(self):
        """aom_filter() populates removed_xpaths on stats."""
        import lxml.html

        from pagemap.pruning.aom_filter import aom_filter

        html = "<html><body><nav>Menu</nav><main>Content</main></body></html>"
        doc = lxml.html.fromstring(html)
        stats = aom_filter(doc)
        # nav is removed
        assert stats.removed_nodes > 0
        assert len(stats.removed_xpaths) > 0

    # ── derive_pruned_regions ──────────────────────────────────────

    def test_derive_pruned_regions_nav(self):
        stats = AomFilterStats(removal_reasons={"semantic-nav": 1})
        assert derive_pruned_regions(stats) == {"navigation"}

    def test_derive_pruned_regions_header_footer(self):
        stats = AomFilterStats(removal_reasons={"semantic-header": 1, "semantic-footer": 1})
        regions = derive_pruned_regions(stats)
        assert "header" in regions
        assert "footer" in regions

    def test_derive_pruned_regions_aside(self):
        stats = AomFilterStats(removal_reasons={"semantic-aside": 1})
        assert derive_pruned_regions(stats) == {"complementary"}

    def test_derive_pruned_regions_multiple_reasons(self):
        stats = AomFilterStats(
            removal_reasons={
                "semantic-nav": 2,
                "role=banner": 1,
                "role=contentinfo": 1,
                "role=complementary": 3,
            }
        )
        regions = derive_pruned_regions(stats)
        assert regions == {"navigation", "header", "footer", "complementary"}

    def test_derive_pruned_regions_noise_unmapped(self):
        """Noise/link-density reasons have no region mapping → empty set."""
        stats = AomFilterStats(
            removal_reasons={
                "noise-pattern(2)": 3,
                "link-density-high(0.90)": 1,
            }
        )
        assert derive_pruned_regions(stats) == set()

    def test_derive_pruned_regions_empty(self):
        stats = AomFilterStats()
        assert derive_pruned_regions(stats) == set()

    # ── _REASON_TO_REGION mapping completeness ─────────────────────

    def test_reason_to_region_has_8_entries(self):
        assert len(_REASON_TO_REGION) == 8

    # ── Serializer annotations ─────────────────────────────────────

    def test_context_pruned_annotation_in_prompt(self):
        el = _make_interactable(region="navigation")
        pm = _make_page_map(
            interactables=[el],
            pruned_regions={"navigation"},
        )
        prompt = to_agent_prompt(pm)
        assert "[context pruned]" in prompt

    def test_no_annotation_for_main_region(self):
        """main region interactables never get [context pruned]."""
        el = _make_interactable(region="main")
        pm = _make_page_map(
            interactables=[el],
            pruned_regions={"navigation"},
        )
        prompt = to_agent_prompt(pm)
        assert "[context pruned]" not in prompt

    def test_both_cdp_and_context_pruned(self):
        """Both annotations can appear together."""
        el = _make_interactable(tier=3, region="navigation")
        pm = _make_page_map(
            interactables=[el],
            pruned_regions={"navigation"},
        )
        prompt = to_agent_prompt(pm)
        assert "[CDP-detected]" in prompt
        assert "[context pruned]" in prompt
        # CDP before context pruned
        line = [x for x in prompt.split("\n") if "[CDP-detected]" in x][0]
        assert line.index("[CDP-detected]") < line.index("[context pruned]")

    def test_context_pruned_in_diff_format(self):
        old = _make_page_map(interactables=[_make_interactable(ref=1, region="navigation")])
        new = _make_page_map(
            interactables=[_make_interactable(ref=1, region="navigation", name="New Nav")],
            pruned_regions={"navigation"},
        )
        diff = to_agent_prompt_diff(old, new, savings_threshold=0.0)
        assert diff is not None
        assert "[context pruned]" in diff

    def test_context_pruned_not_in_json(self):
        """JSON should not contain [context pruned] text."""
        el = _make_interactable(region="navigation")
        pm = _make_page_map(
            interactables=[el],
            pruned_regions={"navigation"},
        )
        json_str = to_json(pm)
        assert "[context pruned]" not in json_str

    # ── Warnings ───────────────────────────────────────────────────

    def test_pruned_region_summary_warning(self):
        """PageMap warnings include pruned region summary."""
        els = [
            _make_interactable(ref=1, region="navigation"),
            _make_interactable(ref=2, region="footer"),
            _make_interactable(ref=3, region="main"),
        ]
        pm = _make_page_map(
            interactables=els,
            warnings=["2 interactable(s) in pruned regions (footer, navigation) — surrounding context unavailable"],
            pruned_regions={"navigation", "footer"},
        )
        prompt = to_agent_prompt(pm)
        assert "2 interactable(s) in pruned regions" in prompt
        assert "surrounding context unavailable" in prompt

    def test_no_warning_when_no_affected_interactables(self):
        """pruned_regions exist but no interactables match → no warning."""
        els = [_make_interactable(ref=1, region="main")]
        pm = _make_page_map(
            interactables=els,
            pruned_regions={"navigation"},
            warnings=[],  # builder would not add warning since affected=0
        )
        prompt = to_agent_prompt(pm)
        assert "pruned regions" not in prompt
