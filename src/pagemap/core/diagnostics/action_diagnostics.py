# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Action failure diagnostics — consumes DomChangeVerdict.

Classifies Playwright action errors into 5 failure types based on
error message patterns and DOM change verdicts.

Target: <1ms per invocation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import ActionDiagnosis, ActionFailureType

if TYPE_CHECKING:
    from .. import Interactable
    from ..dom_change_detector import DomChangeVerdict

# ── Error message patterns (pre-compiled) ─────────────────────────

_HIDDEN_RE = re.compile(r"not visible|hidden|not in the viewport|is not displayed", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"intercept|overlay|covered|obscured|another element", re.IGNORECASE)
_DETACHED_RE = re.compile(r"not attached|detached|removed from|no longer|disposed", re.IGNORECASE)
_TIMEOUT_RE = re.compile(r"timeout|timed?\s*out", re.IGNORECASE)


def diagnose_action_failure(
    *,
    error: Exception,
    action: str,
    ref: int,
    target: Interactable | None = None,
    pre_url: str = "",
    post_url: str = "",
    timed_out: bool = False,
    dom_verdict: DomChangeVerdict | None = None,
) -> ActionDiagnosis:
    """Diagnose an action failure. Never raises — returns best-effort diagnosis."""
    try:
        return _diagnose_impl(
            error=error,
            action=action,
            ref=ref,
            target=target,
            pre_url=pre_url,
            post_url=post_url,
            timed_out=timed_out,
            dom_verdict=dom_verdict,
        )
    except Exception:
        return ActionDiagnosis(
            failure_type=ActionFailureType.STATE_CHANGED,
            confidence=0.3,
            signals=("diagnosis_failed",),
            original_error=str(error)[:200],
            ref=ref,
            action=action,
        )


def _diagnose_impl(
    *,
    error: Exception,
    action: str,
    ref: int,
    target: Interactable | None,
    pre_url: str,
    post_url: str,
    timed_out: bool,
    dom_verdict: DomChangeVerdict | None,
) -> ActionDiagnosis:
    error_msg = str(error)
    signals: list[str] = []

    # ── 1. Timeout (highest precedence) ────────────────────────────
    if timed_out or isinstance(error, TimeoutError) or _TIMEOUT_RE.search(error_msg):
        signals.append("timeout_detected")
        if timed_out:
            signals.append("timed_out_flag")
        return ActionDiagnosis(
            failure_type=ActionFailureType.TIMEOUT_EXCEEDED,
            confidence=0.95,
            signals=tuple(signals),
            original_error=error_msg[:200],
            ref=ref,
            action=action,
        )

    # ── 2. Navigation unexpected ───────────────────────────────────
    if pre_url and post_url and pre_url != post_url:
        signals.append(f"url_changed={pre_url!r}->{post_url!r}")
        confidence = 0.85
        if dom_verdict is not None and dom_verdict.severity == "major":
            signals.append("dom_major_change")
            confidence = 0.90
        return ActionDiagnosis(
            failure_type=ActionFailureType.NAVIGATION_UNEXPECTED,
            confidence=confidence,
            signals=tuple(signals),
            original_error=error_msg[:200],
            ref=ref,
            action=action,
        )

    # ── 3. Element hidden ──────────────────────────────────────────
    if _HIDDEN_RE.search(error_msg):
        signals.append("error_pattern=hidden")
        return ActionDiagnosis(
            failure_type=ActionFailureType.ELEMENT_HIDDEN,
            confidence=0.90,
            signals=tuple(signals),
            original_error=error_msg[:200],
            ref=ref,
            action=action,
        )

    # ── 4. Element blocked (overlay) ───────────────────────────────
    if _BLOCKED_RE.search(error_msg):
        signals.append("error_pattern=blocked")
        return ActionDiagnosis(
            failure_type=ActionFailureType.ELEMENT_BLOCKED,
            confidence=0.90,
            signals=tuple(signals),
            original_error=error_msg[:200],
            ref=ref,
            action=action,
        )

    # ── 5. State changed (detached DOM) ────────────────────────────
    if _DETACHED_RE.search(error_msg):
        signals.append("error_pattern=detached")
        return ActionDiagnosis(
            failure_type=ActionFailureType.STATE_CHANGED,
            confidence=0.90,
            signals=tuple(signals),
            original_error=error_msg[:200],
            ref=ref,
            action=action,
        )

    # ── 6. DOM verdict fallback ────────────────────────────────────
    if dom_verdict is not None and dom_verdict.changed:
        signals.append(f"dom_change={dom_verdict.severity}")
        signals.extend(dom_verdict.reasons[:3])
        return ActionDiagnosis(
            failure_type=ActionFailureType.STATE_CHANGED,
            confidence=0.70,
            signals=tuple(signals),
            original_error=error_msg[:200],
            ref=ref,
            action=action,
        )

    # ── 7. Unknown → default to STATE_CHANGED ─────────────────────
    signals.append("unknown_error_pattern")
    return ActionDiagnosis(
        failure_type=ActionFailureType.STATE_CHANGED,
        confidence=0.50,
        signals=tuple(signals),
        original_error=error_msg[:200],
        ref=ref,
        action=action,
    )
