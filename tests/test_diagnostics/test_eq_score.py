# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for diagnostics.eq_score — page-type-aware EQ scoring."""

from __future__ import annotations

import pytest

from pagemap.diagnostics.eq_score import (
    _DEFAULT_PROFILE,
    _PROFILES,
    EqWeightProfile,
    compute_eq_score,
    should_warn_eq,
)


class TestComputeEqScore:
    def test_unknown_page_type_matches_old_formula(self):
        """page_type='unknown' should produce the same result as the original inline formula."""
        token_ratio = 0.6
        chunk_ratio = 0.5
        mcg = False
        errors = False
        expected = round(0.3 * token_ratio + 0.3 * chunk_ratio + 0.2 * (not mcg) + 0.2 * (not errors), 3)

        result = compute_eq_score(
            token_ratio=token_ratio,
            chunk_ratio=chunk_ratio,
            mcg_activated=mcg,
            has_errors=errors,
            page_type="unknown",
        )
        assert result == expected

    def test_landing_grid_bonus(self):
        """Landing pages with grid whitelist should score higher."""
        base = compute_eq_score(
            token_ratio=0.5,
            chunk_ratio=0.3,
            mcg_activated=True,
            has_errors=False,
            page_type="landing",
            grid_whitelist_count=0,
        )
        boosted = compute_eq_score(
            token_ratio=0.5,
            chunk_ratio=0.3,
            mcg_activated=True,
            has_errors=False,
            page_type="landing",
            grid_whitelist_count=3,
        )
        assert boosted > base

    def test_grid_bonus_capped(self):
        """Grid bonus should not exceed the profile cap."""
        low = compute_eq_score(
            token_ratio=0.5,
            chunk_ratio=0.5,
            mcg_activated=False,
            has_errors=False,
            page_type="landing",
            grid_whitelist_count=3,
        )
        high = compute_eq_score(
            token_ratio=0.5,
            chunk_ratio=0.5,
            mcg_activated=False,
            has_errors=False,
            page_type="landing",
            grid_whitelist_count=100,
        )
        # Both should be capped identically
        assert low == high

    def test_score_clamped_0_1(self):
        result = compute_eq_score(
            token_ratio=1.0,
            chunk_ratio=1.0,
            mcg_activated=False,
            has_errors=False,
            page_type="landing",
            grid_whitelist_count=100,
        )
        assert 0.0 <= result <= 1.0

    def test_score_min_zero(self):
        result = compute_eq_score(
            token_ratio=0.0,
            chunk_ratio=0.0,
            mcg_activated=True,
            has_errors=True,
            page_type="unknown",
        )
        assert result == 0.0

    def test_all_profiles_weights_le_1(self):
        """Base weights (excluding grid_bonus) should sum to <= 1.0 for all profiles."""
        for name, p in _PROFILES.items():
            base_sum = p.token_ratio_w + p.chunk_ratio_w + p.no_mcg_w + p.no_errors_w
            assert base_sum <= 1.0 + 1e-9, f"Profile {name!r} base weights sum to {base_sum}"

    def test_default_profile_backward_compat(self):
        p = _DEFAULT_PROFILE
        assert p.token_ratio_w == 0.3
        assert p.chunk_ratio_w == 0.3
        assert p.no_mcg_w == 0.2
        assert p.no_errors_w == 0.2
        assert p.grid_bonus == 0.0


class TestShouldWarnEq:
    def test_landing_low(self):
        assert should_warn_eq(0.25, "landing") is True

    def test_landing_ok(self):
        assert should_warn_eq(0.35, "landing") is False

    def test_unknown_below_default(self):
        assert should_warn_eq(0.35, "unknown") is True

    def test_unknown_above_default(self):
        assert should_warn_eq(0.45, "unknown") is False

    def test_article_threshold(self):
        assert should_warn_eq(0.44, "article") is True
        assert should_warn_eq(0.46, "article") is False


class TestEqWeightProfile:
    def test_frozen(self):
        p = EqWeightProfile()
        with pytest.raises(AttributeError):
            p.token_ratio_w = 0.5  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(EqWeightProfile(), "__dict__")
