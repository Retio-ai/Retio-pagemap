# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Security headers middleware — OWASP-recommended HTTP response headers + TLS enforcement.

Standalone leaf module with zero dependency on server.py.
Uses stdlib only (json, logging).

Design choices:

- **Pure ASGI** — no BaseHTTPMiddleware (avoids body buffering, SSE issues).
- **Deduplication** — existing app headers are never overwritten.
- **421 Misdirected Request** — RFC 9457 Problem Details JSON body.
- **HSTS** — only injected when ``require_tls=True`` (opt-in).
- **Proxy-aware TLS detection** — reads ``scope["state"]["forwarded_proto"]``
  set by GatewayMiddleware (outermost).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# ── Security header constants (OWASP 2026) ────────────────────────────

_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cache-control", b"no-store, max-age=0"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"x-permitted-cross-domain-policies", b"none"),
)

_HSTS_HEADER: tuple[bytes, bytes] = (
    b"strict-transport-security",
    b"max-age=63072000; includeSubDomains; preload",
)

# ── Helpers ────────────────────────────────────────────────────────────


def _is_https(scope: dict) -> bool:
    """Check if request is over HTTPS (direct or via trusted reverse proxy)."""
    if scope.get("scheme") == "https":
        return True
    state = scope.get("state", {})
    return state.get("forwarded_proto") == "https"


# ── ASGI Middleware ────────────────────────────────────────────────────


class SecurityHeadersMiddleware:
    """Pure ASGI middleware for OWASP security headers and TLS enforcement.

    Injects security headers on every ``http.response.start`` message.
    When ``require_tls=True``, non-HTTPS requests receive a 421 response.
    """

    def __init__(self, app, *, require_tls: bool = False) -> None:
        self.app = app
        self.require_tls = require_tls

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})

        if self.require_tls and not _is_https(scope):
            await _send_421(send)
            return

        _injected = False

        async def _send_with_security_headers(message) -> None:
            nonlocal _injected
            if message["type"] == "http.response.start" and not _injected:
                _injected = True
                headers = list(message.get("headers", []))
                existing = frozenset(h[0].lower() for h in headers)
                for name, value in _SECURITY_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                if self.require_tls and b"strict-transport-security" not in existing:
                    headers.append(_HSTS_HEADER)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send_with_security_headers)


async def _send_421(send) -> None:
    """Send a 421 Misdirected Request with RFC 9457 Problem Details body."""
    body = json.dumps(
        {
            "type": "https://www.retio.ai/pagemap/errors/tls-required",
            "title": "TLS Required",
            "status": 421,
            "detail": "This endpoint requires HTTPS.",
        }
    ).encode("utf-8")

    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/problem+json"),
        (b"content-length", str(len(body)).encode("latin-1")),
    ]
    for name, value in _SECURITY_HEADERS:
        headers.append((name, value))

    await send(
        {
            "type": "http.response.start",
            "status": 421,
            "headers": headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )
