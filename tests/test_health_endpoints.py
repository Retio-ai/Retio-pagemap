# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for /health, /ready, /livez, /readyz, /startupz custom route endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

import pagemap.server as srv


@pytest.fixture
def app():
    """Create the Starlette ASGI app from FastMCP."""
    return srv.mcp.streamable_http_app()


@pytest.fixture
def client(app):
    """Create an httpx async client for ASGI testing."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_health_returns_status_ok(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"

    async def test_health_includes_transport(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "transport" in data

    async def test_health_transport_reflects_mode(self, client):
        """Default is stdio."""
        resp = await client.get("/health")
        data = resp.json()
        assert data["transport"] == "stdio"

    async def test_health_transport_http_mode(self, client):
        old = srv._transport_mode
        srv._transport_mode = "http"
        try:
            resp = await client.get("/health")
            data = resp.json()
            assert data["transport"] == "http"
        finally:
            srv._transport_mode = old


class TestReadyEndpoint:
    """Tests for /ready endpoint."""

    async def test_ready_stdio_mode(self, client):
        resp = await client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["transport"] == "stdio"

    async def test_ready_http_mode_no_pool(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"
        srv._session_manager = MagicMock(spec=[])  # no _pool attr
        try:
            resp = await client.get("/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            assert data["transport"] == "http"
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_ready_http_mode_pool_connected(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"

        mock_pool = MagicMock()
        mock_pool.health.return_value = MagicMock(browser_connected=True, active=2, max_contexts=5)
        mock_mgr = MagicMock()
        mock_mgr._pool = mock_pool
        srv._session_manager = mock_mgr

        try:
            resp = await client.get("/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            assert data["transport"] == "http"
            assert data["pool"]["active"] == 2
            assert data["pool"]["max_contexts"] == 5
            assert data["pool"]["browser_connected"] is True
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_ready_http_mode_pool_disconnected(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"

        mock_pool = MagicMock()
        mock_pool.health.return_value = MagicMock(browser_connected=False, active=0, max_contexts=5)
        mock_mgr = MagicMock()
        mock_mgr._pool = mock_pool
        srv._session_manager = mock_mgr

        try:
            resp = await client.get("/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["pool"]["browser_connected"] is False
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_ready_http_mode_no_session_manager(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"
        srv._session_manager = None
        try:
            resp = await client.get("/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            assert data["transport"] == "stdio"  # fallback
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr


class TestLivezEndpoint:
    """Tests for /livez K8s liveness probe."""

    async def test_livez_returns_200(self, client):
        resp = await client.get("/livez")
        assert resp.status_code == 200

    async def test_livez_returns_status_ok(self, client):
        resp = await client.get("/livez")
        data = resp.json()
        assert data["status"] == "ok"

    async def test_livez_includes_transport(self, client):
        resp = await client.get("/livez")
        data = resp.json()
        assert "transport" in data


class TestReadyzEndpoint:
    """Tests for /readyz K8s readiness probe (drain-aware)."""

    async def test_readyz_returns_200_normal(self, client):
        resp = await client.get("/readyz")
        assert resp.status_code == 200

    async def test_readyz_returns_503_when_draining(self, client):
        srv._draining = True
        try:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "draining"
        finally:
            srv._draining = False

    async def test_readyz_http_mode_ready(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"

        mock_pool = MagicMock()
        mock_pool.health.return_value = MagicMock(browser_connected=True, active=1, max_contexts=5)
        mock_mgr = MagicMock()
        mock_mgr._pool = mock_pool
        srv._session_manager = mock_mgr
        try:
            resp = await client.get("/readyz")
            assert resp.status_code == 200
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_readyz_drain_overrides_ready(self, client):
        """Drain flag takes priority over pool health."""
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"
        srv._draining = True

        mock_pool = MagicMock()
        mock_pool.health.return_value = MagicMock(browser_connected=True, active=1, max_contexts=5)
        mock_mgr = MagicMock()
        mock_mgr._pool = mock_pool
        srv._session_manager = mock_mgr
        try:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            assert resp.json()["status"] == "draining"
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr
            srv._draining = False


class TestStartupzEndpoint:
    """Tests for /startupz K8s startup probe."""

    async def test_startupz_not_started_stdio(self, client):
        """STDIO mode → not_started."""
        resp = await client.get("/startupz")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_started"

    async def test_startupz_started_http_no_pool(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"
        srv._session_manager = MagicMock(spec=[])  # no _pool
        try:
            resp = await client.get("/startupz")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "started"
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_startupz_started_pool_connected(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"

        mock_pool = MagicMock()
        mock_pool.health.return_value = MagicMock(browser_connected=True)
        mock_mgr = MagicMock()
        mock_mgr._pool = mock_pool
        srv._session_manager = mock_mgr
        try:
            resp = await client.get("/startupz")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "started"
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_startupz_starting_pool_disconnected(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"

        mock_pool = MagicMock()
        mock_pool.health.return_value = MagicMock(browser_connected=False)
        mock_mgr = MagicMock()
        mock_mgr._pool = mock_pool
        srv._session_manager = mock_mgr
        try:
            resp = await client.get("/startupz")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "starting"
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr

    async def test_startupz_no_session_manager(self, client):
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"
        srv._session_manager = None
        try:
            resp = await client.get("/startupz")
            assert resp.status_code == 503
        finally:
            srv._transport_mode = old_transport
            srv._session_manager = old_mgr
