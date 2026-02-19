"""Tests for DOM change detection via structural fingerprinting.

Tests the pure comparison function (~22 cases) and the capture function (~6 cases).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap.dom_change_detector import (
    DomFingerprint,
    capture_dom_fingerprint,
    detect_dom_changes,
)


def _fp(
    *,
    interactive_counts: dict[str, int] | None = None,
    total_interactives: int = 10,
    has_dialog: bool = False,
    body_child_count: int = 5,
    title: str = "Test Page",
) -> DomFingerprint:
    """Helper to build a DomFingerprint with defaults."""
    return DomFingerprint(
        interactive_counts=interactive_counts or {"button": 5, "link": 5},
        total_interactives=total_interactives,
        has_dialog=has_dialog,
        body_child_count=body_child_count,
        title=title,
    )


# =========================================================================
# detect_dom_changes — pure function tests
# =========================================================================


class TestDetectDomChanges:
    """Pure function tests for detect_dom_changes."""

    def test_identical_none(self):
        """Identical fingerprints → severity none."""
        before = _fp()
        after = _fp()
        v = detect_dom_changes(before, after)
        assert v.severity == "none"
        assert not v.changed

    def test_title_changed_major(self):
        """Title changed → major."""
        v = detect_dom_changes(_fp(title="A"), _fp(title="B"))
        assert v.severity == "major"
        assert v.changed
        assert any("title" in r for r in v.reasons)

    def test_dialog_appeared_major(self):
        """Dialog newly appeared → major."""
        v = detect_dom_changes(_fp(has_dialog=False), _fp(has_dialog=True))
        assert v.severity == "major"
        assert any("dialog" in r for r in v.reasons)

    def test_dialog_already_present_none(self):
        """Dialog was already open → no change."""
        v = detect_dom_changes(_fp(has_dialog=True), _fp(has_dialog=True))
        assert v.severity == "none"

    def test_dialog_disappeared_none(self):
        """Dialog disappeared → not flagged (stale signal)."""
        v = detect_dom_changes(_fp(has_dialog=True), _fp(has_dialog=False))
        assert v.severity == "none"

    def test_large_interactive_increase_major(self):
        """Large increase in interactives → major."""
        v = detect_dom_changes(_fp(total_interactives=10), _fp(total_interactives=20))
        assert v.severity == "major"
        assert any("increased" in r for r in v.reasons)

    def test_large_interactive_decrease_major(self):
        """Large decrease in interactives → major."""
        v = detect_dom_changes(_fp(total_interactives=20), _fp(total_interactives=10))
        assert v.severity == "major"
        assert any("decreased" in r for r in v.reasons)

    def test_boundary_abs_4_is_major(self):
        """+4 elements (>3 threshold) → major."""
        v = detect_dom_changes(_fp(total_interactives=100), _fp(total_interactives=104))
        assert v.severity == "major"

    def test_boundary_abs_3_is_not_major(self):
        """+3 elements (== threshold, not >) → check pct."""
        # 3 out of 100 = 3% < 20% → minor
        v = detect_dom_changes(_fp(total_interactives=100), _fp(total_interactives=103))
        assert v.severity == "minor"

    def test_boundary_pct_21_is_major(self):
        """>20% change → major."""
        # 2 out of 9 = 22% > 20%
        v = detect_dom_changes(_fp(total_interactives=9), _fp(total_interactives=11))
        assert v.severity == "major"

    def test_boundary_pct_20_is_not_major(self):
        """Exactly 20% → not major (> not >=)."""
        # 2 out of 10 = 20% == threshold → not major → minor
        v = detect_dom_changes(_fp(total_interactives=10), _fp(total_interactives=12))
        assert v.severity == "minor"

    def test_small_interactive_change_minor(self):
        """Small interactive count change → minor."""
        v = detect_dom_changes(_fp(total_interactives=100), _fp(total_interactives=101))
        assert v.severity == "minor"
        assert v.changed

    def test_any_interactive_change_at_least_minor(self):
        """+1 interactive → at least minor."""
        v = detect_dom_changes(_fp(total_interactives=50), _fp(total_interactives=51))
        assert v.severity in ("minor", "major")
        assert v.changed

    def test_body_child_count_change_only_minor(self):
        """body_child_count change only (no interactive change) → minor."""
        v = detect_dom_changes(_fp(body_child_count=5), _fp(body_child_count=8))
        assert v.severity == "minor"
        assert any("body" in r for r in v.reasons)

    def test_zero_to_n_interactives_major(self):
        """0→N interactives → major (100% change)."""
        v = detect_dom_changes(_fp(total_interactives=0), _fp(total_interactives=5))
        assert v.severity == "major"

    def test_n_to_zero_interactives_major(self):
        """N→0 interactives → major."""
        v = detect_dom_changes(_fp(total_interactives=10), _fp(total_interactives=0))
        assert v.severity == "major"

    def test_zero_to_zero_interactives_none(self):
        """0→0 interactives → none."""
        v = detect_dom_changes(_fp(total_interactives=0), _fp(total_interactives=0))
        assert v.severity == "none"

    def test_combined_minor_interactive_plus_title_major(self):
        """Minor interactive change + title change → escalate to major."""
        v = detect_dom_changes(
            _fp(total_interactives=100, title="A"),
            _fp(total_interactives=101, title="B"),
        )
        assert v.severity == "major"
        assert any("title" in r for r in v.reasons)

    def test_multiple_major_reasons_all_listed(self):
        """Multiple major reasons → all present in reasons list."""
        v = detect_dom_changes(
            _fp(title="A", has_dialog=False, total_interactives=10),
            _fp(title="B", has_dialog=True, total_interactives=20),
        )
        assert v.severity == "major"
        assert len(v.reasons) >= 2

    # --- None inputs ---

    def test_none_before_returns_none_severity(self):
        """(None, valid) → severity none."""
        v = detect_dom_changes(None, _fp())
        assert v.severity == "none"
        assert not v.changed

    def test_none_after_returns_none_severity(self):
        """(valid, None) → severity none."""
        v = detect_dom_changes(_fp(), None)
        assert v.severity == "none"
        assert not v.changed

    def test_both_none_returns_none_severity(self):
        """(None, None) → severity none."""
        v = detect_dom_changes(None, None)
        assert v.severity == "none"
        assert not v.changed

    # --- Empty fingerprint ---

    def test_empty_fingerprints_none(self):
        """All-zero/empty fingerprints → none."""
        empty = DomFingerprint(
            interactive_counts={},
            total_interactives=0,
            has_dialog=False,
            body_child_count=0,
            title="",
        )
        v = detect_dom_changes(empty, empty)
        assert v.severity == "none"
        assert not v.changed


# =========================================================================
# capture_dom_fingerprint tests
# =========================================================================


class TestCaptureDomFingerprint:
    """Tests for capture_dom_fingerprint with mocked page."""

    @pytest.mark.asyncio
    async def test_normal_result(self):
        """Normal JS result → DomFingerprint."""
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "interactiveCounts": {"button": 3, "link": 2},
                "totalInteractives": 5,
                "hasDialog": True,
                "bodyChildCount": 10,
                "title": "My Page",
            }
        )
        fp = await capture_dom_fingerprint(page)
        assert fp is not None
        assert fp.total_interactives == 5
        assert fp.has_dialog is True
        assert fp.title == "My Page"
        assert fp.interactive_counts == {"button": 3, "link": 2}
        assert fp.body_child_count == 10

    @pytest.mark.asyncio
    async def test_evaluate_raises_exception(self):
        """evaluate raises Exception → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("page crashed"))
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    @pytest.mark.asyncio
    async def test_evaluate_raises_playwright_error(self):
        """evaluate raises PlaywrightError → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=PlaywrightError("target closed"))
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    @pytest.mark.asyncio
    async def test_evaluate_returns_none(self):
        """evaluate returns None → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=None)
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    @pytest.mark.asyncio
    async def test_evaluate_returns_non_dict(self):
        """evaluate returns non-dict (string) → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="not a dict")
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    @pytest.mark.asyncio
    async def test_missing_fields_uses_defaults(self):
        """Missing fields in dict → defaults applied."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={})
        fp = await capture_dom_fingerprint(page)
        assert fp is not None
        assert fp.interactive_counts == {}
        assert fp.total_interactives == 0
        assert fp.has_dialog is False
        assert fp.body_child_count == 0
        assert fp.title == ""
