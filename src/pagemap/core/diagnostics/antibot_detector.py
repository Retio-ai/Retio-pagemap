# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Provider-specific anti-bot/captcha detection.

Pre-compiled regex patterns for 6 providers: Turnstile, reCAPTCHA, hCaptcha,
Cloudflare, Akamai, Generic.

Target: <2ms per invocation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from . import AntibotDetection, AntibotProvider, AntibotSessionState

# ── Provider patterns (module-level pre-compiled) ─────────────────

_PROVIDERS: tuple[tuple[AntibotProvider, re.Pattern[str], float], ...] = (
    (
        AntibotProvider.TURNSTILE,
        re.compile(r"cf-turnstile|challenges\.cloudflare\.com/turnstile", re.IGNORECASE),
        0.95,
    ),
    (
        AntibotProvider.RECAPTCHA,
        re.compile(r"g-recaptcha|google\.com/recaptcha|grecaptcha", re.IGNORECASE),
        0.95,
    ),
    (
        AntibotProvider.HCAPTCHA,
        re.compile(r"h-captcha|hcaptcha\.com|data-hcaptcha", re.IGNORECASE),
        0.95,
    ),
    (
        AntibotProvider.CLOUDFLARE,
        re.compile(r"cf-browser-verification|just\s+a\s+moment|__cf_chl", re.IGNORECASE),
        0.90,
    ),
    (
        AntibotProvider.AKAMAI,
        re.compile(r"akamai.*bot.*manager|_abck|sensor_data", re.IGNORECASE),
        0.85,
    ),
    (
        AntibotProvider.GENERIC,
        re.compile(r"captcha|challenge-platform", re.IGNORECASE),
        0.70,
    ),
)

# Visibility heuristic: very short body text
_SHORT_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)


def detect_antibot(*, raw_html: str, html_lower: str) -> AntibotDetection | None:
    """Detect anti-bot provider from HTML. Returns None if no antibot found. Never raises."""
    try:
        return _detect_impl(raw_html, html_lower)
    except Exception:
        return None


def _detect_impl(raw_html: str, html_lower: str) -> AntibotDetection | None:
    for provider, pattern, base_confidence in _PROVIDERS:
        match = pattern.search(html_lower)
        if match:
            signals = [f"pattern_match={match.group()!r}"]
            confidence = base_confidence

            # Visibility heuristic: check if challenge is fullscreen
            challenge_visible = _check_challenge_visible(html_lower, signals)

            stealth_tips = _stealth_recommendations(provider, 0)

            return AntibotDetection(
                provider=provider,
                confidence=confidence,
                signals=tuple(signals),
                challenge_visible=challenge_visible,
                stealth_tips=stealth_tips,
            )

    return None


def _check_challenge_visible(html_lower: str, signals: list[str]) -> bool:
    """Check if the captcha challenge appears to be the main page content."""
    # Short body text suggests captcha/challenge page
    body_match = _SHORT_BODY_RE.search(html_lower)
    if body_match:
        body_text = body_match.group(1)
        # Strip HTML tags to get raw text length
        text_only = re.sub(r"<[^>]+>", "", body_text).strip()
        if len(text_only) < 200:
            signals.append(f"short_body_text={len(text_only)}")
            return True

    return False


# ── Session state tracking ────────────────────────────────────────


def _stealth_recommendations(provider: AntibotProvider, consecutive: int) -> tuple[str, ...]:
    """Generate provider-specific stealth tips. Never raises."""
    try:
        tips: list[str] = []
        if provider in (AntibotProvider.CLOUDFLARE, AntibotProvider.TURNSTILE):
            tips.extend(("slow_down_requests", "add_random_delays"))
        elif provider == AntibotProvider.AKAMAI:
            tips.extend(("rotate_user_agent", "avoid_headless_detection"))
        elif provider in (AntibotProvider.RECAPTCHA, AntibotProvider.HCAPTCHA):
            tips.append("manual_verification_needed")
        else:
            tips.append("increase_page_load_delay")

        if consecutive >= 3:
            tips.extend(("consider_alternative_url", "site_may_require_authentication"))

        return tuple(tips)
    except Exception:
        return ()


def update_session_state(
    state: AntibotSessionState,
    detection: AntibotDetection | None,
) -> None:
    """Update session-level antibot state. Never raises."""
    try:
        if detection is not None:
            state.detection_count += 1
            state.last_provider = detection.provider.value
            state.consecutive_blocks += 1
            state.resolved = False
            if not state.first_detected_at:
                state.first_detected_at = datetime.now(UTC).isoformat()
        else:
            if state.consecutive_blocks > 0:
                state.resolved = True
            state.consecutive_blocks = 0
    except Exception:  # nosec B110
        pass
