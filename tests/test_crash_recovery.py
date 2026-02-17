"""Tests for browser crash recovery.

Covers BrowserSession.is_alive() health check, stop() hardening,
and server._get_session() automatic recovery. All mock-based.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.browser_session import BrowserSession

# ── is_alive() ────────────────────────────────────────────────────


class TestIsAlive:
    """Tests for BrowserSession.is_alive() 2-stage health check."""

    @pytest.mark.asyncio
    async def test_returns_true_when_healthy(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock(return_value=1)

        assert await session.is_alive() is True

    @pytest.mark.asyncio
    async def test_returns_false_when_browser_is_none(self):
        session = BrowserSession()
        session._browser = None

        assert await session.is_alive() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_disconnected(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = False

        assert await session.is_alive() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_page_is_none(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = None

        assert await session.is_alive() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_target_closed(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock(side_effect=Exception("Target closed"))

        assert await session.is_alive() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()
        session._page.evaluate = AsyncMock(side_effect=ConnectionError("pipe broken"))

        assert await session.is_alive() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        session = BrowserSession()
        session._browser = MagicMock()
        session._browser.is_connected.return_value = True
        session._page = AsyncMock()

        async def hang():
            await asyncio.sleep(100)

        session._page.evaluate = hang

        assert await session.is_alive(timeout=0.05) is False

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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


class TestGetSessionRecovery:
    """Tests for server._get_session() crash recovery logic."""

    @pytest.mark.asyncio
    async def test_healthy_session_is_reused(self):
        import pagemap.server as srv

        mock_session = AsyncMock(spec=BrowserSession)
        mock_session.is_alive = AsyncMock(return_value=True)

        srv._session = mock_session
        srv._last_page_map = "some_map"

        result = await srv._get_session()

        assert result is mock_session
        mock_session.start.assert_not_called()
        assert srv._last_page_map == "some_map"

        # cleanup
        srv._session = None
        srv._last_page_map = None

    @pytest.mark.asyncio
    async def test_dead_session_is_replaced(self):
        import pagemap.server as srv

        dead_session = AsyncMock(spec=BrowserSession)
        dead_session.is_alive = AsyncMock(return_value=False)
        dead_session.stop = AsyncMock()

        srv._session = dead_session
        srv._last_page_map = "stale_map"

        new_session = AsyncMock(spec=BrowserSession)
        new_session.start = AsyncMock()

        with patch.object(srv, "BrowserSession", return_value=new_session):
            result = await srv._get_session()

        assert result is new_session
        dead_session.stop.assert_awaited_once()
        assert srv._last_page_map is None
        new_session.start.assert_awaited_once()

        # cleanup
        srv._session = None
        srv._last_page_map = None

    @pytest.mark.asyncio
    async def test_stop_failure_during_recovery_is_suppressed(self):
        import pagemap.server as srv

        dead_session = AsyncMock(spec=BrowserSession)
        dead_session.is_alive = AsyncMock(return_value=False)
        dead_session.stop = AsyncMock(side_effect=Exception("stop failed"))

        srv._session = dead_session
        srv._last_page_map = "stale_map"

        new_session = AsyncMock(spec=BrowserSession)
        new_session.start = AsyncMock()

        with patch.object(srv, "BrowserSession", return_value=new_session):
            result = await srv._get_session()

        assert result is new_session
        assert srv._last_page_map is None

        # cleanup
        srv._session = None
        srv._last_page_map = None

    @pytest.mark.asyncio
    async def test_recovery_start_failure_propagates(self):
        import pagemap.server as srv

        dead_session = AsyncMock(spec=BrowserSession)
        dead_session.is_alive = AsyncMock(return_value=False)
        dead_session.stop = AsyncMock()

        srv._session = dead_session
        srv._last_page_map = "stale_map"

        new_session = AsyncMock(spec=BrowserSession)
        new_session.start = AsyncMock(side_effect=RuntimeError("launch failed"))

        with (
            patch.object(srv, "BrowserSession", return_value=new_session),
            pytest.raises(RuntimeError, match="launch failed"),
        ):
            await srv._get_session()

        # cleanup
        srv._session = None
        srv._last_page_map = None
