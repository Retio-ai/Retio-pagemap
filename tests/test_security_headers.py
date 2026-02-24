# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for security headers middleware — OWASP headers, TLS enforcement, ASGI middleware."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from pagemap.security_headers import (
    _HSTS_HEADER,
    _SECURITY_HEADERS,
    SecurityHeadersMiddleware,
    _is_https,
)

# ── TestIsHttps ───────────────────────────────────────────────────────


class TestIsHttps:
    """Tests for _is_https() helper."""

    def test_scheme_https(self):
        assert _is_https({"scheme": "https"}) is True

    def test_scheme_http_no_proxy(self):
        assert _is_https({"scheme": "http"}) is False

    def test_forwarded_proto_https(self):
        scope = {"scheme": "http", "state": {"forwarded_proto": "https"}}
        assert _is_https(scope) is True

    def test_forwarded_proto_http(self):
        scope = {"scheme": "http", "state": {"forwarded_proto": "http"}}
        assert _is_https(scope) is False


# ── TestSecurityHeaders ──────────────────────────────────────────────


class TestSecurityHeaders:
    """Tests for SecurityHeadersMiddleware ASGI behavior."""

    @staticmethod
    def _make_scope(*, scheme: str = "http", state: dict | None = None) -> dict:
        scope = {"type": "http", "scheme": scheme, "headers": []}
        if state is not None:
            scope["state"] = state
        return scope

    @staticmethod
    async def _echo_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def test_standard_headers_injected(self):
        """All 7 OWASP security headers are present on normal response."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        mw = SecurityHeadersMiddleware(self._echo_app)
        await mw(self._make_scope(), AsyncMock(), capture)

        start = sent[0]
        header_names = {h[0].lower() for h in start["headers"]}
        for name, _ in _SECURITY_HEADERS:
            assert name in header_names, f"Missing header: {name!r}"

    async def test_no_hsts_without_require_tls(self):
        """HSTS is NOT injected when require_tls=False."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        mw = SecurityHeadersMiddleware(self._echo_app, require_tls=False)
        await mw(self._make_scope(), AsyncMock(), capture)

        header_names = {h[0].lower() for h in sent[0]["headers"]}
        assert b"strict-transport-security" not in header_names

    async def test_hsts_with_require_tls(self):
        """HSTS is present on HTTPS when require_tls=True."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        mw = SecurityHeadersMiddleware(self._echo_app, require_tls=True)
        scope = self._make_scope(scheme="https")
        await mw(scope, AsyncMock(), capture)

        header_dict = dict(sent[0]["headers"])
        assert _HSTS_HEADER[0] in header_dict
        assert header_dict[_HSTS_HEADER[0]] == _HSTS_HEADER[1]

    async def test_tls_enforcement_421(self):
        """HTTP request → 421 when require_tls=True, app never called."""
        sent: list[dict] = []
        app_called = False

        async def capture(message):
            sent.append(message)

        async def spy_app(scope, receive, send):
            nonlocal app_called
            app_called = True

        mw = SecurityHeadersMiddleware(spy_app, require_tls=True)
        await mw(self._make_scope(scheme="http"), AsyncMock(), capture)

        assert not app_called
        assert sent[0]["status"] == 421
        body = json.loads(sent[1]["body"])
        assert body["type"] == "https://www.retio.ai/pagemap/errors/tls-required"
        assert body["status"] == 421

    async def test_tls_enforcement_proxy_https_passes(self):
        """forwarded_proto=https → 200 (not 421) with require_tls=True."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        mw = SecurityHeadersMiddleware(self._echo_app, require_tls=True)
        scope = self._make_scope(
            scheme="http",
            state={"forwarded_proto": "https"},
        )
        await mw(scope, AsyncMock(), capture)

        assert sent[0]["status"] == 200

    async def test_no_header_duplication(self):
        """App's existing headers are preserved, not duplicated."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        async def app_with_headers(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"x-content-type-options", b"custom-value")],
                }
            )

        mw = SecurityHeadersMiddleware(app_with_headers)
        await mw(self._make_scope(), AsyncMock(), capture)

        xcto_values = [h[1] for h in sent[0]["headers"] if h[0] == b"x-content-type-options"]
        assert len(xcto_values) == 1
        assert xcto_values[0] == b"custom-value"

    async def test_lifespan_passthrough(self):
        """Non-HTTP scope passes through without modification."""
        app_called = False

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True

        mw = SecurityHeadersMiddleware(app)
        await mw({"type": "lifespan"}, AsyncMock(), AsyncMock())

        assert app_called

    async def test_headers_injected_only_once(self):
        """Second http.response.start is untouched (SSE safety)."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        async def sse_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.start", "status": 200, "headers": []})

        mw = SecurityHeadersMiddleware(sse_app)
        await mw(self._make_scope(), AsyncMock(), capture)

        first_names = {h[0].lower() for h in sent[0]["headers"]}
        second_names = {h[0].lower() for h in sent[1]["headers"]}
        assert b"x-content-type-options" in first_names
        assert b"x-content-type-options" not in second_names

    async def test_421_response_includes_security_headers(self):
        """Even the 421 error response carries all security headers."""
        sent: list[dict] = []

        async def capture(message):
            sent.append(message)

        mw = SecurityHeadersMiddleware(self._echo_app, require_tls=True)
        await mw(self._make_scope(scheme="http"), AsyncMock(), capture)

        header_names = {h[0].lower() for h in sent[0]["headers"]}
        for name, _ in _SECURITY_HEADERS:
            assert name in header_names, f"421 missing header: {name!r}"


# ── TestSecurityHeadersIntegration ───────────────────────────────────


class TestSecurityHeadersIntegration:
    """Integration test through full ASGI stack with httpx."""

    @pytest.fixture
    def app(self):
        import pagemap.server as srv

        return srv.mcp.streamable_http_app()

    @pytest.fixture
    def secured_app(self, app):
        return SecurityHeadersMiddleware(app)

    @pytest.fixture
    def client(self, secured_app):
        transport = httpx.ASGITransport(app=secured_app)
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def test_health_has_security_headers(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        for name, value in _SECURITY_HEADERS:
            header_name = name.decode("latin-1")
            assert header_name in resp.headers, f"Missing {header_name}"
            assert resp.headers[header_name] == value.decode("latin-1")
