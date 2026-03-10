# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Page-type-aware Extraction Quality (EQ) Score.

Replaces the inline EQS formula in pruned_context_builder.py with a
page-type-specific weight profile.  ``landing`` pages (e.g. Hacker News)
receive a grid bonus that prevents false-positive quality warnings
caused by high link density triggering MCG.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EqWeightProfile:
    """Weight profile for EQ Score computation."""

    token_ratio_w: float = 0.3
    chunk_ratio_w: float = 0.3
    no_mcg_w: float = 0.2
    no_errors_w: float = 0.2
    grid_bonus: float = 0.0  # bonus per grid whitelist container (capped)


_PROFILES: dict[str, EqWeightProfile] = {
    "product_detail": EqWeightProfile(),
    "landing": EqWeightProfile(0.2, 0.2, 0.1, 0.2, 0.3),
    "search_results": EqWeightProfile(0.2, 0.3, 0.15, 0.15, 0.2),
    "article": EqWeightProfile(0.35, 0.25, 0.2, 0.2),
}

_DEFAULT_PROFILE = EqWeightProfile()  # backward-compatible weights


def compute_eq_score(
    *,
    token_ratio: float,
    chunk_ratio: float,
    mcg_activated: bool,
    has_errors: bool,
    page_type: str = "unknown",
    grid_whitelist_count: int = 0,
) -> float:
    """Compute page-type-aware Extraction Quality Score.

    Returns a float in [0.0, 1.0].  For ``page_type="unknown"`` the result
    is identical to the original inline formula (backward compatible).
    """
    profile = _PROFILES.get(page_type, _DEFAULT_PROFILE)

    score = (
        profile.token_ratio_w * token_ratio
        + profile.chunk_ratio_w * chunk_ratio
        + profile.no_mcg_w * (not mcg_activated)
        + profile.no_errors_w * (not has_errors)
    )

    # Grid bonus: min(count * 0.1, profile.grid_bonus) — only for profiles that opt in
    if profile.grid_bonus > 0.0 and grid_whitelist_count > 0:
        score += min(grid_whitelist_count * 0.1, profile.grid_bonus)

    return round(max(0.0, min(1.0, score)), 3)


# ── Warning thresholds ──────────────────────────────────────────────

_EQ_WARN_THRESHOLDS: dict[str, float] = {
    "product_detail": 0.4,
    "landing": 0.3,
    "search_results": 0.35,
    "article": 0.45,
}

_DEFAULT_WARN_THRESHOLD = 0.4


def should_warn_eq(score: float, page_type: str) -> bool:
    """Return True if the EQ score is below the warning threshold for this page type."""
    threshold = _EQ_WARN_THRESHOLDS.get(page_type, _DEFAULT_WARN_THRESHOLD)
    return score < threshold
