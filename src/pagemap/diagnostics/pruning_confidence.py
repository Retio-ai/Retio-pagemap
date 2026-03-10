"""Backward-compat shim — import from pagemap.core.diagnostics.pruning_confidence instead."""

from pagemap.core.diagnostics.pruning_confidence import assess_pruning_confidence  # noqa: F401

__all__ = ["assess_pruning_confidence"]
