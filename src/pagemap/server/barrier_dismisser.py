# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Auto-dismiss barrier execution — JS API + button click with DOM stability.

Safety: never raises, try/except wrapped.
Only auto-dismisses COOKIE_CONSENT, AGE_VERIFICATION, POPUP_OVERLAY.
Never auto-dismisses LOGIN_REQUIRED or REGION_RESTRICTED.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pagemap.core import Interactable, PageMap
    from pagemap.core.ecommerce import BarrierResult

    from .browser_session import BrowserSession

logger = logging.getLogger(__name__)

# Barrier types allowed for auto-dismiss
_AUTO_DISMISS_TYPES = frozenset({"cookie_consent", "age_verification", "popup_overlay"})

# Max button name length to prevent prompt injection
_MAX_BUTTON_NAME_LEN = 50


@dataclass(frozen=True, slots=True)
class AutoDismissResult:
    """Result of an auto-dismiss attempt."""

    success: bool
    method: str  # "js_api"|"reject"|"accept"|"dismiss"|"symbol"|"none"
    barrier_type: str
    click_ref: int | None
    elapsed_ms: float
    error: str = ""


async def _wait_for_dom_stability(page, max_wait: float = 3.0) -> None:
    """Wait for DOM to stabilize after dismiss action.

    Uses fingerprint-based polling instead of fixed delay.
    """
    import asyncio

    try:
        from pagemap.dom_change_detector import capture_dom_fingerprint

        await asyncio.sleep(0.5)
        fp1 = await capture_dom_fingerprint(page)
        await asyncio.sleep(0.5)
        fp2 = await capture_dom_fingerprint(page)

        if fp1 == fp2:
            return  # DOM is stable

        # Poll up to max_wait more with 500ms intervals
        remaining = max_wait - 1.0
        while remaining > 0:
            await asyncio.sleep(0.5)
            remaining -= 0.5
            fp_new = await capture_dom_fingerprint(page)
            if fp_new == fp2:
                return
            fp2 = fp_new
    except Exception:
        # Fallback: fixed 1s wait
        import asyncio

        await asyncio.sleep(1.0)


async def _execute_js_dismiss(session: BrowserSession, js_call: str) -> bool:
    """Execute CMP JS API call to dismiss barrier. Returns True on success."""
    try:
        page = session.page
        if page is None:
            return False
        await page.evaluate(js_call)
        await _wait_for_dom_stability(page, max_wait=2.0)
        return True
    except Exception as e:
        logger.debug("JS dismiss failed: %s", e)
        return False


async def _execute_click_dismiss(
    session: BrowserSession,
    ref: int,
    interactables: list[Interactable],
) -> bool:
    """Click a dismiss button by ref. Returns True on success."""
    try:
        page = session.page
        if page is None:
            return False

        # Find the target interactable
        target = None
        for item in interactables:
            if item.ref == ref:
                target = item
                break

        if target is None:
            return False

        # Name length safety check
        if len(target.name) > _MAX_BUTTON_NAME_LEN:
            return False

        # Record URL before click (abort on navigation)
        url_before = page.url

        # Resolve locator using existing strategy chain
        from pagemap.server import _resolve_locator

        locator, _method = await _resolve_locator(page, target)

        # Click with timeout
        await locator.click(timeout=3000)

        # Check for URL change (navigation → abort)
        if page.url != url_before:
            logger.warning("URL changed after barrier dismiss click, aborting")
            return False

        # Wait for DOM stability
        await _wait_for_dom_stability(page, max_wait=3.0)

        return True

    except Exception as e:
        logger.debug("Click dismiss failed for ref=%d: %s", ref, e)
        return False


async def try_auto_dismiss(
    session: BrowserSession,
    page_map: PageMap,
    interactables: list[Interactable],
    cookie_policy: str,
) -> AutoDismissResult:
    """Attempt to auto-dismiss a detected barrier.

    Strategy order:
    1. CMP JS API (if available and cookie barrier)
    2. Button click (reject → accept → dismiss → symbol cascade)

    Safety:
    - Never raises
    - Only dismisses COOKIE_CONSENT, AGE_VERIFICATION, POPUP_OVERLAY
    - Never dismisses LOGIN_REQUIRED, REGION_RESTRICTED
    - URL change → abort
    - Button name > 50 chars → skip
    """
    start = time.monotonic()
    barrier = page_map.barrier

    if barrier is None:
        return AutoDismissResult(
            success=False,
            method="none",
            barrier_type="",
            click_ref=None,
            elapsed_ms=0.0,
            error="no barrier",
        )

    barrier_type_str = barrier.barrier_type.value
    if barrier_type_str not in _AUTO_DISMISS_TYPES:
        return AutoDismissResult(
            success=False,
            method="none",
            barrier_type=barrier_type_str,
            click_ref=None,
            elapsed_ms=0.0,
            error="barrier type not auto-dismissible",
        )

    try:
        # Tier 0: CMP JS API (cookie barriers only, policy != "none")
        if barrier.js_dismiss_call and barrier_type_str == "cookie_consent" and cookie_policy != "none":
            # Select appropriate JS call based on policy
            js_call = barrier.js_dismiss_call
            if cookie_policy == "accept":
                from pagemap.core.ecommerce.cookie_patterns import _CMP_JS_ACCEPT

                js_call = _CMP_JS_ACCEPT.get(barrier.provider, js_call)

            success = await _execute_js_dismiss(session, js_call)
            elapsed = (time.monotonic() - start) * 1000
            if success:
                _emit_dismiss_telemetry(barrier, "js_api", cookie_policy, elapsed)
                return AutoDismissResult(
                    success=True,
                    method="js_api",
                    barrier_type=barrier_type_str,
                    click_ref=None,
                    elapsed_ms=elapsed,
                )
            # JS API failed, fall through to button click

        # Skip cookie banner button click if policy is "none"
        if barrier_type_str == "cookie_consent" and cookie_policy == "none":
            elapsed = (time.monotonic() - start) * 1000
            return AutoDismissResult(
                success=False,
                method="none",
                barrier_type=barrier_type_str,
                click_ref=None,
                elapsed_ms=elapsed,
                error="cookie policy is none",
            )

        # Tier 1-4: Button click
        if barrier.accept_ref is not None:
            success = await _execute_click_dismiss(
                session,
                barrier.accept_ref,
                interactables,
            )
            elapsed = (time.monotonic() - start) * 1000
            method = barrier.match_tier or "accept"
            if success:
                _emit_dismiss_telemetry(barrier, method, cookie_policy, elapsed)
                return AutoDismissResult(
                    success=True,
                    method=method,
                    barrier_type=barrier_type_str,
                    click_ref=barrier.accept_ref,
                    elapsed_ms=elapsed,
                )
            else:
                _emit_dismiss_failure(barrier, "click failed")
                return AutoDismissResult(
                    success=False,
                    method=method,
                    barrier_type=barrier_type_str,
                    click_ref=barrier.accept_ref,
                    elapsed_ms=elapsed,
                    error="click dismiss failed",
                )

        elapsed = (time.monotonic() - start) * 1000
        return AutoDismissResult(
            success=False,
            method="none",
            barrier_type=barrier_type_str,
            click_ref=None,
            elapsed_ms=elapsed,
            error="no dismiss target",
        )

    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        logger.debug("Auto-dismiss error: %s", e)
        _emit_dismiss_failure(barrier, str(e))
        return AutoDismissResult(
            success=False,
            method="none",
            barrier_type=barrier_type_str,
            click_ref=None,
            elapsed_ms=elapsed,
            error=str(e),
        )


def _emit_dismiss_telemetry(
    barrier: BarrierResult,
    method: str,
    cookie_policy: str,
    elapsed_ms: float,
) -> None:
    """Emit telemetry for successful barrier dismiss. Never raises."""
    try:
        from pagemap.telemetry import emit

        emit(
            "barrier_auto_dismissed",
            {
                "barrier_type": barrier.barrier_type.value,
                "method": method,
                "provider": barrier.provider,
                "elapsed_ms": round(elapsed_ms, 1),
                "cookie_policy": cookie_policy,
            },
        )
    except Exception:  # nosec B110
        pass


def _emit_dismiss_failure(barrier: BarrierResult, error: str) -> None:
    """Emit telemetry for failed barrier dismiss. Never raises."""
    try:
        from pagemap.telemetry import emit

        emit(
            "barrier_dismiss_failed",
            {
                "barrier_type": barrier.barrier_type.value,
                "error": error[:200],
                "provider": barrier.provider,
            },
        )
    except Exception:  # nosec B110
        pass
