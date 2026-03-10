"""Backward-compat shim — import from pagemap.core.page_classifier instead."""

from pagemap.core.page_classifier import (  # noqa: F401
    ANTI_BOT_KEYWORDS,
    SIGNAL_REGISTRY,
    THRESHOLDS,
    ClassificationResult,
    SignalDef,
    classify_page,
)

__all__ = ["ANTI_BOT_KEYWORDS", "ClassificationResult", "SIGNAL_REGISTRY", "SignalDef", "THRESHOLDS", "classify_page"]
