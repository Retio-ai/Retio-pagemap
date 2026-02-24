# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for _acquire_context() — STDIO and HTTP path selection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

import pagemap.server as srv
from pagemap.context import RequestContext


@pytest.fixture(autouse=True)
def _clear_contextvars():
    """Clear structlog contextvars before/after each test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# STDIO mode tests
# ---------------------------------------------------------------------------


class TestStdioMode:
    """_acquire_context in STDIO mode (default _transport_mode="stdio")."""

    async def test_returns_request_context_and_lock(self):
        ctx, lock = await srv._acquire_context(mcp_ctx=None)
        assert isinstance(ctx, RequestContext)
        assert isinstance(lock, asyncio.Lock)

    async def test_uses_state_tool_lock(self):
        ctx, lock = await srv._acquire_context(mcp_ctx=None)
        assert lock is srv._state.tool_lock

    async def test_context_has_request_id(self):
        ctx, _ = await srv._acquire_context(mcp_ctx=None)
        assert ctx.request_id
        assert len(ctx.request_id) == 12

    async def test_context_has_state_session_id(self):
        ctx, _ = await srv._acquire_context(mcp_ctx=None)
        assert ctx.session_id == srv._state.session_id

    async def test_mcp_ctx_ignored_in_stdio_mode(self):
        """Even with mcp_ctx provided, STDIO mode uses _create_stdio_context."""
        mock_ctx = MagicMock()
        ctx, lock = await srv._acquire_context(mcp_ctx=mock_ctx)
        assert lock is srv._state.tool_lock
        assert ctx.session_id == srv._state.session_id

    async def test_binds_structlog_contextvars(self):
        ctx, _ = await srv._acquire_context(mcp_ctx=None)
        bound = structlog.contextvars.get_contextvars()
        assert bound["request_id"] == ctx.request_id
        assert bound["session_id"] == ctx.session_id

    async def test_each_call_gets_unique_request_id(self):
        ctx1, _ = await srv._acquire_context(mcp_ctx=None)
        ctx2, _ = await srv._acquire_context(mcp_ctx=None)
        assert ctx1.request_id != ctx2.request_id


# ---------------------------------------------------------------------------
# HTTP mode tests
# ---------------------------------------------------------------------------


class TestHttpMode:
    """_acquire_context in HTTP mode with mock session_manager."""

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock HttpSessionManager."""
        mgr = MagicMock()
        mgr.get_context = AsyncMock(
            return_value=RequestContext(
                request_id="http-req-123",
                session_id="http-sess-abc",
                client_id="http-sess-abc",
                cache=srv._state.cache,
                template_cache=srv._state.template_cache,
                get_session=AsyncMock(),
            )
        )
        mgr.get_tool_lock = MagicMock(return_value=asyncio.Lock())
        return mgr

    @pytest.fixture
    def _http_mode(self, mock_session_manager):
        """Switch to HTTP mode for the test."""
        old_transport = srv._transport_mode
        old_mgr = srv._session_manager
        srv._transport_mode = "http"
        srv._session_manager = mock_session_manager
        yield mock_session_manager
        srv._transport_mode = old_transport
        srv._session_manager = old_mgr

    async def test_http_mode_uses_session_manager(self, _http_mode, mock_session_manager):
        mock_mcp_ctx = MagicMock()
        mock_mcp_ctx.request_context.request.headers.get.return_value = "sess-id-123"

        ctx, lock = await srv._acquire_context(mcp_ctx=mock_mcp_ctx)
        mock_session_manager.get_context.assert_awaited_once_with("sess-id-123")
        mock_session_manager.get_tool_lock.assert_called_once_with("sess-id-123")

    async def test_http_mode_returns_per_session_lock(self, _http_mode, mock_session_manager):
        mock_mcp_ctx = MagicMock()
        mock_mcp_ctx.request_context.request.headers.get.return_value = "sess-id-456"

        ctx, lock = await srv._acquire_context(mcp_ctx=mock_mcp_ctx)
        assert lock is mock_session_manager.get_tool_lock.return_value

    async def test_http_mode_ephemeral_id_when_no_header(self, _http_mode, mock_session_manager):
        """When mcp_ctx has no session header, an ephemeral ID is generated."""
        mock_mcp_ctx = MagicMock()
        mock_mcp_ctx.request_context.request.headers.get.return_value = None

        ctx, lock = await srv._acquire_context(mcp_ctx=mock_mcp_ctx)
        # Should have called get_context with some auto-generated ID
        mock_session_manager.get_context.assert_awaited_once()
        call_args = mock_session_manager.get_context.call_args[0]
        assert len(call_args[0]) == 16  # uuid hex[:16]

    async def test_http_mode_ephemeral_id_when_no_mcp_ctx(self, _http_mode, mock_session_manager):
        """mcp_ctx=None in HTTP mode → ephemeral session."""
        ctx, lock = await srv._acquire_context(mcp_ctx=None)
        mock_session_manager.get_context.assert_awaited_once()
        call_args = mock_session_manager.get_context.call_args[0]
        assert len(call_args[0]) == 16

    async def test_two_sessions_get_different_locks(self, _http_mode, mock_session_manager):
        """Different session IDs should get different lock objects."""
        lock1 = asyncio.Lock()
        lock2 = asyncio.Lock()
        mock_session_manager.get_tool_lock.side_effect = [lock1, lock2]

        mcp_ctx1 = MagicMock()
        mcp_ctx1.request_context.request.headers.get.return_value = "sess-a"
        mcp_ctx2 = MagicMock()
        mcp_ctx2.request_context.request.headers.get.return_value = "sess-b"

        _, l1 = await srv._acquire_context(mcp_ctx=mcp_ctx1)
        _, l2 = await srv._acquire_context(mcp_ctx=mcp_ctx2)
        assert l1 is not l2

    async def test_http_mode_binds_structlog_contextvars(self, _http_mode):
        mock_mcp_ctx = MagicMock()
        mock_mcp_ctx.request_context.request.headers.get.return_value = "sess-x"

        ctx, _ = await srv._acquire_context(mcp_ctx=mock_mcp_ctx)
        bound = structlog.contextvars.get_contextvars()
        assert bound["request_id"] == ctx.request_id
        assert bound["session_id"] == ctx.session_id

    async def test_http_mode_no_request_attr(self, _http_mode, mock_session_manager):
        """mcp_ctx.request_context.request is None → ephemeral."""
        mock_mcp_ctx = MagicMock()
        mock_mcp_ctx.request_context.request = None

        ctx, _ = await srv._acquire_context(mcp_ctx=mock_mcp_ctx)
        call_args = mock_session_manager.get_context.call_args[0]
        assert len(call_args[0]) == 16

    async def test_http_mode_request_without_headers(self, _http_mode, mock_session_manager):
        """mcp_ctx.request_context.request has no headers → ephemeral."""
        mock_mcp_ctx = MagicMock()
        mock_mcp_ctx.request_context.request = object()  # no headers attr

        ctx, _ = await srv._acquire_context(mcp_ctx=mock_mcp_ctx)
        call_args = mock_session_manager.get_context.call_args[0]
        assert len(call_args[0]) == 16


# ---------------------------------------------------------------------------
# Fallback: _transport_mode="http" but _session_manager=None
# ---------------------------------------------------------------------------


class TestHttpModeNoManager:
    """HTTP mode but session_manager not yet initialized."""

    async def test_falls_back_to_stdio(self):
        old = srv._transport_mode
        srv._transport_mode = "http"
        old_mgr = srv._session_manager
        srv._session_manager = None
        try:
            ctx, lock = await srv._acquire_context(mcp_ctx=None)
            assert lock is srv._state.tool_lock
            assert ctx.session_id == srv._state.session_id
        finally:
            srv._transport_mode = old
            srv._session_manager = old_mgr
