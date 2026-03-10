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
from .cookie_patterns import detect_cookie_provider
from .login_detector import detect_age_gate_extended, detect_login_wall, detect_region_block

logger = logging.getLogger(__name__)


def _find_accept_ref(
    interactables: list[Interactable],
    accept_terms: tuple[str, ...],
) -> int | None:
    """Find the ref of an accept/dismiss button in interactables."""
    if not accept_terms or not interactables:
        return None

    # Only check clickable elements
    for item in interactables:
        if item.affordance not in ("click", "toggle"):
            continue
        name_lower = item.name.lower()
        for term in accept_terms:
            if term in name_lower:
                return item.ref

    return None


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
    try:
        # 1. Cookie consent (most common — EU universal)
        cookie = detect_cookie_provider(html_lower)
        if cookie is not None:
            accept_ref = _find_accept_ref(interactables, cookie.accept_terms)
            result = BarrierResult(
                barrier_type=BarrierType.COOKIE_CONSENT,
                provider=cookie.provider,
                auto_dismissible=True,
                accept_ref=accept_ref,
                confidence=cookie.confidence,
                signals=cookie.signals,
                accept_terms=cookie.accept_terms,
            )
            _emit_barrier_telemetry(result, url)
            return result

        # 2. Age verification (extended — with click-through support)
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

        # 3. Region restriction
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

        # 4. Login wall (lowest priority — needs password + form evidence)
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
