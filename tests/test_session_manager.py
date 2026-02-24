# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SessionManager — STDIO and HTTP implementations.

All tests mock browser infrastructure to avoid launching real browsers.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import pagemap.server as srv
from pagemap.cache import PageMapCache
from pagemap.context import RequestContext
from pagemap.session_manager import (
    STDIO_SESSION_ID,
    HttpSessionManager,
    SessionManagerProtocol,
    SessionNotFoundError,
    StdioSessionManager,
)
from pagemap.template_cache import InMemoryTemplateCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server_state():
    """Create a minimal ServerState-like object for STDIO tests."""
    state = srv.ServerState()
    return state


def _mock_pool():
    """Create a mock BrowserPool."""
    pool = AsyncMock()
    mock_sess = AsyncMock()
    mock_sess.is_alive = AsyncMock(return_value=True)
    mock_sess.install_ssrf_route_guard = AsyncMock()
    mock_sess.stop = AsyncMock()
    pool.acquire = AsyncMock(return_value=mock_sess)
    pool.release = AsyncMock()
    return pool, mock_sess


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    """Both managers satisfy SessionManagerProtocol."""

    def test_stdio_is_protocol(self):
        mgr = StdioSessionManager(_make_server_state())
        assert isinstance(mgr, SessionManagerProtocol)

    def test_http_is_protocol(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        assert isinstance(mgr, SessionManagerProtocol)


# ---------------------------------------------------------------------------
# STDIO SessionManager
# ---------------------------------------------------------------------------


class TestStdioGetContext:
    """StdioSessionManager.get_context()"""

    async def test_returns_request_context(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        ctx = await mgr.get_context(STDIO_SESSION_ID)
        assert isinstance(ctx, RequestContext)
        assert ctx.session_id == state.session_id
        assert ctx.cache is state.cache
        assert ctx.template_cache is state.template_cache
        assert ctx.client_id == ""

    async def test_get_session_is_module_wrapper(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        ctx = await mgr.get_context(STDIO_SESSION_ID)
        assert ctx.get_session is srv._get_session


class TestStdioGetToolLock:
    """StdioSessionManager.get_tool_lock()"""

    def test_returns_state_tool_lock(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        assert mgr.get_tool_lock() is state.tool_lock


class TestStdioRejectsUnknown:
    """StdioSessionManager rejects non-__stdio__ session_ids."""

    async def test_rejects_other_session(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        with pytest.raises(KeyError, match="StdioSessionManager only supports"):
            await mgr.get_context("other-session")


class TestStdioCannotRemove:
    """remove_session is a no-op for STDIO."""

    async def test_remove_is_noop(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        await mgr.remove_session(STDIO_SESSION_ID)
        # Should not raise


class TestStdioActiveSessions:
    """STDIO always has 1 active session."""

    def test_always_one(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        assert mgr.active_sessions == 1


class TestStdioShutdown:
    """shutdown() is a no-op."""

    async def test_shutdown_noop(self):
        state = _make_server_state()
        mgr = StdioSessionManager(state)
        await mgr.shutdown()  # Should not raise


# ---------------------------------------------------------------------------
# HTTP SessionManager
# ---------------------------------------------------------------------------


class TestHttpCreatesSession:
    """HttpSessionManager auto-creates session entries."""

    async def test_auto_creates(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("http-sess-1")

        assert isinstance(ctx, RequestContext)
        assert ctx.session_id == "http-sess-1"
        assert ctx.client_id == "http-sess-1"
        assert mgr.active_sessions == 1


class TestHttpPerSessionCache:
    """Each HTTP session gets its own PageMapCache."""

    async def test_independent_caches(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx1 = await mgr.get_context("sess-1")
            ctx2 = await mgr.get_context("sess-2")

        assert ctx1.cache is not ctx2.cache
        assert isinstance(ctx1.cache, PageMapCache)
        assert isinstance(ctx2.cache, PageMapCache)


class TestHttpPerSessionLock:
    """Each HTTP session gets its own tool_lock."""

    async def test_independent_locks(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("sess-1")
            await mgr.get_context("sess-2")

        lock1 = mgr.get_tool_lock("sess-1")
        lock2 = mgr.get_tool_lock("sess-2")
        assert lock1 is not lock2
        assert isinstance(lock1, asyncio.Lock)


class TestHttpSharedTemplateCache:
    """All HTTP sessions share the same InMemoryTemplateCache."""

    async def test_shared_template_cache(self):
        pool, mock_sess = _mock_pool()
        tc = InMemoryTemplateCache()
        mgr = HttpSessionManager(pool, template_cache=tc)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx1 = await mgr.get_context("sess-1")
            ctx2 = await mgr.get_context("sess-2")

        assert ctx1.template_cache is tc
        assert ctx2.template_cache is tc


class TestHttpSessionNotFound:
    """get_tool_lock raises SessionNotFoundError for unknown sessions."""

    def test_unknown_session_raises(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        with pytest.raises(SessionNotFoundError):
            mgr.get_tool_lock("nonexistent")


class TestHttpRemoveSession:
    """remove_session cleans up session entry via pool.release()."""

    async def test_remove(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("to-remove")
            # Trigger browser session creation
            await ctx.get_session()

        await mgr.remove_session("to-remove")
        assert mgr.active_sessions == 0
        pool.release.assert_called_with("to-remove")


class TestHttpBrowserRecovery:
    """Dead browser session → released via pool, then re-acquired."""

    async def test_recovery(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)

        # First call: session alive
        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("recover-sess")
            sess1 = await ctx.get_session()
            assert sess1 is mock_sess

        # Mark session as dead
        mock_sess.is_alive = AsyncMock(return_value=False)

        # Second call: should recover
        new_sess = AsyncMock()
        new_sess.is_alive = AsyncMock(return_value=True)
        new_sess.install_ssrf_route_guard = AsyncMock()
        pool.acquire = AsyncMock(return_value=new_sess)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx2 = await mgr.get_context("recover-sess")
            sess2 = await ctx2.get_session()
            assert sess2 is new_sess

        # Fix 4: pool.release must be called to return the old semaphore slot
        pool.release.assert_called_with("recover-sess")


class TestHttpShutdown:
    """shutdown() cleans up all HTTP sessions."""

    async def test_shutdown_all(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("s1")
            await mgr.get_context("s2")

        assert mgr.active_sessions == 2
        await mgr.shutdown()
        assert mgr.active_sessions == 0
