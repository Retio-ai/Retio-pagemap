"""Backward-compat shim — import from pagemap.core.dom_change_detector instead."""

from pagemap.core.dom_change_detector import (  # noqa: F401
    DomChangeVerdict,
    DomFingerprint,
    DomLandmarkVector,
    capture_dom_fingerprint,
    compute_landmark_vector,
    detect_dom_changes,
    fingerprints_structurally_equal,
)

__all__ = [
    "DomChangeVerdict",
    "DomFingerprint",
    "DomLandmarkVector",
    "capture_dom_fingerprint",
    "compute_landmark_vector",
    "detect_dom_changes",
    "fingerprints_structurally_equal",
]
