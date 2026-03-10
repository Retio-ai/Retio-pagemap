# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Page failure state detection — 7 states with priority ordering.

Integrates and replaces _check_blocked_page from page_map_builder.py.
Priority: bot_blocked > error_page > login_required > age_verification >
          region_restricted > out_of_stock > empty_results.

Target: <10ms per invocation.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from . import PageFailureState, PageStateDiagnosis
from .i18n_patterns import (
    AGE_VERIFICATION_RE,
    BOT_BLOCKED_RE,
    EMPTY_RESULTS_RE,
    ERROR_PAGE_RE,
    LOGIN_REQUIRED_RE,
    OUT_OF_STOCK_RE,
    REGION_RESTRICTED_RE,
)

if TYPE_CHECKING:
    from .. import Interactable
    from ..ecommerce import BarrierResult


def detect_page_state(
    *,
    raw_html: str,
    html_lower: str,
    page_type: str,
    barrier: BarrierResult | None,
    interactables: list[Interactable],
    metadata: dict[str, Any],
    url: str,
    http_status: int | None = None,
) -> PageStateDiagnosis | None:
    """Detect page failure state from HTML content and barrier results.

    Priority: bot_blocked > error_page > login_required > age_verification >
              region_restricted > out_of_stock > empty_results.

    Returns None if page is healthy. Never raises.
    """
    try:
        return _detect_page_state_impl(
            raw_html=raw_html,
            html_lower=html_lower,
            page_type=page_type,
            barrier=barrier,
            interactables=interactables,
            metadata=metadata,
            url=url,
            http_status=http_status,
        )
    except Exception:
        return None


def _detect_page_state_impl(
    *,
    raw_html: str,
    html_lower: str,
    page_type: str,
    barrier: BarrierResult | None,
    interactables: list[Interactable],
    metadata: dict[str, Any],
    url: str,
    http_status: int | None = None,
) -> PageStateDiagnosis | None:
    """Internal implementation — may raise."""

    # Pre-resolve BarrierType once for all barrier checks
    _BarrierType = None
    if barrier is not None:
        with contextlib.suppress(Exception):
            from ..ecommerce import BarrierType as _BarrierType

    # ── 1. BOT_BLOCKED (highest priority) ──────────────────────────
    # Check page_type == "blocked" (from page_classifier)
    if page_type == "blocked":
        signals = ["page_type=blocked"]
        if http_status is not None:
            signals.append(f"http_status={http_status}")
        return PageStateDiagnosis(
            state=PageFailureState.BOT_BLOCKED,
            confidence=0.95,
            signals=tuple(signals),
            detail="Page classified as blocked by anti-bot protection",
        )

    # Check for bot-blocked text patterns
    bot_match = BOT_BLOCKED_RE.search(html_lower)
    if bot_match:
        signals = [f"text_match={bot_match.group()!r}"]
        # Higher confidence if very few interactables (captcha page)
        confidence = 0.85
        if len(interactables) < 5:
            confidence = 0.90
            signals.append(f"low_interactables={len(interactables)}")
        return PageStateDiagnosis(
            state=PageFailureState.BOT_BLOCKED,
            confidence=confidence,
            signals=tuple(signals),
            detail="Anti-bot text detected in page content",
        )

    # ── 2. ERROR_PAGE ──────────────────────────────────────────────
    if http_status is not None and http_status >= 400:
        signals = [f"http_status={http_status}"]
        confidence = 0.95
        detail = f"HTTP {http_status} error"
        # Check for error text to boost confidence
        err_match = ERROR_PAGE_RE.search(html_lower)
        if err_match:
            signals.append(f"text_match={err_match.group()!r}")
            confidence = 0.98
        return PageStateDiagnosis(
            state=PageFailureState.ERROR_PAGE,
            confidence=confidence,
            signals=tuple(signals),
            detail=detail,
        )

    # Text-only error page detection (no HTTP status)
    err_match = ERROR_PAGE_RE.search(html_lower)
    if err_match and len(interactables) < 10:
        signals = [f"text_match={err_match.group()!r}", f"low_interactables={len(interactables)}"]
        return PageStateDiagnosis(
            state=PageFailureState.ERROR_PAGE,
            confidence=0.75,
            signals=tuple(signals),
            detail="Error page text detected",
        )

    # ── 3. LOGIN_REQUIRED ──────────────────────────────────────────
    # From barrier detection (S2)
    if barrier is not None and _BarrierType is not None:
        if barrier.barrier_type == _BarrierType.LOGIN_REQUIRED:
            return PageStateDiagnosis(
                state=PageFailureState.LOGIN_REQUIRED,
                confidence=barrier.confidence,
                signals=barrier.signals,
                detail="Login barrier detected",
            )

    # Text-based login detection
    login_match = LOGIN_REQUIRED_RE.search(html_lower)
    if login_match and len(interactables) < 15:
        signals = [f"text_match={login_match.group()!r}"]
        return PageStateDiagnosis(
            state=PageFailureState.LOGIN_REQUIRED,
            confidence=0.70,
            signals=tuple(signals),
            detail="Login required text detected",
        )

    # ── 4. AGE_VERIFICATION ────────────────────────────────────────
    if barrier is not None and _BarrierType is not None:
        if barrier.barrier_type == _BarrierType.AGE_VERIFICATION:
            return PageStateDiagnosis(
                state=PageFailureState.AGE_VERIFICATION,
                confidence=barrier.confidence,
                signals=barrier.signals,
                detail="Age verification barrier detected",
            )

    age_match = AGE_VERIFICATION_RE.search(html_lower)
    if age_match:
        signals = [f"text_match={age_match.group()!r}"]
        return PageStateDiagnosis(
            state=PageFailureState.AGE_VERIFICATION,
            confidence=0.80,
            signals=tuple(signals),
            detail="Age verification text detected",
        )

    # ── 5. REGION_RESTRICTED ───────────────────────────────────────
    if barrier is not None and _BarrierType is not None:
        if barrier.barrier_type == _BarrierType.REGION_RESTRICTED:
            return PageStateDiagnosis(
                state=PageFailureState.REGION_RESTRICTED,
                confidence=barrier.confidence,
                signals=barrier.signals,
                detail="Region restriction barrier detected",
            )

    region_match = REGION_RESTRICTED_RE.search(html_lower)
    if region_match:
        signals = [f"text_match={region_match.group()!r}"]
        return PageStateDiagnosis(
            state=PageFailureState.REGION_RESTRICTED,
            confidence=0.80,
            signals=tuple(signals),
            detail="Region restriction text detected",
        )

    # ── 6. OUT_OF_STOCK ────────────────────────────────────────────
    # Only on product detail pages
    if page_type == "product_detail":
        stock_match = OUT_OF_STOCK_RE.search(html_lower)
        if stock_match:
            signals = [f"text_match={stock_match.group()!r}", f"page_type={page_type}"]
            return PageStateDiagnosis(
                state=PageFailureState.OUT_OF_STOCK,
                confidence=0.80,
                signals=tuple(signals),
                detail="Product appears to be out of stock",
            )

    # ── 7. EMPTY_RESULTS ───────────────────────────────────────────
    # Only on search/listing pages
    if page_type in ("search_results", "listing"):
        empty_match = EMPTY_RESULTS_RE.search(html_lower)
        if empty_match:
            signals = [f"text_match={empty_match.group()!r}", f"page_type={page_type}"]
            return PageStateDiagnosis(
                state=PageFailureState.EMPTY_RESULTS,
                confidence=0.85,
                signals=tuple(signals),
                detail="Search returned no results",
            )

    return None
