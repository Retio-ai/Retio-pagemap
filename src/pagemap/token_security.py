# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Token scrubbing utilities — detect and redact API keys from text and headers.

Standalone leaf module with zero dependency on server.py.
Uses stdlib only (re, logging). Telemetry is lazy-imported in scrub_and_report().
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_SK_PM_RE = re.compile(r"sk-pm-v\d+-[A-Za-z0-9_-]{43}")
_SK_PM_REPLACEMENT = "sk-pm-***"

_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)

_AUTH_HEADER_NAMES = frozenset({b"authorization"})

# ── Public API ─────────────────────────────────────────────────────────


def scrub_from_text(text: str) -> str:
    """Replace all ``sk-pm-*`` tokens in *text* with ``sk-pm-***``."""
    if not text:
        return text
    return _SK_PM_RE.sub(_SK_PM_REPLACEMENT, text)


def contains_token(text: str) -> bool:
    """Return ``True`` if *text* contains an ``sk-pm-*`` token."""
    if not text:
        return False
    return bool(_SK_PM_RE.search(text))


def scrub_headers(
    headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    """Scrub sensitive tokens from ASGI-style header pairs.

    - ``Authorization`` headers: Bearer token replaced with ``Bearer ***``.
    - All other headers: ``sk-pm-*`` patterns scrubbed if found.

    Uses ``latin-1`` encoding (ASGI convention).
    """
    result: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        if name.lower() in _AUTH_HEADER_NAMES:
            decoded = value.decode("latin-1")
            scrubbed = _BEARER_RE.sub("Bearer ***", decoded)
            result.append((name, scrubbed.encode("latin-1")))
        else:
            if b"sk-pm-" in value:
                decoded = value.decode("latin-1")
                scrubbed = _SK_PM_RE.sub(_SK_PM_REPLACEMENT, decoded)
                result.append((name, scrubbed.encode("latin-1")))
            else:
                result.append((name, value))
    return result


def scrub_and_report(text: str, *, field: str = "unknown") -> str:
    """Scrub ``sk-pm-*`` tokens and emit telemetry if any were found.

    Telemetry is lazy-imported so this module works even without
    the telemetry subsystem configured.
    """
    if not text:
        return text
    if not contains_token(text):
        return text

    scrubbed = scrub_from_text(text)

    logger.warning("Token detected and scrubbed in field=%s", field)
    try:
        from .telemetry import emit
        from .telemetry.events import (
            PROMPT_INJECTION_SANITIZED,
            prompt_injection_sanitized,
        )

        emit(
            PROMPT_INJECTION_SANITIZED,
            prompt_injection_sanitized(field=field, pattern="sk-pm-*"),
        )
    except Exception:  # noqa: BLE001  # nosec B110
        pass

    return scrubbed
