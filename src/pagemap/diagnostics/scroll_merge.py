"""Backward-compat shim — import from pagemap.core.diagnostics.scroll_merge instead."""

from pagemap.core.diagnostics.scroll_merge import ScrollMergeResult, merge_scroll_results  # noqa: F401

__all__ = ["ScrollMergeResult", "merge_scroll_results"]
