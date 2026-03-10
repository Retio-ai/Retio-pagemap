"""Tests for DOM change detection via structural fingerprinting.

Tests the pure comparison function (~22 cases) and the capture function (~6 cases).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap.dom_change_detector import (
    DomFingerprint,
    DomLandmarkVector,
    capture_dom_fingerprint,
    compute_landmark_vector,
    detect_dom_changes,
    fingerprints_structurally_equal,
)


def _fp(
    *,
    interactive_counts: dict[str, int] | None = None,
    total_interactives: int = 10,
    has_dialog: bool = False,
    body_child_count: int = 5,
    title: str = "Test Page",
    content_hash: int = 12345,
) -> DomFingerprint:
    """Helper to build a DomFingerprint with defaults."""
    return DomFingerprint(
        interactive_counts=interactive_counts or {"button": 5, "link": 5},
        total_interactives=total_interactives,
        has_dialog=has_dialog,
        body_child_count=body_child_count,
        title=title,
        content_hash=content_hash,
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

    async def test_evaluate_raises_exception(self):
        """evaluate raises Exception → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("page crashed"))
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    async def test_evaluate_raises_playwright_error(self):
        """evaluate raises PlaywrightError → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=PlaywrightError("target closed"))
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    async def test_evaluate_returns_none(self):
        """evaluate returns None → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=None)
        fp = await capture_dom_fingerprint(page)
        assert fp is None

    async def test_evaluate_returns_non_dict(self):
        """evaluate returns non-dict (string) → None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="not a dict")
        fp = await capture_dom_fingerprint(page)
        assert fp is None

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
        assert fp.content_hash is None

    async def test_content_hash_captured(self):
        """contentHash from JS → content_hash field."""
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "interactiveCounts": {"button": 2},
                "totalInteractives": 2,
                "hasDialog": False,
                "bodyChildCount": 3,
                "title": "Test",
                "contentHash": -987654,
            }
        )
        fp = await capture_dom_fingerprint(page)
        assert fp is not None
        assert fp.content_hash == -987654


# =========================================================================
# content_hash change detection
# =========================================================================


class TestContentHashDetection:
    """Tests for content_changed severity when only text changes."""

    def test_content_changed_severity(self):
        """Same structure, different content_hash → content_changed."""
        before = _fp(content_hash=111)
        after = _fp(content_hash=222)
        v = detect_dom_changes(before, after)
        assert v.severity == "content_changed"
        assert v.changed
        assert any("text" in r for r in v.reasons)

    def test_same_content_hash_no_change(self):
        """Same structure, same content_hash → none."""
        before = _fp(content_hash=111)
        after = _fp(content_hash=111)
        v = detect_dom_changes(before, after)
        assert v.severity == "none"
        assert not v.changed

    def test_content_hash_none_before_ignored(self):
        """content_hash=None (missing) → not treated as content change."""
        before = _fp(content_hash=None)
        after = _fp(content_hash=999)
        v = detect_dom_changes(before, after)
        assert v.severity == "none"  # None = unknown, no change detected

    def test_major_takes_precedence_over_content_changed(self):
        """Major structural change + content change → major (not content_changed)."""
        before = _fp(total_interactives=10, content_hash=111)
        after = _fp(total_interactives=20, content_hash=222)
        v = detect_dom_changes(before, after)
        assert v.severity == "major"

    def test_minor_takes_precedence_over_content_changed(self):
        """Minor structural change + content change → minor (not content_changed)."""
        before = _fp(total_interactives=100, content_hash=111)
        after = _fp(total_interactives=101, content_hash=222)
        v = detect_dom_changes(before, after)
        assert v.severity == "minor"


# =========================================================================
# fingerprints_structurally_equal
# =========================================================================


class TestFingerprintsStructurallyEqual:
    """Tests for structural equality check (ignoring content_hash)."""

    def test_identical_structurally_equal(self):
        a = _fp(content_hash=111)
        b = _fp(content_hash=222)
        assert fingerprints_structurally_equal(a, b) is True

    def test_different_title_not_equal(self):
        a = _fp(title="A")
        b = _fp(title="B")
        assert fingerprints_structurally_equal(a, b) is False

    def test_different_interactive_count_not_equal(self):
        a = _fp(total_interactives=10)
        b = _fp(total_interactives=20)
        assert fingerprints_structurally_equal(a, b) is False

    def test_none_inputs_not_equal(self):
        assert fingerprints_structurally_equal(None, _fp()) is False
        assert fingerprints_structurally_equal(_fp(), None) is False
        assert fingerprints_structurally_equal(None, None) is False

    def test_dialog_difference_not_equal(self):
        a = _fp(has_dialog=False)
        b = _fp(has_dialog=True)
        assert fingerprints_structurally_equal(a, b) is False


# =========================================================================
# DomLandmarkVector — dataclass tests
# =========================================================================


class TestDomLandmarkVector:
    """Tests for DomLandmarkVector serialization."""

    def test_to_list_from_list_roundtrip(self):
        """to_list → from_list → identical vector."""
        vec = DomLandmarkVector(
            content_ratio=0.75,
            interaction_density=0.5,
            structural_symmetry=0.8,
            nesting_ratio=0.6,
            repetition_period=12,
        )
        restored = DomLandmarkVector.from_list(vec.to_list())
        assert restored == vec

    def test_from_list_wrong_length(self):
        """len != 5 → ValueError."""
        with pytest.raises(ValueError, match="Expected 5"):
            DomLandmarkVector.from_list([0.1, 0.2, 0.3])


# =========================================================================
# compute_landmark_vector — pure function tests
# =========================================================================


def _landmark_raw(
    *,
    total_landmarks: int = 7,
    interactive_landmarks: int = 3,
    main_chars: int = 500,
    total_chars: int = 1000,
    depth_sum: int = 60,
    depth_count: int = 10,
    max_depth: int = 8,
    sym_match: int = 3,
    sym_half: int = 4,
    rep_period: int = 5,
) -> dict:
    """Build a raw JS dict with landmarkData."""
    return {
        "landmarkData": {
            "totalLandmarks": total_landmarks,
            "interactiveLandmarks": interactive_landmarks,
            "mainChars": main_chars,
            "totalChars": total_chars,
            "depthSum": depth_sum,
            "depthCount": depth_count,
            "maxDepth": max_depth,
            "symMatch": sym_match,
            "symHalf": sym_half,
            "repPeriod": rep_period,
        }
    }


class TestComputeLandmarkVector:
    """Pure function tests for compute_landmark_vector."""

    def test_full_data(self):
        """Complete landmarkData → correct 5-dimensional vector."""
        vec = compute_landmark_vector(_landmark_raw())
        assert vec is not None
        assert vec.content_ratio == 0.5  # 500/1000
        assert vec.interaction_density == round(3 / 7, 3)  # 3/7
        assert vec.structural_symmetry == 0.75  # 3/4
        assert vec.nesting_ratio == 0.75  # (60/10)/8
        assert vec.repetition_period == 5

    def test_missing_landmark_data(self):
        """No landmarkData key → None."""
        assert compute_landmark_vector({}) is None

    def test_non_dict_landmark_data(self):
        """landmarkData is not a dict → None."""
        assert compute_landmark_vector({"landmarkData": "bad"}) is None
        assert compute_landmark_vector({"landmarkData": 42}) is None

    def test_empty_page(self):
        """All zeros → safe defaults, symmetry=0.5 (indeterminate)."""
        vec = compute_landmark_vector(
            _landmark_raw(
                total_landmarks=0,
                interactive_landmarks=0,
                main_chars=0,
                total_chars=0,
                depth_sum=0,
                depth_count=0,
                max_depth=0,
                sym_match=0,
                sym_half=0,
                rep_period=0,
            )
        )
        assert vec is not None
        assert vec.content_ratio == 0.0
        assert vec.interaction_density == 0.0
        assert vec.structural_symmetry == 0.5
        assert vec.nesting_ratio == 0.0
        assert vec.repetition_period == 0

    def test_division_safety(self):
        """Zero divisors → no ZeroDivisionError."""
        vec = compute_landmark_vector(
            _landmark_raw(
                total_chars=0,
                total_landmarks=0,
                max_depth=0,
                depth_count=0,
                sym_half=0,
            )
        )
        assert vec is not None

    def test_clamping_over(self):
        """mainChars > totalChars → content_ratio clamped to 1.0."""
        vec = compute_landmark_vector(_landmark_raw(main_chars=2000, total_chars=100))
        assert vec is not None
        assert vec.content_ratio == 1.0

    def test_clamping_negative(self):
        """Negative raw values → clamped to 0.0."""
        raw = _landmark_raw()
        raw["landmarkData"]["mainChars"] = -100
        raw["landmarkData"]["interactiveLandmarks"] = -5
        vec = compute_landmark_vector(raw)
        assert vec is not None
        assert vec.content_ratio == 0.0
        assert vec.interaction_density == 0.0

    def test_partial_landmark_data(self):
        """Only some fields present → .get() defaults work."""
        vec = compute_landmark_vector({"landmarkData": {"totalChars": 100}})
        assert vec is not None
        assert vec.content_ratio == 0.0
        assert vec.repetition_period == 0

    def test_rounding_precision(self):
        """Results are rounded to 3 decimal places."""
        vec = compute_landmark_vector(
            _landmark_raw(
                main_chars=1,
                total_chars=3,  # 0.33333...
                interactive_landmarks=1,
                total_landmarks=3,  # 0.33333...
            )
        )
        assert vec is not None
        assert vec.content_ratio == 0.333
        assert vec.interaction_density == 0.333


# =========================================================================
# capture with landmark — integration tests
# =========================================================================


class TestCaptureWithLandmark:
    """Integration tests for landmark_vector in capture_dom_fingerprint."""

    async def test_capture_includes_landmark_vector(self):
        """JS result with landmarkData → fp.landmark_vector is not None."""
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "interactiveCounts": {"button": 2},
                "totalInteractives": 2,
                "hasDialog": False,
                "bodyChildCount": 3,
                "title": "Test",
                "contentHash": 123,
                "spaSignals": {},
                "landmarkData": {
                    "totalLandmarks": 5,
                    "interactiveLandmarks": 2,
                    "mainChars": 300,
                    "totalChars": 600,
                    "depthSum": 30,
                    "depthCount": 5,
                    "maxDepth": 8,
                    "symMatch": 2,
                    "symHalf": 3,
                    "repPeriod": 4,
                },
            }
        )
        fp = await capture_dom_fingerprint(page)
        assert fp is not None
        assert fp.landmark_vector is not None
        assert fp.landmark_vector.content_ratio == 0.5
        assert fp.landmark_vector.repetition_period == 4

    async def test_capture_without_landmark_data_compat(self):
        """Old JS result (no landmarkData) → landmark_vector=None, rest normal."""
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "interactiveCounts": {"button": 1},
                "totalInteractives": 1,
                "hasDialog": False,
                "bodyChildCount": 2,
                "title": "Old",
                "contentHash": 456,
            }
        )
        fp = await capture_dom_fingerprint(page)
        assert fp is not None
        assert fp.landmark_vector is None
        assert fp.title == "Old"
        assert fp.total_interactives == 1

    def test_structural_equal_ignores_landmark(self):
        """Different landmark_vector → fingerprints_structurally_equal still True."""
        vec_a = DomLandmarkVector(0.5, 0.3, 0.8, 0.6, 10)
        vec_b = DomLandmarkVector(0.1, 0.9, 0.2, 0.4, 0)
        a = _fp()
        # Reconstruct with landmark_vector since _fp doesn't set it
        a_with = DomFingerprint(
            interactive_counts=a.interactive_counts,
            total_interactives=a.total_interactives,
            has_dialog=a.has_dialog,
            body_child_count=a.body_child_count,
            title=a.title,
            content_hash=a.content_hash,
            landmark_vector=vec_a,
        )
        b_with = DomFingerprint(
            interactive_counts=a.interactive_counts,
            total_interactives=a.total_interactives,
            has_dialog=a.has_dialog,
            body_child_count=a.body_child_count,
            title=a.title,
            content_hash=a.content_hash,
            landmark_vector=vec_b,
        )
        assert fingerprints_structurally_equal(a_with, b_with) is True
