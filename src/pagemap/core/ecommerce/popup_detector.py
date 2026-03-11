# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Popup overlay detection — AX tree dialog + HTML regex 2-phase.

Detects promotional popups (newsletter, app banners, exit-intent, subscribe)
that are NOT cookie consent, login, or age gate barriers.

Never raises — returns None if no popup detected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Interactable

from .cookie_patterns import _DISMISS_TERMS

# ── Negative filters (skip these even if they look like popups) ───

_NEGATIVE_RE = re.compile(
    r"quick[-_\s]?view|product[-_\s]?modal|size[-_\s]?guide|cart[-_\s]?drawer"
    r"|mini[-_\s]?cart|accessibility[-_\s]?dialog|cookie|login|sign[-_\s]?in"
    r"|age[-_\s]?verif|age[-_\s]?gate|consent",
    re.IGNORECASE,
)

# ── Promotional content keywords ──────────────────────────────────

_PROMO_KEYWORDS_RE = re.compile(
    r"newsletter|subscribe|sign[-_]?up|email[-_]?list|promo(?:tion)?"
    r"|discount|coupon|offer|deal|popup|exit[-_]?intent|app[-_]?banner"
    r"|download[-_]?app|install[-_]?app|notification|alert"
    r"|뉴스레터|구독|이메일|할인|쿠폰|앱\s*다운",
    re.IGNORECASE,
)

# ── HTML regex patterns for popup detection ───────────────────────

_POPUP_HTML_RE = re.compile(
    r'(?:class|id|aria-label)=["\'][^"\']*(?:'
    r"newsletter[-_]?(?:popup|modal|overlay|signup)"
    r"|app[-_]?(?:banner|download|promo)"
    r"|promo[-_]?(?:popup|modal|overlay|banner)"
    r"|exit[-_]?intent"
    r"|subscribe[-_]?(?:popup|modal|overlay)"
    r"|popup[-_]?(?:overlay|modal|banner|container)"
    r"|modal[-_]?(?:overlay|popup|newsletter)"
    r')[^"\']*["\']',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PopupOverlayResult:
    """Result of popup overlay detection."""

    provider: str  # "dialog", "newsletter", "app-banner", "promo", "generic"
    confidence: float
    signals: tuple[str, ...]
    dismiss_terms: tuple[str, ...]


def _is_negative_context(text: str) -> bool:
    """Check if the text matches negative filters (not a promotional popup)."""
    return bool(_NEGATIVE_RE.search(text))


def _detect_ax_dialog(interactables: list[Interactable]) -> PopupOverlayResult | None:
    """Phase 1: AX tree dialog detection (no regex needed).

    Looks for role="dialog" or role="alertdialog" elements that contain
    promotional content keywords.
    """
    for item in interactables:
        if item.role not in ("dialog", "alertdialog"):
            continue

        name_lower = item.name.lower()

        # Skip negative patterns
        if _is_negative_context(name_lower):
            continue

        # Check for promotional content
        has_promo = bool(_PROMO_KEYWORDS_RE.search(name_lower))
        confidence = 0.85 if has_promo else 0.70

        return PopupOverlayResult(
            provider="dialog",
            confidence=confidence,
            signals=(f"ax_dialog:{item.role}:{item.name[:60]}",),
            dismiss_terms=_DISMISS_TERMS,
        )

    return None


def _detect_html_popup(html_lower: str) -> PopupOverlayResult | None:
    """Phase 2: HTML regex fallback for popup detection."""
    # Skip if negative patterns present in the match context
    match = _POPUP_HTML_RE.search(html_lower)
    if match is None:
        return None

    matched_text = match.group()
    if _is_negative_context(matched_text):
        return None

    # Determine provider from matched pattern
    provider = "generic"
    if "newsletter" in matched_text:
        provider = "newsletter"
    elif "app" in matched_text:
        provider = "app-banner"
    elif "promo" in matched_text:
        provider = "promo"
    elif "exit" in matched_text:
        provider = "exit-intent"
    elif "subscribe" in matched_text:
        provider = "newsletter"

    # Confidence based on specificity
    has_promo = bool(_PROMO_KEYWORDS_RE.search(html_lower[:5000]))
    confidence = 0.80 if has_promo else 0.65

    return PopupOverlayResult(
        provider=provider,
        confidence=confidence,
        signals=(f"html_popup:{matched_text[:80]}",),
        dismiss_terms=_DISMISS_TERMS,
    )


def detect_popup_overlay(
    html_lower: str,
    interactables: list[Interactable],
) -> PopupOverlayResult | None:
    """Detect popup overlay using 2-phase approach.

    Phase 1: AX tree dialog detection (role="dialog")
    Phase 2: HTML regex fallback

    Never raises.
    """
    try:
        # Phase 1: AX tree dialog
        result = _detect_ax_dialog(interactables)
        if result is not None:
            return result

        # Phase 2: HTML regex fallback
        return _detect_html_popup(html_lower)

    except Exception:
        return None
