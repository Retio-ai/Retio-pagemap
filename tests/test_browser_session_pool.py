# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for BrowserSession pool mode — start_from_pool / stop behavior.

All tests mock Playwright/Browser/Context to avoid launching a real browser.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pagemap.browser_session import BrowserSession


def _mock_browser():
    """Create a mock Browser with new_context returning a mock BrowserContext."""
    browser = AsyncMock()
    context = AsyncMock()
    context.route = AsyncMock()
    context.on = MagicMock()
    page = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser.new_context = AsyncMock(return_value=context)
    browser.is_connected = MagicMock(return_value=True)
    browser.close = AsyncMock()
    return browser, context, page


class TestStartFromPool:
    """start_from_pool() uses shared browser, sets _owns_browser=False."""

    async def test_creates_context_and_page(self):
        browser, context, page = _mock_browser()
        session = BrowserSession()
        await session.start_from_pool(browser)

        browser.new_context.assert_called_once()
        context.new_page.assert_called_once()
        assert session.page is page
        assert session._owns_browser is False

    async def test_does_not_create_playwright(self):
        browser, context, page = _mock_browser()
        session = BrowserSession()
        await session.start_from_pool(browser)

        assert session._playwright is None

    async def test_installs_scheme_block_route(self):
        browser, context, page = _mock_browser()
        session = BrowserSession()
        await session.start_from_pool(browser)

        # _install_scheme_block_route calls self._context.route("**/*", ...)
        context.route.assert_called_once()

    async def test_registers_dialog_and_page_handlers(self):
        browser, context, page = _mock_browser()
        session = BrowserSession()
        await session.start_from_pool(browser)

        on_calls = [call.args[0] for call in context.on.call_args_list]
        assert "dialog" in on_calls
        assert "page" in on_calls


class TestPoolStopClosesContextOnly:
    """stop() in pool mode closes context but not browser/playwright."""

    async def test_closes_context_not_browser(self):
        browser, context, page = _mock_browser()
        session = BrowserSession()
        await session.start_from_pool(browser)
        await session.stop()

        context.close.assert_called_once()
        browser.close.assert_not_called()
        assert session._browser is None
        assert session._context is None
        assert session._page is None

    async def test_detaches_cdp(self):
        browser, context, page = _mock_browser()
        session = BrowserSession()
        await session.start_from_pool(browser)
        cdp = AsyncMock()
        session._cdp_session = cdp
        await session.stop()

        cdp.detach.assert_called_once()
        assert session._cdp_session is None


class TestStandaloneStopClosesAll:
    """stop() in standalone mode closes everything (existing behavior preserved)."""

    async def test_closes_browser_and_playwright(self):
        browser, context, page = _mock_browser()
        pw = AsyncMock()

        session = BrowserSession()
        session._playwright = pw
        session._browser = browser
        session._context = context
        session._page = page
        session._owns_browser = True

        await session.stop()

        context.close.assert_called_once()
        browser.close.assert_called_once()
        pw.stop.assert_called_once()
        assert session._browser is None
        assert session._playwright is None


class TestOwnsBrowserDefault:
    """Default _owns_browser is True."""

    def test_default_true(self):
        session = BrowserSession()
        assert session._owns_browser is True


class TestCreateContextReuse:
    """Both start() and start_from_pool() go through _create_context()."""

    async def test_start_uses_create_context(self):
        session = BrowserSession()
        browser, context, page = _mock_browser()

        with (
            patch.object(session, "_create_context", new_callable=AsyncMock) as mock_cc,
            patch.object(session, "_launch_browser", new_callable=AsyncMock),
            patch("pagemap.browser_session.async_playwright") as mock_pw,
        ):
            mock_pw.return_value.start = AsyncMock(return_value=AsyncMock())
            session._browser = browser
            await session.start()
            mock_cc.assert_called_once_with(browser)

    async def test_start_from_pool_uses_create_context(self):
        session = BrowserSession()
        browser, context, page = _mock_browser()

        with patch.object(session, "_create_context", new_callable=AsyncMock) as mock_cc:
            await session.start_from_pool(browser)
            mock_cc.assert_called_once_with(browser)
