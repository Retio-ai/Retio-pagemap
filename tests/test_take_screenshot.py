"""Tests for the take_screenshot MCP tool.

Verifies:
1. Basic screenshot returns McpImage + text metadata
2. No PageMap required (standalone diagnostic tool)
3. full_page parameter forwarding
4. Browser death handling
5. Timeout handling
6. Dialog warnings included in response
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.server.fastmcp import Image as McpImage
from playwright.async_api import Error as PlaywrightError

from pagemap import PageMap
from pagemap.server import (
    SCREENSHOT_TIMEOUT_SECONDS,
    take_screenshot,
)


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


# ── Helpers ──────────────────────────────────────────────────────────

# Minimal valid PNG bytes (1x1 transparent pixel)
_FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"


def _make_mock_session() -> MagicMock:
    """Create a mock BrowserSession with screenshot support."""
    session = MagicMock()
    session.drain_dialogs = MagicMock(return_value=[])

    page = MagicMock()
    page.screenshot = AsyncMock(return_value=_FAKE_PNG)
    session.page = page

    return session


# ── TestScreenshotBasic ──────────────────────────────────────────────


class TestScreenshotBasic:
    """Basic screenshot functionality."""

    async def test_returns_list_with_image_and_text(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], McpImage)
        assert isinstance(result[1], str)

    async def test_image_has_png_data(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        img = result[0]
        assert img.data == _FAKE_PNG
        assert img._format == "png"

    async def test_text_includes_byte_count(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert f"{len(_FAKE_PNG)} bytes" in result[1]
        assert "Screenshot captured" in result[1]

    async def test_default_viewport_screenshot(self):
        """Default: full_page=False → viewport screenshot."""
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await take_screenshot()

        mock_session.page.screenshot.assert_called_once_with(full_page=False, type="png")

    async def test_constant_defined(self):
        assert SCREENSHOT_TIMEOUT_SECONDS == 15


# ── TestScreenshotNoPageMapRequired ──────────────────────────────────


class TestScreenshotNoPageMapRequired:
    """Screenshot works without an active PageMap."""

    async def test_works_with_no_page_map(self):
        import pagemap.server as srv

        srv._state.cache.invalidate_all()
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert isinstance(result, list)
        assert "Screenshot captured" in result[1]

    async def test_does_not_invalidate_existing_page_map(self):
        """Screenshot should not touch _last_page_map."""
        import pagemap.server as srv

        sentinel = _make_page_map()
        srv._state.cache.store(sentinel, None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await take_screenshot()

        assert srv._state.cache.active is sentinel


# ── TestScreenshotFullPage ──────────────────────────────────────────


class TestScreenshotFullPage:
    """full_page parameter forwarding."""

    async def test_full_page_true(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await take_screenshot(full_page=True)

        mock_session.page.screenshot.assert_called_once_with(full_page=True, type="png")

    async def test_full_page_false(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await take_screenshot(full_page=False)

        mock_session.page.screenshot.assert_called_once_with(full_page=False, type="png")


# ── TestScreenshotBrowserDead ──────────────────────────────────────


class TestScreenshotBrowserDead:
    """Browser death during screenshot."""

    async def test_target_closed_returns_error(self):
        import pagemap.server as srv

        sentinel = _make_page_map()
        srv._state.cache.store(sentinel, None)
        mock_session = _make_mock_session()
        mock_session.page.screenshot = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert isinstance(result, str)
        assert "Browser connection lost" in result
        assert srv._state.cache.active is None

    async def test_browser_disconnected_returns_error(self):
        mock_session = _make_mock_session()
        mock_session.page.screenshot = AsyncMock(side_effect=PlaywrightError("Browser disconnected"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert isinstance(result, str)
        assert "Browser connection lost" in result


# ── TestScreenshotTimeout ──────────────────────────────────────────


class TestScreenshotTimeout:
    """Timeout handling for screenshot."""

    async def test_timeout_returns_error_string(self):
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.page.screenshot = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.SCREENSHOT_TIMEOUT_SECONDS", 0.1),
        ):
            result = await take_screenshot()

        assert isinstance(result, str)
        assert "timed out" in result


# ── TestScreenshotDialogWarnings ──────────────────────────────────


class TestScreenshotDialogWarnings:
    """Dialog warnings are included in screenshot responses."""

    async def test_dialog_warning_appended(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="confirm", message="Delete?", dismissed=True)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert "JS dialog" in result[1]
        assert "Delete?" in result[1]
