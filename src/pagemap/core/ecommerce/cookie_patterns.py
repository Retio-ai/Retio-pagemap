# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Cookie consent banner detection — 7 named CMP providers + generic fallback.

All regex patterns are pre-compiled at module level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CookieConsentPattern:
    """A detected cookie consent provider."""

    provider: str  # e.g. "cookiebot", "onetrust", "generic"
    confidence: float  # 0.0–1.0
    signals: tuple[str, ...]  # matched evidence
    accept_terms: tuple[str, ...]  # i18n accept button text patterns


# ── Module-level pre-compiled regex patterns ───────────────────────

# Named CMP detection patterns
_COOKIEBOT_RE = re.compile(r"cybotcookiebotdialog|cookiebot", re.IGNORECASE)
_ONETRUST_RE = re.compile(r"onetrust-banner-sdk|optanon|onetrust", re.IGNORECASE)
_TRUSTARC_RE = re.compile(r"truste-consent|trustarc|truste_overlay", re.IGNORECASE)
_DIDOMI_RE = re.compile(r"didomi-popup|didomi-notice|didomi", re.IGNORECASE)
_QUANTCAST_RE = re.compile(r"qc-cmp-ui|quantcast-choice|qc-cmp2", re.IGNORECASE)
_USERCENTRICS_RE = re.compile(r"usercentrics|uc-banner|uc-consent", re.IGNORECASE)

# Generic cookie banner patterns — anchored to class/id/aria-label attributes
# to avoid false positives from JS/CSS references.
_GENERIC_COOKIE_RE = re.compile(
    r'(?:class|id|aria-label)=["\'][^"\']*(?:'
    r"cookie[-_]?(?:banner|consent|notice|popup|modal|wall|bar|overlay)"
    r"|gdpr[-_]?(?:banner|consent|notice|popup|modal)"
    r"|consent[-_]?(?:banner|modal|popup|notice)"
    r')[^"\']*["\']',
    re.IGNORECASE,
)

# ── i18n accept button text patterns (10 locales) ─────────────────

_ACCEPT_TERMS_ALL: tuple[str, ...] = (
    # en
    "accept all",
    "accept cookies",
    "accept",
    "allow all",
    "allow cookies",
    "i agree",
    "agree",
    "got it",
    "ok",
    "continue",
    # ko
    "모두 수락",
    "모두 동의",
    "동의",
    "쿠키 수락",
    "수락",
    "확인",
    # ja
    "すべて受け入れる",
    "すべて許可",
    "同意する",
    "同意",
    "承認",
    # fr
    "tout accepter",
    "accepter tout",
    "accepter les cookies",
    "accepter",
    "j'accepte",
    # de
    "alle akzeptieren",
    "alle cookies akzeptieren",
    "akzeptieren",
    "zustimmen",
    "einverstanden",
    # zh
    "全部接受",
    "接受所有",
    "接受",
    "同意",
    # es
    "aceptar todo",
    "aceptar todas",
    "aceptar cookies",
    "aceptar",
    # it
    "accetta tutto",
    "accetta tutti",
    "accetta i cookie",
    "accetta",
    # pt
    "aceitar tudo",
    "aceitar todos",
    "aceitar cookies",
    "aceitar",
    # nl
    "alles accepteren",
    "alle cookies accepteren",
    "accepteren",
)


def _detect_named_cmp(html_lower: str) -> CookieConsentPattern | None:
    """Check for known CMP providers (highest confidence)."""
    checks: list[tuple[re.Pattern[str], str, float]] = [
        (_COOKIEBOT_RE, "cookiebot", 0.95),
        (_ONETRUST_RE, "onetrust", 0.95),
        (_TRUSTARC_RE, "trustarc", 0.90),
        (_DIDOMI_RE, "didomi", 0.90),
        (_QUANTCAST_RE, "quantcast", 0.90),
        (_USERCENTRICS_RE, "usercentrics", 0.90),
    ]

    for pattern, provider, confidence in checks:
        match = pattern.search(html_lower)
        if match:
            return CookieConsentPattern(
                provider=provider,
                confidence=confidence,
                signals=(f"cmp:{provider}:{match.group()}",),
                accept_terms=_ACCEPT_TERMS_ALL,
            )

    return None


def _detect_generic_cookie(html_lower: str) -> CookieConsentPattern | None:
    """Check for generic cookie banners (lower confidence)."""
    match = _GENERIC_COOKIE_RE.search(html_lower)
    if match:
        return CookieConsentPattern(
            provider="generic",
            confidence=0.70,
            signals=(f"generic_cookie:{match.group()}",),
            accept_terms=_ACCEPT_TERMS_ALL,
        )
    return None


def detect_cookie_provider(html_lower: str) -> CookieConsentPattern | None:
    """Detect cookie consent provider from lowercased HTML.

    Named CMP providers are checked first (higher confidence).
    Falls back to generic cookie banner patterns.

    Never raises.
    """
    try:
        result = _detect_named_cmp(html_lower)
        if result is not None:
            return result
        return _detect_generic_cookie(html_lower)
    except Exception:
        return None
