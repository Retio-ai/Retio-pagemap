# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for browser recycling, tab quotas, and navigation counters.

Stream D: Session Isolation + Browser Recycling + Resource Quotas.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from pagemap.errors import ResourceExhaustionError
from pagemap.session_manager import HttpSessionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool_with_recycle():
    """Create a mock BrowserPool that returns fresh sessions on each acquire.

    Returns (pool, sessions_list) — sessions_list grows with each acquire.
    """
    pool = AsyncMock()
    sessions: list[AsyncMock] = []

    async def _make(sid):
        s = AsyncMock()
        s.is_alive = AsyncMock(return_value=True)
        s.install_ssrf_route_guard = AsyncMock()
        s.tab_count = 1
        sessions.append(s)
        return s

    pool.acquire = AsyncMock(side_effect=_make)
    pool.release = AsyncMock()
    return pool, sessions


# ---------------------------------------------------------------------------
# D2 navigation-count recycling
# ---------------------------------------------------------------------------


class TestRecycleAfterMaxNavigations:
    """Browser is recycled after MAX_NAVIGATIONS calls."""

    async def test_recycle_triggers(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_NAVIGATIONS", 5)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("nav-recycle")
            # Calls 1-5: same session
            for _ in range(5):
                await ctx.get_session()

            assert len(sessions) == 1

            # Call 6: triggers recycle (nav_count=5 >= MAX_NAVIGATIONS=5)
            # Need fresh context to get updated entry ref
            ctx2 = await mgr.get_context("nav-recycle")
            await ctx2.get_session()

        # Second session acquired from pool
        assert len(sessions) == 2
        pool.release.assert_called()

    async def test_nav_count_resets_after_recycle(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_NAVIGATIONS", 3)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("nav-reset")
            for _ in range(3):
                await ctx.get_session()

            # Trigger recycle
            ctx2 = await mgr.get_context("nav-reset")
            await ctx2.get_session()

        entry = mgr._sessions["nav-reset"]
        # After recycle: nav_count reset to 0, then incremented to 1
        assert entry.navigation_count == 1


# ---------------------------------------------------------------------------
# D2 age-based recycling
# ---------------------------------------------------------------------------


class TestRecycleAfterMaxSessionAge:
    """Browser is recycled when browser_acquired_at exceeds MAX_SESSION_AGE."""

    async def test_age_triggers_recycle(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_SESSION_AGE", 60.0)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        # Deterministic clock: avoids flakiness from real time + xdist patching
        _now = 1000.0
        monkeypatch.setattr(time, "monotonic", lambda: _now)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("age-recycle")
            await ctx.get_session()

        assert len(sessions) == 1

        # Advance clock past MAX_SESSION_AGE (60s)
        _now = 1120.0  # +120s
        monkeypatch.setattr(time, "monotonic", lambda: _now)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx2 = await mgr.get_context("age-recycle")
            await ctx2.get_session()

        assert len(sessions) == 2

    async def test_no_recycle_under_limit(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_SESSION_AGE", 600.0)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        # Deterministic clock: only 10s elapsed, well under 600s
        _now = 1000.0
        monkeypatch.setattr(time, "monotonic", lambda: _now)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("age-ok")
            await ctx.get_session()

            _now = 1010.0  # +10s, under limit
            monkeypatch.setattr(time, "monotonic", lambda: _now)

            ctx2 = await mgr.get_context("age-ok")
            await ctx2.get_session()

        # Same session reused
        assert len(sessions) == 1


# ---------------------------------------------------------------------------
# D2 cache invalidation on recycle
# ---------------------------------------------------------------------------


class TestCacheInvalidatedOnRecycle:
    """Cache is invalidated when browser is recycled."""

    async def test_invalidate_called(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_NAVIGATIONS", 2)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("cache-inv")
            await ctx.get_session()
            await ctx.get_session()

        entry = mgr._sessions["cache-inv"]
        with patch.object(entry.cache, "invalidate_all", wraps=entry.cache.invalidate_all) as spy:
            with patch("pagemap.server._validate_url", return_value=None):
                ctx2 = await mgr.get_context("cache-inv")
                await ctx2.get_session()
            spy.assert_called_once()


# ---------------------------------------------------------------------------
# D2 telemetry on recycle
# ---------------------------------------------------------------------------


class TestTelemetryOnRecycle:
    """BROWSER_DEAD telemetry emitted on recycle."""

    async def test_emit_on_recycle(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_NAVIGATIONS", 2)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("telem-sess")
            await ctx.get_session()
            await ctx.get_session()

        from pagemap.telemetry import events

        # Lazy import inside _get_session_for_entry resolves to
        # pagemap.telemetry.emit — patch at source module.
        with (
            patch("pagemap.telemetry.emit") as mock_telem_emit,
            patch("pagemap.server._validate_url", return_value=None),
        ):
            ctx2 = await mgr.get_context("telem-sess")
            await ctx2.get_session()

        mock_telem_emit.assert_called_once()
        call_args = mock_telem_emit.call_args
        assert call_args[0][0] == events.BROWSER_DEAD
        payload = call_args[0][1]
        assert "recycled" in payload["error"]


# ---------------------------------------------------------------------------
# D3 tab limit enforcement
# ---------------------------------------------------------------------------


class TestTabLimitEnforcement:
    """ResourceExhaustionError raised when tab limit is reached."""

    async def test_exceeds_tab_limit(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_TABS_PER_SESSION", 5)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("tab-limit")
            await ctx.get_session()

        # Set tab_count to the limit
        entry = mgr._sessions["tab-limit"]
        entry.browser_session.tab_count = 5

        with patch("pagemap.server._validate_url", return_value=None):
            ctx2 = await mgr.get_context("tab-limit")
            with pytest.raises(ResourceExhaustionError, match="Tab limit exceeded"):
                await ctx2.get_session()

    async def test_under_tab_limit_succeeds(self, monkeypatch):
        monkeypatch.setattr("pagemap.session_manager.MAX_TABS_PER_SESSION", 5)
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("tab-ok")
            await ctx.get_session()

        # Set tab_count under the limit
        entry = mgr._sessions["tab-ok"]
        entry.browser_session.tab_count = 3

        with patch("pagemap.server._validate_url", return_value=None):
            ctx2 = await mgr.get_context("tab-ok")
            sess2 = await ctx2.get_session()
            assert sess2 is not None  # Should succeed


# ---------------------------------------------------------------------------
# D2 navigation count accuracy
# ---------------------------------------------------------------------------


class TestNavigationCountIncrement:
    """Navigation count increments with each get_session() call."""

    async def test_increments_per_call(self):
        pool, sessions = _mock_pool_with_recycle()
        mgr = HttpSessionManager(pool)

        with patch("pagemap.server._validate_url", return_value=None):
            ctx = await mgr.get_context("nav-count")
            await ctx.get_session()
            await ctx.get_session()
            await ctx.get_session()

        entry = mgr._sessions["nav-count"]
        assert entry.navigation_count == 3
