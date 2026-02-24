# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for BrowserPool — shared browser with per-session context isolation.

All tests mock Playwright/Browser/BrowserSession to avoid launching a real browser.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.browser_pool import (
    BrowserPool,
    PoolHealth,
)
from pagemap.browser_session import BrowserConfig, BrowserSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_playwright_and_browser():
    """Create mock playwright, browser for patching."""
    pw = AsyncMock()
    browser = AsyncMock()
    browser.is_connected = MagicMock(return_value=True)
    browser.close = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()
    return pw, browser


@pytest.fixture
def mock_pw():
    """Patch async_playwright to return mock objects."""
    pw, browser = _mock_playwright_and_browser()
    with patch("pagemap.browser_pool.async_playwright") as mock_apw:
        mock_start = AsyncMock(return_value=pw)
        mock_apw.return_value.start = mock_start
        yield pw, browser


def _mock_browser_session():
    """Create a mock BrowserSession for pool tests."""
    sess = AsyncMock(spec=BrowserSession)
    sess.start_from_pool = AsyncMock()
    sess.stop = AsyncMock()
    sess.is_alive = AsyncMock(return_value=True)
    sess.config = BrowserConfig()
    sess._chromium_launch_args = MagicMock(return_value=["--headless"])
    return sess


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestContextManagerLifecycle:
    """async with BrowserPool() as pool: starts and stops cleanly."""

    async def test_enter_starts_browser(self, mock_pw):
        pw, browser = mock_pw
        async with BrowserPool(max_contexts=2) as pool:
            assert pool._browser is browser
            assert pool._playwright is pw
            assert pool._semaphore is not None
            assert pool._reaper_task is not None

    async def test_exit_shuts_down(self, mock_pw):
        pw, browser = mock_pw
        async with BrowserPool(max_contexts=2) as pool:
            pass
        browser.close.assert_called_once()
        pw.stop.assert_called_once()
        assert pool._browser is None
        assert pool._playwright is None


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------


class TestSessionContextManager:
    """async with pool.session(sid) as sess: acquires and releases."""

    async def test_yields_browser_session(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=2) as pool, pool.session("s1") as sess:
                assert sess is mock_sess
                mock_sess.start_from_pool.assert_called_once_with(browser)

    async def test_releases_on_normal_exit(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=2) as pool:
                async with pool.session("s1"):
                    pass
                # Semaphore should be released (can acquire again)
                assert pool._semaphore._value == 2  # max_contexts restored


class TestSessionExceptionReleases:
    """Semaphore is released even if session usage raises."""

    async def test_exception_releases_semaphore(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=2) as pool:
                with pytest.raises(RuntimeError):
                    async with pool.session("s1"):
                        raise RuntimeError("test error")
                # Semaphore still released
                assert pool._semaphore._value == 2


# ---------------------------------------------------------------------------
# Acquire/release
# ---------------------------------------------------------------------------


class TestAcquireCreatesContext:
    """New session_id → BrowserSession.start_from_pool() called."""

    async def test_new_session(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=3) as pool:
                sess = await pool.acquire("new-sess")
                assert sess is mock_sess
                mock_sess.start_from_pool.assert_called_once_with(browser)
                assert "new-sess" in pool._contexts
                # Clean up
                await pool.release("new-sess")


class TestAcquireReturnsExisting:
    """Same session_id → same session, last_used_at updated."""

    async def test_returns_same_session(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=3) as pool:
                async with pool.session("s1") as sess1:
                    pass
                t1 = pool._contexts["s1"].last_used_at
                await asyncio.sleep(0.01)
                async with pool.session("s1") as sess2:
                    pass
                assert sess1 is sess2
                assert pool._contexts["s1"].last_used_at >= t1


class TestReleaseClosesContext:
    """release() → session.stop() called."""

    async def test_release(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=3) as pool:
                await pool.acquire("s1")
                await pool.release("s1")
                mock_sess.stop.assert_called_once()
                assert "s1" not in pool._contexts


class TestReleaseNonexistent:
    """Fix 2: release() on unknown session_id must not inflate semaphore."""

    async def test_no_semaphore_inflation(self, mock_pw):
        pw, browser = mock_pw
        async with BrowserPool(max_contexts=3) as pool:
            sem_before = pool._semaphore._value
            await pool.release("nonexistent")
            assert pool._semaphore._value == sem_before


# ---------------------------------------------------------------------------
# Semaphore capacity
# ---------------------------------------------------------------------------


class TestSemaphoreBlocksAtCapacity:
    """max_contexts=1 → second acquire blocks until release."""

    async def test_blocks_and_unblocks(self, mock_pw):
        pw, browser = mock_pw

        call_count = 0

        def make_session(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_browser_session()

        with patch("pagemap.browser_pool.BrowserSession", side_effect=make_session):
            async with BrowserPool(max_contexts=1) as pool:
                async with pool.session("s1"):
                    # Pool at capacity. Second acquire in background:
                    acquired = asyncio.Event()

                    async def try_acquire():
                        async with pool.session("s2"):
                            acquired.set()

                    task = asyncio.create_task(try_acquire())
                    await asyncio.sleep(0.05)
                    assert not acquired.is_set()  # still blocked

                # s1 released → s2 can proceed
                await asyncio.sleep(0.05)
                assert acquired.is_set()
                await task


class TestAcquireTimeout:
    """Pool full + ACQUIRE_TIMEOUT → TimeoutError."""

    async def test_timeout(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            MockBS.return_value = _mock_browser_session()
            with patch("pagemap.browser_pool._ACQUIRE_TIMEOUT", 0.1):
                async with BrowserPool(max_contexts=1) as pool:
                    async with pool.session("s1"):
                        with pytest.raises(TimeoutError):
                            async with pool.session("s2"):
                                pass  # should not reach here


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------


class TestReaperRemovesIdle:
    """Idle sessions beyond timeout are reaped."""

    async def test_reaper_evicts(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=3, idle_timeout=0.05) as pool:
                # Patch reaper interval to be fast
                with patch("pagemap.browser_pool._REAPER_INTERVAL", 0.05):
                    async with pool.session("s1"):
                        pass
                    # Manually set last_used_at to the past
                    pool._contexts["s1"].last_used_at = time.monotonic() - 1.0

                    # Restart reaper with fast interval
                    pool._shutdown_event.clear()
                    if pool._reaper_task:
                        pool._reaper_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await pool._reaper_task
                    pool._start_reaper()

                    await asyncio.sleep(0.2)
                    assert "s1" not in pool._contexts

    async def test_reaper_releases_semaphore_for_acquired(self, mock_pw):
        """Fix 1: reaper must release semaphore for acquire()-held sessions."""
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=2, idle_timeout=0.05) as pool:
                with patch("pagemap.browser_pool._REAPER_INTERVAL", 0.05):
                    # acquire() holds semaphore slot (value 2→1)
                    await pool.acquire("s1")
                    assert pool._semaphore._value == 1
                    pool._contexts["s1"].last_used_at = time.monotonic() - 1.0

                    # Restart reaper with fast interval
                    pool._shutdown_event.clear()
                    if pool._reaper_task:
                        pool._reaper_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await pool._reaper_task
                    pool._start_reaper()

                    await asyncio.sleep(0.2)
                    assert "s1" not in pool._contexts
                    # Semaphore slot must be restored
                    assert pool._semaphore._value == 2

    async def test_reaper_no_over_release_for_session_cm(self, mock_pw):
        """Reaper must NOT release semaphore for session() CM entries."""
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            async with BrowserPool(max_contexts=2, idle_timeout=0.05) as pool:
                with patch("pagemap.browser_pool._REAPER_INTERVAL", 0.05):
                    # session() CM: semaphore borrowed transiently, entry persists
                    async with pool.session("s1"):
                        pass
                    # Semaphore fully restored after CM exit
                    assert pool._semaphore._value == 2
                    assert "s1" in pool._contexts
                    pool._contexts["s1"].last_used_at = time.monotonic() - 1.0

                    # Restart reaper
                    pool._shutdown_event.clear()
                    if pool._reaper_task:
                        pool._reaper_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await pool._reaper_task
                    pool._start_reaper()

                    await asyncio.sleep(0.2)
                    assert "s1" not in pool._contexts
                    # Semaphore must NOT inflate beyond max_contexts
                    assert pool._semaphore._value == 2


class TestReaperCrashRecovery:
    """Reaper crash → done_callback restarts it."""

    async def test_reaper_restarts(self, mock_pw):
        pw, browser = mock_pw
        async with BrowserPool(max_contexts=2) as pool:
            first_task = pool._reaper_task
            assert first_task is not None

            # Simulate crash by cancelling the task and checking the callback fires
            # Instead, let's directly test _handle_reaper_crash
            mock_task = MagicMock()
            mock_task.cancelled.return_value = False
            mock_task.exception.return_value = RuntimeError("boom")

            pool._handle_reaper_crash(mock_task)

            # A new reaper should be started
            assert pool._reaper_task is not None
            assert pool._reaper_task is not first_task


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdownClosesAll:
    """shutdown() closes all contexts + browser + playwright."""

    async def test_full_shutdown(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            mock_sess = _mock_browser_session()
            MockBS.return_value = mock_sess

            pool = BrowserPool(max_contexts=3)
            await pool.__aenter__()

            async with pool.session("s1"):
                pass
            async with pool.session("s2"):
                pass

            await pool.shutdown()

            assert len(pool._contexts) == 0
            assert pool._browser is None
            assert pool._playwright is None
            # Sessions should have been stopped
            assert mock_sess.stop.call_count >= 2


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """health() returns accurate PoolHealth snapshot."""

    async def test_health_empty_pool(self, mock_pw):
        pw, browser = mock_pw
        async with BrowserPool(max_contexts=5) as pool:
            h = pool.health()
            assert h.active == 0
            assert h.max_contexts == 5
            assert h.browser_connected is True
            assert isinstance(h, PoolHealth)

    async def test_health_with_sessions(self, mock_pw):
        pw, browser = mock_pw
        with patch("pagemap.browser_pool.BrowserSession") as MockBS:
            MockBS.return_value = _mock_browser_session()

            async with BrowserPool(max_contexts=5) as pool, pool.session("s1"):
                h = pool.health()
                assert h.active == 1

    async def test_active_count_and_capacity(self, mock_pw):
        pw, browser = mock_pw
        async with BrowserPool(max_contexts=3) as pool:
            assert pool.active_count == 0
            assert pool.capacity == 3
