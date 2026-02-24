# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Rate-limit ASGI middleware — enforces token-bucket limits at HTTP layer.

Wraps ``RateLimiter`` as pure ASGI middleware, returning 429 responses
with RFC 9457 problem details when rate limits are exceeded.

Position in middleware chain: Gateway → **RateLimit** → Auth → App.
Since Auth runs after this middleware, ``client_id`` may not be available;
falls back to ``client_ip`` → ``"anonymous"``.

Design:

- **Pure ASGI** — no BaseHTTPMiddleware (avoids body buffering, SSE issues).
- **Body buffering** for JSON-RPC tool name extraction (64 KB max).
- **Fire-and-forget** usage recording with GC-safe task references.
- **Header injection** via send-wrapper (same pattern as ``gateway.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from .problem_details import from_rate_limit
from .rate_limiter import RateLimiter, RateLimitResult, tool_cost
from .repository import RepositoryProtocol, UsageRecord
from .telemetry import emit
from .telemetry.events import (
    RATE_LIMIT_EXCEEDED,
    RATE_LIMIT_WARNING,
    rate_limit_exceeded,
    rate_limit_warning,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_BYPASS_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/livez", "/readyz", "/startupz"})

_WARNING_THRESHOLD: float = 0.2
_MAX_BODY_BUFFER: int = 64 * 1024  # 64 KB

# ── Fire-and-forget task set (prevents GC) ───────────────────────────

_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro: Coroutine) -> None:
    """Schedule a coroutine as a fire-and-forget task with GC protection."""
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ── Middleware ────────────────────────────────────────────────────────


class RateLimitMiddleware:
    """Pure ASGI middleware enforcing token-bucket rate limits.

    Returns 429 with RFC 9457 problem details when limits are exceeded.
    Injects ``RateLimit-*`` headers on allowed responses.
    """

    def __init__(
        self,
        app: Any,
        rate_limiter: RateLimiter,
        *,
        repository: RepositoryProtocol | None = None,
    ) -> None:
        self.app = app
        self.rate_limiter = rate_limiter
        self.repository = repository

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        # Type guard — non-HTTP scopes pass through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Defensive init
        scope.setdefault("state", {})

        # Health bypass — no rate check
        path = scope.get("path", "")
        if path in _BYPASS_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract client identity (client_id → client_ip → "anonymous")
        client_id = _extract_client_id(scope)

        # Extract tool name from JSON-RPC body (may buffer POST body)
        tool_name, replay_receive = await _extract_tool_name(scope, receive)

        # Acquire rate limit
        cost = tool_cost(tool_name)
        result = await self.rate_limiter.acquire(client_id, tool_name)

        if not result.allowed:
            _emit_exceeded(client_id, tool_name, cost, result)
            response = from_rate_limit(result, client_id=client_id, tool_name=tool_name).to_response()
            await response(scope, replay_receive, send)
            return

        # Warning telemetry when remaining tokens are low
        if _should_warn(result):
            _emit_warning(client_id, result)

        # Fire-and-forget usage recording
        self._record_usage(client_id, tool_name, cost)

        # Forward to inner app with RateLimit-* headers injected
        wrapped_send = _make_send_wrapper(send, result)
        await self.app(scope, replay_receive, wrapped_send)

    # ── Usage recording ──────────────────────────────────────────────

    def _record_usage(self, client_id: str, tool_name: str, cost: int) -> None:
        """Fire-and-forget usage record. No-op if repository is None."""
        if self.repository is None:
            return
        record = UsageRecord(key_hash=client_id, tool=tool_name, cost=cost)
        _fire_and_forget(self._safe_record(record))

    async def _safe_record(self, record: UsageRecord) -> None:
        """Record usage, swallowing any exception."""
        try:
            await self.repository.record_usage(record)  # type: ignore[union-attr]
        except Exception:
            logger.debug("Failed to record usage: %s", record, exc_info=True)


# ── Static helpers ───────────────────────────────────────────────────


def _extract_client_id(scope: dict) -> str:
    """Extract client identifier: client_id → client_ip → 'anonymous'."""
    state = scope.get("state", {})
    client_id = state.get("client_id", "")
    if client_id:
        return client_id
    client_ip = state.get("client_ip", "")
    if client_ip:
        return client_ip
    return "anonymous"


async def _extract_tool_name(scope: dict, receive: Callable) -> tuple[str, Callable]:
    """Extract tool name from JSON-RPC ``tools/call`` body.

    Buffers up to ``_MAX_BODY_BUFFER`` bytes from POST requests.
    Returns ``(tool_name, replay_receive)`` where replay_receive replays
    the buffered body so the inner app can read it again.

    For non-POST requests, returns ``("", receive)`` unchanged.
    """
    method = scope.get("method", "")
    if method != "POST":
        return "", receive

    # Buffer body chunks
    body_parts: list[bytes] = []
    total = 0

    while True:
        message = await receive()
        chunk = message.get("body", b"")
        total += len(chunk)
        if total <= _MAX_BODY_BUFFER:
            body_parts.append(chunk)
        if not message.get("more_body", False):
            break

    body = b"".join(body_parts)

    # Create replay receive callable
    _replayed = False

    async def replay_receive() -> dict:
        nonlocal _replayed
        if not _replayed:
            _replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    # Parse JSON-RPC tools/call to extract tool name
    tool_name = ""
    try:
        if body:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("method") == "tools/call":
                params = data.get("params", {})
                if isinstance(params, dict):
                    name = params.get("name", "")
                    if isinstance(name, str):
                        tool_name = name
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pass

    return tool_name, replay_receive


def _make_send_wrapper(send: Callable, result: RateLimitResult) -> Callable:
    """Wrap send to inject RateLimit-* headers into http.response.start.

    Uses ``nonlocal _injected`` flag pattern (same as ``gateway.py:342-349``).
    Returns ``send`` unchanged for bypass results.
    """
    if result.scope == "bypass":
        return send

    headers_to_inject = result.to_headers()
    _injected = False

    async def wrapped_send(message: dict) -> None:
        nonlocal _injected
        if message["type"] == "http.response.start" and not _injected:
            _injected = True
            headers = list(message.get("headers", []))
            for name, value in headers_to_inject.items():
                headers.append((name.lower().encode("latin-1"), str(value).encode("latin-1")))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped_send


def _should_warn(result: RateLimitResult) -> bool:
    """Check if remaining tokens are at or below the warning threshold."""
    return result.scope != "bypass" and result.limit > 0 and result.remaining / result.limit <= _WARNING_THRESHOLD


def _emit_exceeded(client_id: str, tool_name: str, cost: int, result: RateLimitResult) -> None:
    """Emit RATE_LIMIT_EXCEEDED telemetry event."""
    emit(
        RATE_LIMIT_EXCEEDED,
        rate_limit_exceeded(
            client_id=client_id,
            tool=tool_name,
            cost=cost,
            remaining=result.remaining,
            retry_after=result.retry_after,
        ),
    )


def _emit_warning(client_id: str, result: RateLimitResult) -> None:
    """Emit RATE_LIMIT_WARNING telemetry event."""
    emit(
        RATE_LIMIT_WARNING,
        rate_limit_warning(
            client_id=client_id,
            remaining=result.remaining,
            limit=result.limit,
        ),
    )
