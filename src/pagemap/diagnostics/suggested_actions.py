"""Backward-compat shim — import from pagemap.core.diagnostics.suggested_actions instead."""

from pagemap.core.diagnostics.suggested_actions import suggest_action_recovery, suggest_page_recovery  # noqa: F401

__all__ = ["suggest_action_recovery", "suggest_page_recovery"]
