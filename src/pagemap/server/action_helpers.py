# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Action result builders and error classifiers for execute_action / fill_form.

Extracted from server.py — all symbols are re-exported there for backward compatibility.
"""

from __future__ import annotations

import json

from .browser_session import DialogInfo

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

# ── Browser dead detection ────────────────────────────────────────────

_BROWSER_DEAD_PATTERNS = (
    "target closed",
    "target page",
    "browser has been closed",
    "connection closed",
    "browser disconnected",
)


def _is_browser_dead_error(exc: Exception) -> bool:
    """Detect browser crash/disconnect errors."""
    msg = str(exc).lower()
    return any(p in msg for p in _BROWSER_DEAD_PATTERNS)


# ── Action result helpers ─────────────────────────────────────────────


def _build_action_result(
    description: str,
    current_url: str,
    change: str,
    refs_expired: bool,
    change_details: list[str] | None = None,
    dialogs: list[DialogInfo] | None = None,
) -> str:
    """Build a structured JSON success response for execute_action.

    Keys with empty/None/False values are omitted to save tokens.
    """
    data: dict = {
        "description": description,
        "current_url": current_url,
        "change": change,
        "refs_expired": refs_expired,
    }
    if change_details:
        data["change_details"] = change_details
    if dialogs:
        data["dialogs"] = [
            {
                "type": d.dialog_type,
                "message": d.message,
                "action": "dismissed" if d.dismissed else "accepted",
            }
            for d in dialogs
        ]
    return json.dumps(data, ensure_ascii=False)


def _build_action_error(
    error_msg: str,
    refs_expired: bool = False,
    suggested_actions: list[dict] | None = None,
) -> str:
    """Build a structured JSON error response for execute_action."""
    data: dict = {"error": error_msg, "refs_expired": refs_expired}
    if suggested_actions:
        data["suggested_actions"] = suggested_actions
    return json.dumps(data, ensure_ascii=False)


def _collect_dialogs(session) -> list[DialogInfo]:
    """Drain dialog buffer and return list (may be empty)."""
    return session.drain_dialogs()


# ── Dialog warning formatting ─────────────────────────────────────────


def _format_dialog_warnings(dialogs: list[DialogInfo]) -> str:
    """Format pending dialog records into a warning string for tool responses."""
    if not dialogs:
        return ""
    lines = []
    for d in dialogs:
        action = "dismissed" if d.dismissed else "accepted"
        lines.append(f'  - JS {d.dialog_type}() {action}: "{d.message}"')
    return "\n\n⚠ JS dialog(s) appeared during action:\n" + "\n".join(lines)


# ── Retry error classification ────────────────────────────────────────

_RETRYABLE_PATTERNS = (
    "Timeout",  # actionability timeout
    "not visible",  # element temporarily hidden
    "not stable",  # mid-animation
    "intercept",  # overlay temporarily blocking
    "not attached",  # detached during re-render
    "detached",  # element detached from DOM
)

# Click is NOT idempotent — only retry on pre-dispatch failures
_CLICK_SAFE_PATTERNS = (
    "not visible",
    "not stable",
    "intercept",
)


def _is_retryable_error(exc: Exception, action: str) -> bool:
    """Determine if error is transient and safe to retry for this action."""
    msg = str(exc).lower()
    if action in ("click", "hover"):
        return any(p.lower() in msg for p in _CLICK_SAFE_PATTERNS)
    return any(p.lower() in msg for p in _RETRYABLE_PATTERNS)
