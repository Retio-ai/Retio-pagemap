# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for session lifecycle — creation, removal, isolation, TTL expiry.

Stream D: Session Isolation + Browser Recycling + Resource Quotas.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from pagemap.cache import PageMapCache
from pagemap.session_manager import HttpSessionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool():
    """Create a mock BrowserPool that returns a healthy session."""
    pool = AsyncMock()
    mock_sess = AsyncMock()
    mock_sess.is_alive = AsyncMock(return_value=True)
    mock_sess.install_ssrf_route_guard = AsyncMock()
    mock_sess.stop = AsyncMock()
    mock_sess.tab_count = 1
    pool.acquire = AsyncMock(return_value=mock_sess)
    pool.release = AsyncMock()
    return pool, mock_sess


# ---------------------------------------------------------------------------
# D2 field initialization
# ---------------------------------------------------------------------------


class TestSessionCreation:
    """SessionEntry is auto-created with correct defaults."""

    async def test_auto_creates_entry(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("new-sess")
        assert mgr.active_sessions == 1

    async def test_created_at_is_monotonic(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        before = time.monotonic()
        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("ts-sess")
        after = time.monotonic()
        entry = mgr._sessions["ts-sess"]
        assert before <= entry.created_at <= after

    async def test_navigation_count_starts_zero(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("nav-sess")
        entry = mgr._sessions["nav-sess"]
        assert entry.navigation_count == 0

    async def test_browser_acquired_at_starts_zero(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("acq-sess")
        entry = mgr._sessions["acq-sess"]
        assert entry.browser_acquired_at == 0.0


# ---------------------------------------------------------------------------
# D1 cookie/storage destruction
# ---------------------------------------------------------------------------


class TestSessionRemoval:
    """remove_session releases pool and invalidates cache."""

    async def test_pool_release_called(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("rm-sess")
            await ctx.get_session()
        await mgr.remove_session("rm-sess")
        pool.release.assert_called_with("rm-sess")

    async def test_cache_invalidated(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("cache-sess")
            await ctx.get_session()
        entry = mgr._sessions["cache-sess"]
        cache = entry.cache
        with patch.object(cache, "invalidate_all", wraps=cache.invalidate_all) as spy:
            await mgr.remove_session("cache-sess")
            spy.assert_called_once()

    async def test_nonexistent_is_noop(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        await mgr.remove_session("ghost")  # Should not raise

    async def test_session_stop_chain_via_pool(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("stop-sess")
            await ctx.get_session()
        await mgr.remove_session("stop-sess")
        pool.release.assert_called_once_with("stop-sess")
        assert mgr.active_sessions == 0


# ---------------------------------------------------------------------------
# D1 cross-session isolation
# ---------------------------------------------------------------------------


class TestCrossSessionIsolation:
    """Different sessions get independent caches, locks, and browser sessions."""

    async def test_different_caches(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            ctx1 = await mgr.get_context("iso-1")
            ctx2 = await mgr.get_context("iso-2")
        assert ctx1.cache is not ctx2.cache
        assert isinstance(ctx1.cache, PageMapCache)

    async def test_different_tool_locks(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)
        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("iso-1")
            await mgr.get_context("iso-2")
        lock1 = mgr.get_tool_lock("iso-1")
        lock2 = mgr.get_tool_lock("iso-2")
        assert lock1 is not lock2

    async def test_different_browser_sessions(self):
        """Each session acquires its own BrowserSession from pool."""
        sessions = []

        async def _make(sid):
            s = AsyncMock()
            s.is_alive = AsyncMock(return_value=True)
            s.install_ssrf_route_guard = AsyncMock()
            s.tab_count = 1
            sessions.append(s)
            return s

        pool = AsyncMock()
        pool.acquire = AsyncMock(side_effect=_make)
        pool.release = AsyncMock()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx1 = await mgr.get_context("iso-1")
            ctx2 = await mgr.get_context("iso-2")
            s1 = await ctx1.get_session()
            s2 = await ctx2.get_session()

        assert s1 is not s2
        assert len(sessions) == 2


# ---------------------------------------------------------------------------
# D3 TTL enforcement
# ---------------------------------------------------------------------------


class TestExpiredSessionAutoRemoval:
    """Expired sessions are cleaned up and replaced transparently."""

    async def test_expired_session_creates_fresh_entry(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool, session_ttl=10.0)

        # Create initial session
        with patch("pagemap.server._validate_url", return_value=None):
            ctx1 = await mgr.get_context("ttl-sess")
            await ctx1.get_session()
        old_entry = mgr._sessions["ttl-sess"]
        old_cache = old_entry.cache

        # Simulate time passing beyond TTL
        old_entry.created_at = time.monotonic() - 20.0

        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("ttl-sess")

        new_entry = mgr._sessions["ttl-sess"]
        assert new_entry.cache is not old_cache
        assert new_entry.navigation_count == 0

    async def test_pool_release_called_for_old_browser(self):
        pool, mock_sess = _mock_pool()
        mgr = HttpSessionManager(pool, session_ttl=10.0)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("ttl-rel")
            await ctx.get_session()
        entry = mgr._sessions["ttl-rel"]
        entry.created_at = time.monotonic() - 20.0

        with patch("pagemap.server._validate_url", return_value=None):
            await mgr.get_context("ttl-rel")

        pool.release.assert_called_with("ttl-rel")


# ---------------------------------------------------------------------------
# Concurrent session creation
# ---------------------------------------------------------------------------


class TestConcurrentSessionCreation:
    """Concurrent get_context for same ID yields a single entry."""

    async def test_single_entry_under_concurrency(self):
        pool, _ = _mock_pool()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            results = await asyncio.gather(
                mgr.get_context("race"),
                mgr.get_context("race"),
            )

        # Only one entry in _sessions
        assert len(mgr._sessions) == 1
        assert "race" in mgr._sessions
        # Both contexts share the same cache
        assert results[0].cache is results[1].cache
