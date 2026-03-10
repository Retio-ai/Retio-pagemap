"""Backward-compat shim — import from pagemap.core.diagnostics.auto_threshold instead."""

from pagemap.core.diagnostics.auto_threshold import (  # noqa: F401
    _EMA_ALPHA,
    _MAX_BUDGET_MULTIPLIER,
    _MIN_BUDGET,
    _MIN_SAMPLES_FOR_ACTION,
    _TIGHTEN_CONSECUTIVE_PAGES,
    _WINDOW_SAMPLES,
    _WINDOW_SECONDS,
    AutoThresholdController,
    ThresholdState,
)

__all__ = ["AutoThresholdController", "ThresholdState"]
