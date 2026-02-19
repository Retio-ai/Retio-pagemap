"""Tests for execute_action timeout + TargetClosedError handling.

Verifies:
1. TargetClosedError during action → _last_page_map=None + recovery message
2. Browser disconnected patterns → same handling
3. Overall execute_action timeout → _last_page_map=None + timeout message
4. Timeout during locator resolution → retry or error
5. Session recovery after browser death
6. _is_browser_dead_error() classifier
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap
from pagemap.server import (
    _is_browser_dead_error,
    execute_action,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
    """Create a minimal PageMap for testing."""
    return PageMap(
        url=url,
        title="Test Page",
        page_type="unknown",
        interactables=[
            Interactable(
                ref=1,
                role="button",
                name="Submit",
                affordance="click",
                region="main",
                tier=1,
                selector="#submit-btn",
            ),
            Interactable(
                ref=2,
                role="textbox",
                name="Search",
                affordance="type",
                region="main",
                tier=1,
                selector="input.search-box",
            ),
            Interactable(
                ref=3,
                role="combobox",
                name="Sort by",
                affordance="select",
                region="main",
                tier=1,
                selector="select.sort-dropdown",
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(
    current_url: str = "https://example.com",
    role_count: int = 1,
) -> MagicMock:
    """Create a mock BrowserSession."""
    session = MagicMock()
    session.get_page_url = AsyncMock(return_value=current_url)

    locator = AsyncMock()
    locator.first = AsyncMock()
    locator.first.click = AsyncMock()
    locator.first.fill = AsyncMock()
    locator.first.select_option = AsyncMock()
    locator.count = AsyncMock(return_value=role_count)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=locator)
    page.locator = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    type(page).url = PropertyMock(return_value=current_url)

    session.page = page
    return session


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._last_page_map = None
    yield
    srv._last_page_map = None


# ── TestBrowserDeadClassifier ────────────────────────────────────────


class TestBrowserDeadClassifier:
    """Unit tests for _is_browser_dead_error()."""

    def test_target_closed(self):
        exc = PlaywrightError("Target closed")
        assert _is_browser_dead_error(exc) is True

    def test_target_page(self):
        exc = PlaywrightError("Target page, context or browser has been closed")
        assert _is_browser_dead_error(exc) is True

    def test_browser_has_been_closed(self):
        exc = PlaywrightError("browser has been closed")
        assert _is_browser_dead_error(exc) is True

    def test_connection_closed(self):
        exc = Exception("Connection closed while reading from the driver")
        assert _is_browser_dead_error(exc) is True

    def test_browser_disconnected(self):
        exc = PlaywrightError("Browser disconnected")
        assert _is_browser_dead_error(exc) is True

    def test_normal_error_not_dead(self):
        exc = PlaywrightError("Timeout 5000ms exceeded")
        assert _is_browser_dead_error(exc) is False

    def test_element_not_visible_not_dead(self):
        exc = PlaywrightError("Element is not visible")
        assert _is_browser_dead_error(exc) is False


# ── TestTargetClosedDuringAction ─────────────────────────────────────


class TestTargetClosedDuringAction:
    """TargetClosedError during click/type/select/press_key
    → _last_page_map=None + recovery message."""

    @pytest.mark.asyncio
    async def test_click_target_closed(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Browser connection lost" in result
        assert "get_page_map" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_type_target_closed(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_select_target_closed(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.select_option = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_press_key_target_closed(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        mock_session.page.keyboard.press = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_target_page_context_closed(self):
        """Full Playwright error message variant."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Target page, context or browser has been closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Browser connection lost" in result
        assert srv._last_page_map is None


# ── TestBrowserDisconnected ──────────────────────────────────────────


class TestBrowserDisconnected:
    """Non-Playwright disconnect errors → same browser-dead handling."""

    @pytest.mark.asyncio
    async def test_browser_disconnected_error(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Browser disconnected"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_connection_closed_error(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=Exception("Connection closed while reading"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        assert "Browser connection lost" in result
        assert srv._last_page_map is None


# ── TestOverallTimeout ───────────────────────────────────────────────


class TestOverallTimeout:
    """execute_action overall timeout → _last_page_map=None + timeout message."""

    @pytest.mark.asyncio
    async def test_timeout_on_slow_click(self):
        """Action hangs forever → overall timeout fires."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        locator.first.click = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.EXECUTE_ACTION_TIMEOUT_SECONDS", 0.1),
        ):
            result = await execute_action(ref=1, action="click")

        assert "timed out" in result
        assert "get_page_map" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_timeout_on_slow_type(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        locator.first.fill = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.EXECUTE_ACTION_TIMEOUT_SECONDS", 0.1),
        ):
            result = await execute_action(ref=2, action="type", value="hello")

        assert "timed out" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_timeout_includes_seconds_in_message(self):
        """Message includes the actual timeout value."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.page.keyboard.press = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.EXECUTE_ACTION_TIMEOUT_SECONDS", 0.1),
        ):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        assert "timed out" in result
        assert "0.1s" in result


# ── TestTimeoutDuringLocator ─────────────────────────────────────────


class TestTimeoutDuringLocator:
    """Playwright 5s timeout during _resolve_locator → retry or error."""

    @pytest.mark.asyncio
    async def test_locator_timeout_triggers_retry(self):
        """Playwright timeout in locator.count() → retried via retry helper."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        # fill times out first attempt, succeeds second
        locator.first.fill = AsyncMock(side_effect=[PlaywrightError("Timeout 5000ms exceeded"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        assert "Typed into [2]" in result
        assert locator.first.fill.call_count == 2

    @pytest.mark.asyncio
    async def test_locator_count_timeout_falls_to_css(self):
        """role locator count() raises → fallback to CSS selector."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        page = mock_session.page

        role_locator = MagicMock()
        role_locator.count = AsyncMock(side_effect=PlaywrightError("Timeout"))

        css_locator = AsyncMock()
        css_locator.count = AsyncMock(return_value=1)
        css_locator.first = AsyncMock()
        css_locator.first.fill = AsyncMock()

        page.get_by_role = MagicMock(return_value=role_locator)
        page.locator = MagicMock(return_value=css_locator)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        assert "Typed into [2]" in result
        assert "CSS selector" in result


# ── TestSessionRecoveryAfterDeath ────────────────────────────────────


class TestSessionRecoveryAfterDeath:
    """After TargetClosedError → next get_page_map should recover."""

    @pytest.mark.asyncio
    async def test_page_map_none_after_target_closed(self):
        """TargetClosed sets _last_page_map=None, subsequent check confirms."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            await execute_action(ref=1, action="click")

        # _last_page_map was invalidated
        assert srv._last_page_map is None

        # Subsequent execute_action without get_page_map → "No active Page Map"
        result = await execute_action(ref=1, action="click")
        assert "No active Page Map" in result

    @pytest.mark.asyncio
    async def test_execute_after_timeout_needs_refresh(self):
        """After timeout, next execute_action requires get_page_map refresh."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.page.keyboard.press = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.EXECUTE_ACTION_TIMEOUT_SECONDS", 0.1),
        ):
            await execute_action(ref=1, action="press_key", value="Enter")

        assert srv._last_page_map is None

        # Next call without refresh → error
        result = await execute_action(ref=1, action="click")
        assert "No active Page Map" in result
