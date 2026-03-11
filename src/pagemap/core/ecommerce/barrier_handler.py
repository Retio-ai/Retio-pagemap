# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Barrier Handler — Layer 0 orchestrator.

Detects cookie consent, login walls, age verification, and region restrictions.
Runs on ALL pages (not just ecommerce) since cookie banners appear everywhere.

Never raises — returns None if no barrier detected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Interactable

from . import BarrierResult, BarrierType
from .cookie_patterns import (
    _CLOSE_SYMBOLS,
    _CMP_JS_ACCEPT,
    detect_cookie_provider,
)
from .login_detector import detect_age_gate_extended, detect_login_wall, detect_region_block

logger = logging.getLogger(__name__)

# Max button name length to prevent prompt injection
_MAX_BUTTON_NAME_LEN = 50


def _find_dismiss_ref(
    interactables: list[Interactable],
    accept_terms: tuple[str, ...] = (),
    reject_terms: tuple[str, ...] = (),
    dismiss_terms: tuple[str, ...] = (),
    barrier_confirmed: bool = False,
    cookie_policy: str = "reject",
) -> tuple[int | None, str, str | None]:
    """Find dismiss button ref using 5-tier cascade.

    Returns (ref, match_tier, js_call).
    Tiers: js_api(0) → reject(1) → accept(2) → dismiss(3) → symbol(4)
    cookie_policy controls tier ordering for cookie barriers.
    """
    if not interactables:
        return None, "", None

    # Build tier ordering based on cookie_policy
    # js_api tier is handled externally (needs provider), so start from tier 1
    if cookie_policy == "accept":
        term_tiers: list[tuple[tuple[str, ...], str]] = [
            (accept_terms, "accept"),
            (reject_terms, "reject"),
            (dismiss_terms, "dismiss"),
        ]
    elif cookie_policy == "dismiss":
        term_tiers = [
            (dismiss_terms, "dismiss"),
        ]
    else:  # "reject" (default)
        term_tiers = [
            (reject_terms, "reject"),
            (accept_terms, "accept"),
            (dismiss_terms, "dismiss"),
        ]

    # Tiers 1-3: term-based matching (role=button only)
    for terms, tier_name in term_tiers:
        if not terms:
            continue
        for item in interactables:
            if item.affordance not in ("click", "toggle"):
                continue
            if item.role != "button":
                continue
            name_lower = item.name.lower()
            if len(name_lower) > _MAX_BUTTON_NAME_LEN:
                continue
            for term in terms:
                if term in name_lower:
                    return item.ref, tier_name, None

    # Tier 4: Close symbol (×/X) — strict: button only, barrier_confirmed required
    if barrier_confirmed:
        for item in interactables:
            if item.affordance not in ("click", "toggle"):
                continue
            if item.role != "button":
                continue
            name_stripped = item.name.strip()
            if len(name_stripped) > _MAX_BUTTON_NAME_LEN:
                continue
            if name_stripped in _CLOSE_SYMBOLS:
                return item.ref, "symbol", None

    return None, "", None


def _find_accept_ref(
    interactables: list[Interactable],
    accept_terms: tuple[str, ...],
) -> int | None:
    """Find the ref of an accept/dismiss button in interactables.

    Backward-compatible wrapper around ``_find_dismiss_ref()``.
    """
    ref, _, _ = _find_dismiss_ref(
        interactables,
        accept_terms=accept_terms,
        cookie_policy="accept",
    )
    return ref


def detect_barriers(
    raw_html: str,
    html_lower: str,
    url: str,
    interactables: list[Interactable],
    page_type: str,
) -> BarrierResult | None:
    """Detect page barriers (cookie consent, login wall, etc.).

    Priority: cookie_consent > age_verification > region_restricted > login_required

    Cookie consent is checked first because it is the most common barrier
    and often overlays other content.  Login detection runs only if no
    higher-priority barrier is found.

    Note: returns only the highest-priority barrier. Re-call after resolving
    to detect additional barriers (e.g. login wall behind cookie consent).

    Never raises.
    """
    cookie_policy = _get_cookie_policy()

    try:
        # 1. Cookie consent (most common — EU universal)
        cookie = detect_cookie_provider(html_lower)
        if cookie is not None:
            ref, match_tier, _ = _find_dismiss_ref(
                interactables,
                accept_terms=cookie.accept_terms,
                reject_terms=cookie.reject_terms,
                dismiss_terms=cookie.dismiss_terms,
                barrier_confirmed=True,
                cookie_policy=cookie_policy,
            )
            # Determine JS API call based on policy
            js_call = ""
            if cookie.js_dismiss_call:
                if cookie_policy == "accept":
                    js_call = _CMP_JS_ACCEPT.get(cookie.provider, cookie.js_dismiss_call)
                elif cookie_policy != "dismiss":
                    js_call = cookie.js_dismiss_call  # reject API
            if js_call and not match_tier:
                match_tier = "js_api"
            result = BarrierResult(
                barrier_type=BarrierType.COOKIE_CONSENT,
                provider=cookie.provider,
                auto_dismissible=True,
                accept_ref=ref,
                confidence=cookie.confidence,
                signals=cookie.signals,
                accept_terms=cookie.accept_terms,
                reject_terms=cookie.reject_terms,
                dismiss_terms=cookie.dismiss_terms,
                match_tier=match_tier,
                js_dismiss_call=js_call,
            )
            _emit_barrier_telemetry(result, url)
            return result

        # 2. Popup overlay (promotional popups, newsletters, app banners)
        from .popup_detector import detect_popup_overlay

        popup = detect_popup_overlay(html_lower, interactables)
        if popup is not None:
            ref, match_tier, _ = _find_dismiss_ref(
                interactables,
                dismiss_terms=popup.dismiss_terms,
                barrier_confirmed=True,
                cookie_policy="dismiss",
            )
            result = BarrierResult(
                barrier_type=BarrierType.POPUP_OVERLAY,
                provider=popup.provider,
                auto_dismissible=ref is not None,
                accept_ref=ref,
                confidence=popup.confidence,
                signals=popup.signals,
                dismiss_terms=popup.dismiss_terms,
                match_tier=match_tier,
            )
            _emit_barrier_telemetry(result, url)
            return result

        # 3. Age verification (extended — with click-through support)
        age_info = detect_age_gate_extended(html_lower, interactables)
        if age_info is not None and age_info.confidence >= 0.7:
            accept_ref = None
            auto_dismissible = False
            if age_info.gate_type == "click_through":
                accept_ref = _find_accept_ref(interactables, age_info.accept_terms)
                if accept_ref is not None:
                    auto_dismissible = True
            result = BarrierResult(
                barrier_type=BarrierType.AGE_VERIFICATION,
                provider="generic",
                auto_dismissible=auto_dismissible,
                accept_ref=accept_ref,
                confidence=age_info.confidence,
                signals=age_info.signals,
                accept_terms=age_info.accept_terms,
                gate_type=age_info.gate_type,
            )
            _emit_barrier_telemetry(result, url)
            return result

        # 4. Region restriction
        region_confidence, region_signals = detect_region_block(html_lower)
        if region_confidence >= 0.7:
            result = BarrierResult(
                barrier_type=BarrierType.REGION_RESTRICTED,
                provider="generic",
                auto_dismissible=False,
                accept_ref=None,
                confidence=region_confidence,
                signals=region_signals,
            )
            _emit_barrier_telemetry(result, url)
            return result

        # 5. Login wall (lowest priority — needs password + form evidence)
        login = detect_login_wall(raw_html, html_lower, url, interactables, page_type)
        if login is not None:
            result = BarrierResult(
                barrier_type=BarrierType.LOGIN_REQUIRED,
                provider="generic",
                auto_dismissible=False,
                accept_ref=None,
                confidence=login.confidence,
                signals=login.signals,
                form_fields=login.form_fields,
                oauth_providers=login.oauth_providers,
            )
            _emit_barrier_telemetry(result, url)
            return result

        return None

    except Exception as e:
        logger.debug("Barrier detection error: %s", e)
        return None


def _get_cookie_policy() -> str:
    """Read PAGEMAP_COOKIE_POLICY env var (reject|accept|dismiss|none)."""
    import os

    val = os.environ.get("PAGEMAP_COOKIE_POLICY", "reject").lower()
    if val in ("reject", "accept", "dismiss", "none"):
        return val
    return "reject"


def _emit_barrier_telemetry(result: BarrierResult, url: str) -> None:
    """Emit telemetry for barrier detection. Never raises."""
    try:
        from pagemap.telemetry import emit
        from pagemap.telemetry.events import BARRIER_DETECTED

        emit(
            BARRIER_DETECTED,
            {
                "barrier_type": result.barrier_type.value,
                "provider": result.provider,
                "confidence": result.confidence,
                "auto_dismissible": result.auto_dismissible,
                "url": url,
            },
        )
    except Exception:  # nosec B110
        pass
