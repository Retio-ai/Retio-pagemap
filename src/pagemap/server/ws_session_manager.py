# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""WebSocket session manager — per-connection BrowserContext lifecycle + rate limiting.

Each WS connection gets an isolated BrowserContext that is destroyed on
disconnect (cookies, localStorage, sessionStorage all wiped).

Rate limiting uses a token-bucket algorithm:
- burst: 30 requests
- rate: 10 requests/second (refills 1 token per 100ms)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Rate Limiter ─────────────────────────────────────────────────────

_DEFAULT_BURST = 30
_DEFAULT_RATE = 10.0  # tokens per second


@dataclass(slots=True)
class TokenBucket:
    """Simple token-bucket rate limiter."""

    burst: int = _DEFAULT_BURST
    rate: float = _DEFAULT_RATE
    tokens: float = field(default=0.0, init=False)
    last_refill: float = field(default=0.0, init=False)

    def __post_init__(self):
        self.tokens = float(self.burst)
        self.last_refill = time.monotonic()

    def allow(self) -> bool:
        """Return True if request is allowed (consumes one token)."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


# ── WS Session ───────────────────────────────────────────────────────


@dataclass
class WsSession:
    """Per-WebSocket-connection session state."""

    connection_id: str
    client_id: str
    auth_method: str
    connected_at: float = field(default_factory=time.monotonic)
    rate_limiter: TokenBucket = field(default_factory=TokenBucket)
    browser_context_id: str | None = None
    _closed: bool = field(default=False, init=False)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True


# ── WS Session Manager ──────────────────────────────────────────────


class WsSessionManager:
    """Manages WebSocket connection lifecycle with BrowserContext isolation.

    Each connection gets:
    - An isolated BrowserContext (destroyed on disconnect)
    - A per-connection rate limiter (token bucket)
    - Session binding to client identity
    """

    __slots__ = ("_sessions", "_browser_pool", "_max_connections")

    def __init__(
        self,
        *,
        browser_pool: object | None = None,
        max_connections: int = 100,
    ) -> None:
        self._sessions: dict[str, WsSession] = {}
        self._browser_pool = browser_pool
        self._max_connections = max_connections

    async def on_connect(
        self,
        connection_id: str,
        client_id: str,
        auth_method: str,
    ) -> WsSession:
        """Register a new WebSocket connection.

        Creates isolated BrowserContext via BrowserPool if available.
        """
        if len(self._sessions) >= self._max_connections:
            raise ConnectionError("Maximum WebSocket connections exceeded")

        session = WsSession(
            connection_id=connection_id,
            client_id=client_id,
            auth_method=auth_method,
        )

        # Acquire isolated BrowserContext
        if self._browser_pool is not None:
            acquire_fn = getattr(self._browser_pool, "acquire", None)
            if acquire_fn is not None:
                try:
                    ctx_id = await acquire_fn(client_id)
                    session.browser_context_id = ctx_id
                except Exception:
                    logger.warning("Failed to acquire BrowserContext for WS %s", connection_id[:8])

        self._sessions[connection_id] = session
        logger.info(
            "WS connected: %s (client=%s, auth=%s)",
            connection_id[:8],
            client_id[:8],
            auth_method,
        )
        return session

    async def on_disconnect(self, connection_id: str) -> None:
        """Clean up a WebSocket connection.

        Destroys BrowserContext (wipes cookies, localStorage, sessionStorage).
        """
        session = self._sessions.pop(connection_id, None)
        if session is None:
            return

        session.close()

        # Destroy BrowserContext → wipes all session data
        if session.browser_context_id and self._browser_pool is not None:
            release_fn = getattr(self._browser_pool, "release", None)
            if release_fn is not None:
                try:
                    await release_fn(session.browser_context_id)
                except Exception:
                    logger.warning("Failed to release BrowserContext for WS %s", connection_id[:8])

        logger.info("WS disconnected: %s", connection_id[:8])

    def get_session(self, connection_id: str) -> WsSession | None:
        """Get session by connection ID."""
        return self._sessions.get(connection_id)

    def check_rate_limit(self, connection_id: str) -> bool:
        """Check if request is within rate limit. Returns True if allowed."""
        session = self._sessions.get(connection_id)
        if session is None:
            return False
        return session.rate_limiter.allow()

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @property
    def connection_ids(self) -> list[str]:
        return list(self._sessions.keys())
