"""Backward-compat shim — import from pagemap.server.action_helpers instead."""

from pagemap.server.action_helpers import (  # noqa: F401
    _BROWSER_DEAD_PATTERNS,
    _CLICK_SAFE_PATTERNS,
    _RETRYABLE_PATTERNS,
    _build_action_error,
    _build_action_result,
    _collect_dialogs,
    _format_dialog_warnings,
    _is_browser_dead_error,
    _is_retryable_error,
)

__all__ = [
    "_BROWSER_DEAD_PATTERNS",
    "_CLICK_SAFE_PATTERNS",
    "_RETRYABLE_PATTERNS",
    "_build_action_error",
    "_build_action_result",
    "_collect_dialogs",
    "_format_dialog_warnings",
    "_is_browser_dead_error",
    "_is_retryable_error",
]
