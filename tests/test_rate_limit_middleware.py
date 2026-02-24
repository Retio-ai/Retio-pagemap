# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for RateLimitMiddleware — 12 scenarios covering enforcement, bypass,
telemetry, body parsing, and usage recording."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from pagemap.rate_limit_middleware import RateLimitMiddleware
from pagemap.rate_limiter import RateLimitConfig, RateLimiter
from pagemap.repository import InMemoryRepository
from pagemap.telemetry.events import RATE_LIMIT_EXCEEDED, RATE_LIMIT_WARNING

# ── Helpers ──────────────────────────────────────────────────────────


def _make_scope(
    *,
    path: str = "/mcp",
    method: str = "POST",
    client_ip: str = "1.2.3.4",
    client_id: str = "",
) -> dict:
    """Build a minimal ASGI HTTP scope."""
    state: dict = {}
    if client_ip:
        state["client_ip"] = client_ip
    if client_id:
        state["client_id"] = client_id
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "state": state,
    }


def _make_jsonrpc_receive(tool_name: str):
    """Return a receive callable that yields a JSON-RPC tools/call body."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name},
            "id": 1,
        }
    ).encode()

    _called = False

    async def receive() -> dict:
        nonlocal _called
        if not _called:
            _called = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def _make_empty_receive():
    """Return a receive callable that yields an empty body."""
    _called = False

    async def receive() -> dict:
        nonlocal _called
        if not _called:
            _called = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    return receive


async def _noop_app(scope, receive, send):
    """Inner app that sends 200 OK with empty body."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


class _ResponseCapture:
    """Captures ASGI send() calls for assertion."""

    def __init__(self):
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def start(self) -> dict | None:
        for m in self.messages:
            if m["type"] == "http.response.start":
                return m
        return None

    @property
    def status(self) -> int | None:
        s = self.start
        return s["status"] if s else None

    @property
    def headers_dict(self) -> dict[str, str]:
        """Parse response headers into a lowercase-key dict."""
        s = self.start
        if not s:
            return {}
        return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in s.get("headers", [])}

    @property
    def body(self) -> bytes:
        parts = []
        for m in self.messages:
            if m["type"] == "http.response.body":
                parts.append(m.get("body", b""))
        return b"".join(parts)

    @property
    def body_json(self) -> dict:
        return json.loads(self.body)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def enabled_limiter():
    """RateLimiter with small capacity and near-zero refill for deterministic tests."""
    return RateLimiter(
        RateLimitConfig(
            enabled=True,
            capacity=10,
            refill_rate=0.0001,
            global_capacity=100,
            global_refill_rate=0.0001,
        )
    )


@pytest.fixture()
def repo():
    return InMemoryRepository()


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowed_request_passes_with_headers(enabled_limiter):
    """T1: Allowed request reaches inner app and response contains RateLimit-* headers."""
    app_called = False

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True
        await _noop_app(scope, receive, send)

    mw = RateLimitMiddleware(inner_app, enabled_limiter)
    scope = _make_scope()
    capture = _ResponseCapture()

    await mw(scope, _make_empty_receive(), capture)

    assert app_called
    assert capture.status == 200
    hdrs = capture.headers_dict
    assert "ratelimit-limit" in hdrs
    assert "ratelimit-remaining" in hdrs
    assert "ratelimit-reset" in hdrs


@pytest.mark.asyncio
async def test_exhausted_bucket_returns_429(enabled_limiter):
    """T2: Exhausted bucket returns 429 and inner app is NOT called."""
    # Exhaust the per-client bucket (capacity=10, default cost=3)
    for _ in range(4):
        await enabled_limiter.acquire("1.2.3.4", "")

    app_called = False

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True

    mw = RateLimitMiddleware(inner_app, enabled_limiter)
    scope = _make_scope()
    capture = _ResponseCapture()

    await mw(scope, _make_empty_receive(), capture)

    assert not app_called
    assert capture.status == 429
    body = capture.body_json
    assert "rate-limit-exceeded" in body["type"]


@pytest.mark.asyncio
async def test_429_includes_retry_after(enabled_limiter):
    """T3: 429 response includes Retry-After header."""
    for _ in range(4):
        await enabled_limiter.acquire("1.2.3.4", "")

    mw = RateLimitMiddleware(_noop_app, enabled_limiter)
    scope = _make_scope()
    capture = _ResponseCapture()

    await mw(scope, _make_empty_receive(), capture)

    assert capture.status == 429
    assert "retry-after" in capture.headers_dict


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/health", "/ready", "/livez", "/readyz", "/startupz"],
)
async def test_health_endpoints_bypass(enabled_limiter, path):
    """T4: Health endpoints bypass rate limiting entirely."""
    app_called = False

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True
        await _noop_app(scope, receive, send)

    mw = RateLimitMiddleware(inner_app, enabled_limiter)
    scope = _make_scope(path=path, method="GET")
    capture = _ResponseCapture()

    await mw(scope, _make_empty_receive(), capture)

    assert app_called
    # No rate-limit headers injected on bypass
    assert "ratelimit-limit" not in capture.headers_dict


@pytest.mark.asyncio
async def test_warning_telemetry_at_threshold(enabled_limiter):
    """T5: RATE_LIMIT_WARNING emitted when remaining ≤ 20% of limit."""
    # Deplete to ≤ 20% remaining: capacity=10, each default cost=3
    # After 2 calls: 10 - 6 = 4 remaining → 4/10 = 40% (no warning)
    # After 3 calls: 10 - 9 = 1 remaining → 1/10 = 10% (warning)
    await enabled_limiter.acquire("1.2.3.4", "")
    await enabled_limiter.acquire("1.2.3.4", "")

    mw = RateLimitMiddleware(_noop_app, enabled_limiter)

    with patch("pagemap.rate_limit_middleware.emit") as mock_emit:
        scope = _make_scope()
        capture = _ResponseCapture()
        await mw(scope, _make_empty_receive(), capture)

        assert capture.status == 200
        # Verify RATE_LIMIT_WARNING was emitted
        warning_calls = [c for c in mock_emit.call_args_list if c[0][0] == RATE_LIMIT_WARNING]
        assert len(warning_calls) == 1


@pytest.mark.asyncio
async def test_unauthenticated_fallback_to_client_ip(enabled_limiter):
    """T6: Without client_id, rate limiter is called with client_ip."""
    mw = RateLimitMiddleware(_noop_app, enabled_limiter)
    scope = _make_scope(client_id="", client_ip="10.0.0.1")
    capture = _ResponseCapture()

    with patch.object(enabled_limiter, "acquire", wraps=enabled_limiter.acquire) as spy:
        await mw(scope, _make_empty_receive(), capture)

        assert capture.status == 200
        spy.assert_called_once()
        assert spy.call_args[0][0] == "10.0.0.1"


@pytest.mark.asyncio
async def test_usage_recording_on_success(enabled_limiter, repo):
    """T7: Usage record created after successful request."""
    mw = RateLimitMiddleware(_noop_app, enabled_limiter, repository=repo)
    scope = _make_scope()
    capture = _ResponseCapture()

    await mw(scope, _make_empty_receive(), capture)

    # Allow fire-and-forget task to complete
    await asyncio.sleep(0.01)

    assert len(repo.usage_records) == 1
    rec = repo.usage_records[0]
    assert rec.key_hash == "1.2.3.4"
    assert rec.cost > 0


@pytest.mark.asyncio
async def test_exceeded_telemetry_emitted(enabled_limiter):
    """T8: RATE_LIMIT_EXCEEDED telemetry emitted on 429."""
    for _ in range(4):
        await enabled_limiter.acquire("1.2.3.4", "")

    mw = RateLimitMiddleware(_noop_app, enabled_limiter)

    with patch("pagemap.rate_limit_middleware.emit") as mock_emit:
        scope = _make_scope()
        capture = _ResponseCapture()
        await mw(scope, _make_empty_receive(), capture)

        assert capture.status == 429
        exceeded_calls = [c for c in mock_emit.call_args_list if c[0][0] == RATE_LIMIT_EXCEEDED]
        assert len(exceeded_calls) == 1


@pytest.mark.asyncio
async def test_tool_name_extracted_from_jsonrpc(enabled_limiter):
    """T9: Tool name extracted from JSON-RPC body; inner app can re-read body."""
    received_body = None

    async def inner_app(scope, receive, send):
        nonlocal received_body
        msg = await receive()
        received_body = msg.get("body", b"")
        await _noop_app(scope, receive, send)

    mw = RateLimitMiddleware(inner_app, enabled_limiter)
    scope = _make_scope()
    capture = _ResponseCapture()

    with patch.object(enabled_limiter, "acquire", wraps=enabled_limiter.acquire) as spy:
        await mw(scope, _make_jsonrpc_receive("get_page_map"), capture)

        assert capture.status == 200
        # acquire() was called with the extracted tool name
        spy.assert_called_once()
        assert spy.call_args[0][1] == "get_page_map"

    # Inner app received the replayed body
    assert received_body is not None
    data = json.loads(received_body)
    assert data["params"]["name"] == "get_page_map"


@pytest.mark.asyncio
async def test_non_http_scope_passthrough(enabled_limiter):
    """T10: Non-HTTP scopes (e.g., lifespan) pass through directly."""
    app_called = False

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True

    mw = RateLimitMiddleware(inner_app, enabled_limiter)

    async def noop_receive():
        return {"type": "lifespan.startup"}

    await mw({"type": "lifespan"}, noop_receive, AsyncMock())

    assert app_called


@pytest.mark.asyncio
async def test_invalid_json_body_defaults_gracefully(enabled_limiter):
    """T11: Invalid JSON body does not crash; default tool cost applied."""
    _called = False

    async def bad_body_receive() -> dict:
        nonlocal _called
        if not _called:
            _called = True
            return {
                "type": "http.request",
                "body": b"not json at all {{{",
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    mw = RateLimitMiddleware(_noop_app, enabled_limiter)
    scope = _make_scope()
    capture = _ResponseCapture()

    await mw(scope, bad_body_receive, capture)

    assert capture.status == 200


@pytest.mark.asyncio
async def test_no_repository_no_crash(enabled_limiter):
    """T12: Middleware works without repository param (no crash on record_usage)."""
    mw = RateLimitMiddleware(_noop_app, enabled_limiter, repository=None)
    scope = _make_scope()
    capture = _ResponseCapture()

    await mw(scope, _make_empty_receive(), capture)

    assert capture.status == 200
