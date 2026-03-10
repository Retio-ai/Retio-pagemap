"""Backward-compat shim — import from pagemap.core.diagnostics instead."""

from pagemap.core.diagnostics import (  # noqa: F401
    ActionDiagnosis,
    ActionFailureType,
    AntibotDetection,
    AntibotProvider,
    AntibotSessionState,
    DiagnosticResult,
    PageFailureState,
    PageStateDiagnosis,
    PruningConfidence,
    ScrollMergeState,
    SpaFramework,
    SpaStatus,
    SuggestedAction,
    run_page_diagnostics,
)

__all__ = [
    "ActionDiagnosis",
    "ActionFailureType",
    "AntibotDetection",
    "AntibotProvider",
    "AntibotSessionState",
    "DiagnosticResult",
    "PageFailureState",
    "PageStateDiagnosis",
    "PruningConfidence",
    "ScrollMergeState",
    "SpaFramework",
    "SpaStatus",
    "SuggestedAction",
    "run_page_diagnostics",
]
