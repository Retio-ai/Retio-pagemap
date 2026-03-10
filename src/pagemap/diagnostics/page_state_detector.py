"""Backward-compat shim — import from pagemap.core.diagnostics.page_state_detector instead."""

from pagemap.core.diagnostics.page_state_detector import detect_page_state  # noqa: F401

__all__ = ["detect_page_state"]
