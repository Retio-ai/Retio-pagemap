# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Recovery action suggestions using match/case exhaustive dispatch.

Maps page failure states and action failure types to suggested recovery
actions for AI agents.
"""

from __future__ import annotations

from . import (
    ActionDiagnosis,
    ActionFailureType,
    PageFailureState,
    PageStateDiagnosis,
    SuggestedAction,
)


def suggest_page_recovery(diagnosis: PageStateDiagnosis, **kw) -> tuple[SuggestedAction, ...]:
    """Suggest recovery actions for a page failure state. Never raises."""
    try:
        return _suggest_page_impl(diagnosis)
    except Exception:
        return ()


def _suggest_page_impl(diagnosis: PageStateDiagnosis) -> tuple[SuggestedAction, ...]:
    match diagnosis.state:
        case PageFailureState.BOT_BLOCKED:
            return (
                SuggestedAction(
                    "wait_for",
                    "Wait for challenge to resolve",
                    1,
                    {"time": 10, "stealth_tips": ("slow_down_requests",)},
                ),
                SuggestedAction("navigate", "Try alternate URL", 2),
            )
        case PageFailureState.EMPTY_RESULTS:
            return (
                SuggestedAction("navigate", "Try broader search", 1),
                SuggestedAction("scroll_page", "Check lazy-loaded results", 2, {"direction": "down"}),
            )
        case PageFailureState.OUT_OF_STOCK:
            return (
                SuggestedAction("get_page_map", "Refresh to check stock", 1),
                SuggestedAction("navigate", "Search alternatives", 2),
            )
        case PageFailureState.LOGIN_REQUIRED:
            return (
                SuggestedAction("execute_action", "Fill login form", 1, {"form_fields": True}),
                SuggestedAction("navigate", "Try public page", 2),
            )
        case PageFailureState.ERROR_PAGE:
            return (
                SuggestedAction("navigate", "Try different URL", 1),
                SuggestedAction("get_page_map", "Retry current page", 2),
            )
        case PageFailureState.AGE_VERIFICATION:
            return (SuggestedAction("execute_action", "Accept age verification", 1, {"accept_ref": True}),)
        case PageFailureState.REGION_RESTRICTED:
            return (SuggestedAction("navigate", "Try alternate URL", 1),)
        case _:
            return ()


def suggest_action_recovery(diagnosis: ActionDiagnosis, **kw) -> tuple[SuggestedAction, ...]:
    """Suggest recovery actions for an action failure. Never raises."""
    try:
        return _suggest_action_impl(diagnosis)
    except Exception:
        return ()


def _suggest_action_impl(diagnosis: ActionDiagnosis) -> tuple[SuggestedAction, ...]:
    match diagnosis.failure_type:
        case ActionFailureType.ELEMENT_HIDDEN:
            return (
                SuggestedAction("scroll_page", "Scroll to reveal element", 1),
                SuggestedAction("get_page_map", "Refresh refs", 2),
            )
        case ActionFailureType.ELEMENT_BLOCKED:
            return (
                SuggestedAction("execute_action", "Dismiss overlay", 1),
                SuggestedAction("get_page_map", "Refresh refs", 2),
            )
        case ActionFailureType.STATE_CHANGED:
            return (SuggestedAction("get_page_map", "Page changed; refresh refs", 1),)
        case ActionFailureType.NAVIGATION_UNEXPECTED:
            return (SuggestedAction("get_page_map", "Unexpected navigation; refresh refs", 1),)
        case ActionFailureType.TIMEOUT_EXCEEDED:
            return (
                SuggestedAction("wait_for", "Wait for page response", 1, {"time": 5}),
                SuggestedAction("get_page_map", "Rebuild after wait", 2),
            )
        case _:
            return ()
