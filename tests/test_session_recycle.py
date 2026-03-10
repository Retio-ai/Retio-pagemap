# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for STDIO session recycling (D2-style proactive recycle)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import pagemap.server as srv


@pytest.fixture()
def _mock_browser(monkeypatch):
    """Patch BrowserSession so get_session() creates a new fake session each time."""
    sessions = []

    def _make_session(*args, **kwargs):
        s = AsyncMock()
        s.is_alive = AsyncMock(return_value=True)
        s.start = AsyncMock()
        s.stop = AsyncMock()
        s.install_ssrf_route_guard = AsyncMock()
        sessions.append(s)
        return s

    monkeypatch.setattr("pagemap.server.BrowserSession", _make_session)
    return sessions


@pytest.mark.allow_real_get_session
class TestStdioRecycleOnNavCount:
    @pytest.mark.asyncio
    async def test_recycles_after_nav_threshold(self, _mock_browser, monkeypatch):
        """Session is recycled after _max_stdio_navigations calls."""
        monkeypatch.setattr(srv, "_max_stdio_navigations", 3)
        state = srv._state
        state.session = None

        # 3 calls → creates session, nav_count reaches 3
        for _ in range(3):
            await state.get_session()

        first_session = state.session
        assert state._navigation_count == 3
        assert len(_mock_browser) == 1  # one session created so far

        # 4th call triggers recycle (nav_count=3 >= threshold=3)
        await state.get_session()
        first_session.stop.assert_awaited_once()  # old session stopped
        assert len(_mock_browser) == 2  # new session created
        assert state.session is _mock_browser[1]
        assert state._navigation_count == 1  # reset after recycle + 1 for new call


@pytest.mark.allow_real_get_session
class TestStdioRecycleOnAge:
    @pytest.mark.asyncio
    async def test_recycles_after_session_age(self, _mock_browser, monkeypatch):
        """Session is recycled when age exceeds _max_stdio_session_age."""
        monkeypatch.setattr(srv, "_max_stdio_session_age", 60.0)
        monkeypatch.setattr(srv, "_max_stdio_navigations", 9999)  # disable nav-count trigger
        state = srv._state
        state.session = None

        await state.get_session()
        first_session = state.session
        assert len(_mock_browser) == 1

        # Simulate aging: set started_at far in the past
        state._session_started_at = state._session_started_at - 120.0

        await state.get_session()
        first_session.stop.assert_awaited_once()
        assert len(_mock_browser) == 2
        assert state.session is _mock_browser[1]


@pytest.mark.allow_real_get_session
class TestStdioRecycleSkippedWithMultiTab:
    @pytest.mark.asyncio
    async def test_skip_recycle_when_multi_tab_active(self, _mock_browser, monkeypatch):
        """Recycle is skipped when multi_tab has open tabs."""
        monkeypatch.setattr(srv, "_max_stdio_navigations", 2)
        state = srv._state
        state.session = None

        # Set up multi_tab mock with is_multi_tab=True
        mock_multi_tab = MagicMock()
        mock_multi_tab.is_multi_tab = True
        state.multi_tab = mock_multi_tab

        # 2 calls → nav_count reaches 2 (threshold)
        await state.get_session()
        await state.get_session()

        # 3rd call would normally trigger recycle, but multi_tab blocks it
        await state.get_session()
        assert len(_mock_browser) == 1  # still only one session — no recycle


@pytest.mark.allow_real_get_session
class TestStdioRecycleResetsCounter:
    @pytest.mark.asyncio
    async def test_counter_resets_after_recycle(self, _mock_browser, monkeypatch):
        """Navigation counter resets to 1 after recycle."""
        monkeypatch.setattr(srv, "_max_stdio_navigations", 2)
        state = srv._state
        state.session = None

        await state.get_session()
        await state.get_session()
        assert state._navigation_count == 2

        # Next call triggers recycle → counter resets to 1 (new session + 1 nav)
        await state.get_session()
        assert state._navigation_count == 1


@pytest.mark.allow_real_get_session
class TestStdioRecycleInvalidatesCache:
    @pytest.mark.asyncio
    async def test_cache_invalidated_on_recycle(self, _mock_browser, monkeypatch):
        """Cache is invalidated when session is recycled."""
        monkeypatch.setattr(srv, "_max_stdio_navigations", 2)
        state = srv._state
        state.session = None

        await state.get_session()
        await state.get_session()

        # Store a dummy cache entry
        mock_pm = MagicMock()
        mock_pm.url = "https://example.com"
        state.cache.store(mock_pm, None)
        assert state.cache.active is not None

        # Trigger recycle
        await state.get_session()
        assert state.cache.active is None  # cache was invalidated
