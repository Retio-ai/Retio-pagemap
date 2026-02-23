"""Tests for browser crash recovery.

Covers BrowserSession.is_alive() health check, stop() hardening,
and server._get_session() automatic recovery. All mock-based.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap import PageMap
from pagemap.browser_session import BrowserSession


def _make_page_map(url: str = "https://example.com") -> PageMap:
    return PageMap(
        url=url,
        title="Test",
        page_type="unknown",
        interactables=[],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0,
    )


# ── is_alive() ────────────────────────────────────────────────────


class TestIsAlive:
    """Tests for BrowserSession.is_alive() 2-stage health check."""

    async def test_returns_true_when_healthy(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock(return_value=1)

        assert await session.is_alive() is True

    async def test_returns_false_when_browser_is_none(self):
        session = BrowserSession()
        session._browser = None

        assert await session.is_alive() is False

    async def test_returns_false_when_disconnected(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = False

        assert await session.is_alive() is False

    async def test_returns_false_when_page_is_none(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = None

        assert await session.is_alive() is False

    async def test_returns_false_on_target_closed(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock(side_effect=Exception("Target closed"))

        assert await session.is_alive() is False

    async def test_returns_false_on_connection_error(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock(side_effect=ConnectionError("pipe broken"))

        assert await session.is_alive() is False

    async def test_returns_false_on_timeout(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()

        async def hang():
            await asyncio.sleep(100)

        session._page.evaluate = hang

        assert await session.is_alive(timeout=0.05) is False

    async def test_skips_evaluate_when_disconnected(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = False
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock()

        await session.is_alive()

        session._page.evaluate.assert_not_called()


# ── stop() hardening ──────────────────────────────────────────────


class TestStopHardening:
    """Tests that stop() suppresses errors from crashed browsers."""

    async def test_suppresses_browser_close_error(self):
        session = BrowserSession()
        session._browser = AsyncMock()
        session._browser.close = AsyncMock(side_effect=Exception("TargetClosedError"))
        session._playwright = AsyncMock()
        session._playwright.stop = AsyncMock()

        await session.stop()

        assert session._browser is None
        assert session._playwright is None
        assert session._page is None
        assert session._context is None


# ── _get_session() recovery ───────────────────────────────────────


@pytest.mark.allow_real_get_session
class TestGetSessionRecovery:
    """Tests for server._get_session() crash recovery logic."""

    async def test_healthy_session_is_reused(self):
        import pagemap.server as srv

        mock_session = AsyncMock(spec=BrowserSession)
        mock_session.is_alive = AsyncMock(return_value=True)

        srv._state.session = mock_session
        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)

        result = await srv._get_session()

        assert result is mock_session
        mock_session.start.assert_not_called()
        assert srv._state.cache.active is page_map

        # cleanup
        srv._state.session = None
        srv._state.cache.invalidate_all()

    async def test_dead_session_is_replaced(self):
        import pagemap.server as srv

        dead_session = AsyncMock(spec=BrowserSession)
        dead_session.is_alive = AsyncMock(return_value=False)
        dead_session.stop = AsyncMock()

        srv._state.session = dead_session
        srv._state.cache.store(_make_page_map(), None)

        new_session = AsyncMock(spec=BrowserSession)
        new_session.start = AsyncMock()

        with patch.object(srv, "BrowserSession", return_value=new_session):
            result = await srv._get_session()

        assert result is new_session
        dead_session.stop.assert_awaited_once()
        assert srv._state.cache.active is None
        new_session.start.assert_awaited_once()

        # cleanup
        srv._state.session = None
        srv._state.cache.invalidate_all()

    async def test_stop_failure_during_recovery_is_suppressed(self):
        import pagemap.server as srv

        dead_session = AsyncMock(spec=BrowserSession)
        dead_session.is_alive = AsyncMock(return_value=False)
        dead_session.stop = AsyncMock(side_effect=Exception("stop failed"))

        srv._state.session = dead_session
        srv._state.cache.store(_make_page_map(), None)

        new_session = AsyncMock(spec=BrowserSession)
        new_session.start = AsyncMock()

        with patch.object(srv, "BrowserSession", return_value=new_session):
            result = await srv._get_session()

        assert result is new_session
        assert srv._state.cache.active is None

        # cleanup
        srv._state.session = None
        srv._state.cache.invalidate_all()

    async def test_recovery_start_failure_propagates(self):
        import pagemap.server as srv

        dead_session = AsyncMock(spec=BrowserSession)
        dead_session.is_alive = AsyncMock(return_value=False)
        dead_session.stop = AsyncMock()

        srv._state.session = dead_session
        srv._state.cache.store(_make_page_map(), None)

        new_session = AsyncMock(spec=BrowserSession)
        new_session.start = AsyncMock(side_effect=RuntimeError("launch failed"))

        with (
            patch.object(srv, "BrowserSession", return_value=new_session),
            pytest.raises(RuntimeError, match="launch failed"),
        ):
            await srv._get_session()

        # cleanup
        srv._state.session = None
        srv._state.cache.invalidate_all()
