"""Backward-compat shim — import from pagemap.core.diagnostics.action_diagnostics instead."""

from pagemap.core.diagnostics.action_diagnostics import diagnose_action_failure  # noqa: F401

__all__ = ["diagnose_action_failure"]
