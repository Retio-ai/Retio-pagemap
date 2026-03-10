"""Backward-compat shim — import from pagemap.core.diagnostics.eq_score instead."""

from pagemap.core.diagnostics.eq_score import (  # noqa: F401
    _DEFAULT_PROFILE,
    _PROFILES,
    EqWeightProfile,
    compute_eq_score,
    should_warn_eq,
)

__all__ = ["EqWeightProfile", "compute_eq_score", "should_warn_eq"]
