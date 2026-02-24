# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for AuthMiddleware — ASGI authentication middleware."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from pagemap.api_key import KeyRecord, KeyScope, KeyVersion, generate_api_key, hash_key
from pagemap.auth_middleware import _BYPASS_PATHS, AuthMiddleware
from pagemap.repository import InMemoryRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(
    path: str = "/mcp",
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
) -> dict:
    """Build a minimal ASGI scope."""
    return {
        "type": scope_type,
        "path": path,
        "headers": headers or [],
        "state": {},
    }


def _bearer_header(raw_key: str) -> list[tuple[bytes, bytes]]:
    """Build headers with Authorization: Bearer <raw_key>."""
    return [(b"authorization", f"Bearer {raw_key}".encode("latin-1"))]


def _capture_app():
    """Returns (app, captured) where captured records scope state and sends 200."""
    captured: dict = {}

    async def app(scope, receive, send):
        captured["scope"] = scope
        captured["state"] = dict(scope.get("state", {}))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app, captured


async def _collect_response(middleware, scope, receive=None) -> tuple[int, bytes, dict]:
    """Run middleware and collect response status, body, and headers."""
    if receive is None:
        receive = AsyncMock(return_value={"type": "http.request", "body": b""})

    status = None
    body_parts = []
    headers_dict = {}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            for name, value in message.get("headers", []):
                headers_dict[name.decode("latin-1").lower()] = value.decode("latin-1")
        elif message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await middleware(scope, receive, send)
    return status, b"".join(body_parts), headers_dict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repository():
    """Fresh InMemoryRepository for testing auth logic."""
    return InMemoryRepository()


@pytest.fixture
async def valid_key(repository):
    """Generate and store a valid key, return raw_key."""
    raw_key, key_hash = generate_api_key()
    record = KeyRecord(
        key_hash=key_hash,
        label="test",
        version=KeyVersion.V1,
        created_at=time.time(),
        expires_at=None,
        revoked=False,
        scopes=frozenset({KeyScope.FULL}),
    )
    await repository.store_key(record)
    return raw_key


@pytest.fixture
async def expired_key(repository):
    """Generate and store an expired key, return raw_key."""
    raw_key, key_hash = generate_api_key()
    record = KeyRecord(
        key_hash=key_hash,
        label="expired",
        version=KeyVersion.V1,
        created_at=time.time() - 100,
        expires_at=time.time() - 1,  # Already expired
        revoked=False,
        scopes=frozenset({KeyScope.FULL}),
    )
    await repository.store_key(record)
    return raw_key


@pytest.fixture
async def revoked_key(repository):
    """Generate and store a revoked key, return raw_key."""
    raw_key, key_hash = generate_api_key()
    record = KeyRecord(
        key_hash=key_hash,
        label="revoked",
        version=KeyVersion.V1,
        created_at=time.time(),
        expires_at=None,
        revoked=True,
        scopes=frozenset({KeyScope.FULL}),
    )
    await repository.store_key(record)
    return raw_key


# ---------------------------------------------------------------------------
# TestAuthSuccess
# ---------------------------------------------------------------------------


class TestAuthSuccess:
    async def test_valid_key_passes_through(self, repository, valid_key):
        inner_app, captured = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(valid_key))

        status, body, _ = await _collect_response(middleware, scope)
        assert status == 200
        assert body == b"ok"

    async def test_client_id_set_in_state(self, repository, valid_key):
        inner_app, captured = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(valid_key))

        await middleware(scope, AsyncMock(), AsyncMock())

        expected_client_id = hash_key(valid_key)[:12]
        assert captured["state"]["client_id"] == expected_client_id


# ---------------------------------------------------------------------------
# TestAuthRejection
# ---------------------------------------------------------------------------


class TestAuthRejection:
    async def test_missing_header_401(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=[])

        status, body, headers = await _collect_response(middleware, scope)
        assert status == 401

    async def test_no_bearer_prefix_401(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        headers = [(b"authorization", b"Basic abc123")]
        scope = _make_scope(headers=headers)

        status, _, _ = await _collect_response(middleware, scope)
        assert status == 401

    async def test_malformed_key_401(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        headers = [(b"authorization", b"Bearer not-a-valid-key-format")]
        scope = _make_scope(headers=headers)

        status, _, _ = await _collect_response(middleware, scope)
        assert status == 401

    async def test_not_found_403(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        # Generate a valid-format key but don't store it
        raw_key, _ = generate_api_key()
        scope = _make_scope(headers=_bearer_header(raw_key))

        status, _, _ = await _collect_response(middleware, scope)
        assert status == 403

    async def test_expired_403(self, repository, expired_key):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(expired_key))

        status, _, _ = await _collect_response(middleware, scope)
        assert status == 403

    async def test_revoked_403(self, repository, revoked_key):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(revoked_key))

        status, _, _ = await _collect_response(middleware, scope)
        assert status == 403

    async def test_problem_json_content_type(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=[])

        _, _, headers = await _collect_response(middleware, scope)
        assert headers.get("content-type") == "application/problem+json"


# ---------------------------------------------------------------------------
# TestHealthBypass
# ---------------------------------------------------------------------------


class TestHealthBypass:
    @pytest.mark.parametrize("path", sorted(_BYPASS_PATHS))
    async def test_bypass_paths_no_auth(self, repository, path):
        inner_app, captured = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(path=path, headers=[])

        status, body, _ = await _collect_response(middleware, scope)
        assert status == 200

    async def test_non_bypass_requires_auth(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(path="/mcp", headers=[])

        status, _, _ = await _collect_response(middleware, scope)
        assert status == 401


# ---------------------------------------------------------------------------
# TestLifespanPassthrough
# ---------------------------------------------------------------------------


class TestLifespanPassthrough:
    async def test_lifespan_passes_through(self, repository):
        inner_app, captured = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(scope_type="lifespan")

        # Lifespan should pass through without auth
        await middleware(scope, AsyncMock(), AsyncMock())
        assert "scope" in captured

    async def test_websocket_requires_auth(self, repository):
        """WebSocket scope without auth should be rejected with websocket.close."""
        inner_app, captured = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(scope_type="websocket", headers=[])
        ws_close: dict = {}

        async def send(message):
            if message["type"] == "websocket.close":
                ws_close.update(message)

        await middleware(scope, AsyncMock(), send)
        assert "scope" not in captured
        assert ws_close.get("code") == 1008
        assert len(repository.audit_log) == 1


# ---------------------------------------------------------------------------
# TestAuditEvent
# ---------------------------------------------------------------------------


class TestAuditEvent:
    async def test_audit_on_missing_header(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=[])

        await _collect_response(middleware, scope)

        assert len(repository.audit_log) == 1
        event = repository.audit_log[0]
        assert event.event_type == "auth_rejected"
        assert event.detail == "missing"

    async def test_key_hash_populated_on_revoked(self, repository, revoked_key):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(revoked_key))

        await _collect_response(middleware, scope)

        assert len(repository.audit_log) == 1
        event = repository.audit_log[0]
        assert event.key_hash == hash_key(revoked_key)

    async def test_no_audit_on_success(self, repository, valid_key):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(valid_key))

        await _collect_response(middleware, scope)

        assert len(repository.audit_log) == 0


# ---------------------------------------------------------------------------
# TestTelemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    async def test_auth_rejected_emitted(self, repository):
        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=[])

        with patch("pagemap.telemetry.emit") as mock_emit:
            await _collect_response(middleware, scope)
            mock_emit.assert_called_once()
            args = mock_emit.call_args
            assert args[0][0] == "pagemap.auth.rejected"
            payload = args[0][1]
            assert payload["reason"] == "missing"


# ---------------------------------------------------------------------------
# TestTokenSecurity
# ---------------------------------------------------------------------------


class TestTokenSecurity:
    async def test_raw_key_not_in_response_body(self, repository):
        """Verify that the raw API key never appears in error responses."""
        raw_key, _ = generate_api_key()
        # Don't store — will get 403

        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        scope = _make_scope(headers=_bearer_header(raw_key))

        _, body, _ = await _collect_response(middleware, scope)

        # The raw key must not appear in the response
        assert raw_key.encode() not in body

        # Verify the body is valid JSON with expected structure
        data = json.loads(body)
        assert "type" in data
        assert "status" in data

    async def test_raw_key_not_in_error_body(self, repository):
        """Verify raw key not leaked in 401 responses either."""
        raw_key = "sk-pm-v1-" + "A" * 43  # Valid format but malformed enough to test

        inner_app, _ = _capture_app()
        middleware = AuthMiddleware(inner_app, repository)
        headers = [(b"authorization", f"Bearer {raw_key}".encode("latin-1"))]
        scope = _make_scope(headers=headers)

        _, body, _ = await _collect_response(middleware, scope)
        # Raw key should not appear in response
        assert raw_key.encode() not in body
