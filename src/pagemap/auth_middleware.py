# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""ASGI authentication middleware — Bearer token auth for HTTP transport.

Pure ASGI middleware (no ``BaseHTTPMiddleware``) following the
``GatewayMiddleware`` pattern in ``gateway.py``.

Auth flow:
1. Extract ``Authorization: Bearer <token>`` header.
2. Validate key format, hash, lookup, expiry, revocation.
3. On success: set ``scope["state"]["client_id"]`` and call inner app.
4. On failure: send RFC 9457 problem+json response, emit telemetry, log audit.

Dependencies: api_key.py, problem_details.py, repository.py.
No server.py import (acyclic).
"""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from dataclasses import dataclass

from .api_key import display_prefix, hash_key, validate_key_format
from .problem_details import ProblemDetail, from_auth_invalid, from_auth_missing
from .repository import AuditEvent, RepositoryProtocol

logger = logging.getLogger(__name__)

# Health/readiness endpoints that bypass authentication
_BYPASS_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/ready",
        "/livez",
        "/readyz",
        "/startupz",
    }
)


# ---------------------------------------------------------------------------
# Internal exception for clean auth-failure control flow
# ---------------------------------------------------------------------------


@dataclass
class _AuthFailure(Exception):
    """Module-private exception carrying rejection context. Never escapes."""

    reason: str
    problem: ProblemDetail
    client_id: str
    key_hash: str
    log_prefix: str


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------


class AuthMiddleware:
    """Pure ASGI middleware for API key authentication.

    Constructor:
        ``AuthMiddleware(app, repository)``

    Bypasses: ``/health``, ``/ready``, ``/livez``, ``/readyz``, ``/startupz``.
    Non-HTTP/WS scopes (e.g. lifespan) pass through unconditionally.
    """

    def __init__(self, app, repository: RepositoryProtocol) -> None:
        self.app = app
        self.repository = repository

    async def __call__(self, scope, receive, send) -> None:
        # Non-HTTP/WS scopes (lifespan) pass through
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})

        # Health bypass — check path for HTTP requests
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path in _BYPASS_PATHS:
                await self.app(scope, receive, send)
                return

        # Authenticate
        try:
            client_id = await self._authenticate(scope)
        except _AuthFailure as failure:
            await self._reject(failure, scope, receive, send)
            return

        scope["state"]["client_id"] = client_id
        await self.app(scope, receive, send)

    async def _authenticate(self, scope) -> str:
        """Validate the Authorization header. Returns ``client_id`` on success.

        Raises ``_AuthFailure`` on any validation failure.
        """
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])

        # 1. Extract Authorization header
        auth_value: str | None = None
        for name, value in raw_headers:
            if name.lower() == b"authorization":
                auth_value = value.decode("latin-1")
                break

        if auth_value is None:
            raise _AuthFailure(
                reason="missing",
                problem=from_auth_missing(),
                client_id="",
                key_hash="",
                log_prefix="<no-key>",
            )

        # 2. Parse Bearer prefix
        if not auth_value.startswith("Bearer "):
            raise _AuthFailure(
                reason="missing",
                problem=from_auth_missing(),
                client_id="",
                key_hash="",
                log_prefix="<no-key>",
            )

        raw_key = auth_value[7:]  # len("Bearer ") == 7

        # 3. Validate key format
        fmt_err = validate_key_format(raw_key)
        if fmt_err is not None:
            raise _AuthFailure(
                reason="malformed",
                problem=from_auth_missing(),
                client_id="",
                key_hash="",
                log_prefix=display_prefix(raw_key),
            )

        # 4. Hash and lookup
        key_hash = hash_key(raw_key)
        client_id = key_hash[:12]
        log_prefix = display_prefix(raw_key)

        record = await self.repository.get_key(key_hash)
        if record is None:
            raise _AuthFailure(
                reason="not_found",
                problem=from_auth_invalid(reason="not_found", client_id=client_id),
                client_id=client_id,
                key_hash=key_hash,
                log_prefix=log_prefix,
            )

        # 5. Check expiry
        if record.expires_at is not None and time.time() > record.expires_at:
            raise _AuthFailure(
                reason="expired",
                problem=from_auth_invalid(reason="expired", client_id=client_id),
                client_id=client_id,
                key_hash=key_hash,
                log_prefix=log_prefix,
            )

        # 6. Check revocation
        if record.revoked:
            raise _AuthFailure(
                reason="revoked",
                problem=from_auth_invalid(reason="revoked", client_id=client_id),
                client_id=client_id,
                key_hash=key_hash,
                log_prefix=log_prefix,
            )

        return client_id

    async def _reject(self, failure: _AuthFailure, scope, receive, send) -> None:
        """Send error response, emit telemetry, log audit event."""
        # Send response (best-effort — client may have disconnected)
        try:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": ""})
            else:
                response = failure.problem.to_response()
                await response(scope, receive, send)
        except Exception:  # noqa: BLE001  # nosec B110
            pass

        # Emit telemetry (lazy import — project-wide pattern)
        try:
            from .telemetry import emit
            from .telemetry.events import AUTH_REJECTED, auth_rejected

            emit(AUTH_REJECTED, auth_rejected(client_id=failure.client_id, reason=failure.reason))
        except Exception:  # noqa: BLE001  # nosec B110
            pass

        # Log audit event (fire-and-forget)
        with suppress(Exception):
            await self.repository.log_event(
                AuditEvent(
                    event_type="auth_rejected",
                    key_hash=failure.key_hash,
                    client_ip=scope.get("state", {}).get("client_ip", ""),
                    detail=failure.reason,
                )
            )

        logger.warning("Auth rejected: %s reason=%s", failure.log_prefix, failure.reason)
