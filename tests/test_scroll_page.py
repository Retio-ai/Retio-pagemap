"""Tests for the scroll_page MCP tool.

Verifies:
1. Input validation (direction, amount, negative/overflow pixels)
2. Basic scroll down/up with page/half/pixels
3. Page map invalidation on scroll
4. Scroll position metadata in response (JSON, atBottom/atTop hints)
5. Browser death, timeout, dialog warnings
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap
from pagemap.server import (
    SCROLL_TIMEOUT_SECONDS,
    VALID_SCROLL_AMOUNTS,
    VALID_SCROLL_DIRECTIONS,
    scroll_page,
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
                name="Load More",
                affordance="click",
                region="main",
                tier=1,
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _scroll_position(
    scroll_y: int = 0,
    scroll_height: int = 5000,
    client_height: int = 800,
    scroll_x: int = 0,
    scroll_width: int = 1280,
    client_width: int = 1280,
) -> dict:
    """Create a scroll position dict."""
    return {
        "scrollX": scroll_x,
        "scrollY": scroll_y,
        "scrollWidth": scroll_width,
        "scrollHeight": scroll_height,
        "clientWidth": client_width,
        "clientHeight": client_height,
    }


def _make_mock_session(
    initial_pos: dict | None = None,
    scroll_result: dict | None = None,
) -> MagicMock:
    """Create a mock BrowserSession with scroll support."""
    if initial_pos is None:
        initial_pos = _scroll_position()
    if scroll_result is None:
        scroll_result = _scroll_position(scroll_y=800)

    session = MagicMock()
    session.get_scroll_position = AsyncMock(return_value=initial_pos)
    session.scroll = AsyncMock(return_value=scroll_result)
    session.drain_dialogs = MagicMock(return_value=[])

    return session


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._last_page_map = None
    yield
    srv._last_page_map = None


# ── TestScrollConstants ──────────────────────────────────────────────


class TestScrollConstants:
    """Verify scroll constants."""

    def test_valid_directions(self):
        assert frozenset({"up", "down"}) == VALID_SCROLL_DIRECTIONS

    def test_valid_amounts(self):
        assert frozenset({"page", "half"}) == VALID_SCROLL_AMOUNTS

    def test_timeout(self):
        assert SCROLL_TIMEOUT_SECONDS == 10


# ── TestScrollInputValidation ──────────────────────────────────────


class TestScrollInputValidation:
    """Input validation for scroll_page."""

    @pytest.mark.asyncio
    async def test_invalid_direction(self):
        result = await scroll_page(direction="left")
        assert "Invalid direction" in result

    @pytest.mark.asyncio
    async def test_invalid_direction_right(self):
        result = await scroll_page(direction="right")
        assert "Invalid direction" in result

    @pytest.mark.asyncio
    async def test_invalid_amount(self):
        result = await scroll_page(amount="lots")
        assert "Invalid amount" in result

    @pytest.mark.asyncio
    async def test_negative_pixels(self):
        result = await scroll_page(amount="-100")
        assert "non-negative" in result

    @pytest.mark.asyncio
    async def test_overflow_pixels(self):
        result = await scroll_page(amount="100000")
        assert "too large" in result

    @pytest.mark.asyncio
    async def test_max_allowed_pixels(self):
        """50000 is the max — should NOT error on validation."""
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(amount="50000")

        assert "Error" not in result or "Invalid" not in result

    @pytest.mark.asyncio
    async def test_zero_pixels_allowed(self):
        """0 pixels is valid (no-op scroll)."""
        mock_session = _make_mock_session(scroll_result=_scroll_position(scroll_y=0))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(amount="0")

        assert "Scrolled" in result


# ── TestScrollBasic ──────────────────────────────────────────────────


class TestScrollBasic:
    """Basic scroll operations."""

    @pytest.mark.asyncio
    async def test_scroll_down_page(self):
        mock_session = _make_mock_session(
            initial_pos=_scroll_position(client_height=800),
            scroll_result=_scroll_position(scroll_y=800),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="down", amount="page")

        assert "Scrolled down by 800px" in result
        mock_session.scroll.assert_called_once_with(delta_y=800)

    @pytest.mark.asyncio
    async def test_scroll_down_half(self):
        mock_session = _make_mock_session(
            initial_pos=_scroll_position(client_height=800),
            scroll_result=_scroll_position(scroll_y=400),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="down", amount="half")

        assert "Scrolled down by 400px" in result
        mock_session.scroll.assert_called_once_with(delta_y=400)

    @pytest.mark.asyncio
    async def test_scroll_down_pixels(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=300),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="down", amount="300")

        assert "Scrolled down by 300px" in result
        mock_session.scroll.assert_called_once_with(delta_y=300)

    @pytest.mark.asyncio
    async def test_scroll_up_page(self):
        mock_session = _make_mock_session(
            initial_pos=_scroll_position(client_height=800, scroll_y=800),
            scroll_result=_scroll_position(scroll_y=0),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="up", amount="page")

        assert "Scrolled up by 800px" in result
        mock_session.scroll.assert_called_once_with(delta_y=-800)

    @pytest.mark.asyncio
    async def test_scroll_up_pixels(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=200),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="up", amount="500")

        assert "Scrolled up by 500px" in result
        mock_session.scroll.assert_called_once_with(delta_y=-500)

    @pytest.mark.asyncio
    async def test_direction_case_insensitive(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="DOWN", amount="100")

        assert "Scrolled down" in result

    @pytest.mark.asyncio
    async def test_direction_whitespace_trimmed(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="  down  ", amount="100")

        assert "Scrolled down" in result

    @pytest.mark.asyncio
    async def test_amount_case_insensitive(self):
        """'Page' and 'HALF' should work as 'page' and 'half'."""
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="down", amount="Page")

        assert "Scrolled down" in result

        with patch("pagemap.server._get_session", return_value=mock_session):
            result2 = await scroll_page(direction="down", amount="HALF")

        assert "Scrolled down" in result2


# ── TestScrollInvalidation ──────────────────────────────────────────


class TestScrollInvalidation:
    """Verify page_map invalidation after scroll."""

    @pytest.mark.asyncio
    async def test_scroll_clears_page_map(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await scroll_page(direction="down", amount="page")

        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_scroll_suggests_get_page_map(self):
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert "get_page_map" in result


# ── TestScrollPosition ──────────────────────────────────────────────


class TestScrollPosition:
    """Verify scroll position metadata in response."""

    @pytest.mark.asyncio
    async def test_includes_scroll_metadata(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=800, scroll_height=5000, client_height=800),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert '"scrollY": 800' in result
        assert '"scrollHeight": 5000' in result
        assert '"viewportHeight": 800' in result

    @pytest.mark.asyncio
    async def test_scroll_percent_calculated(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=2100, scroll_height=5000, client_height=800),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert '"scrollPercent": 50' in result

    @pytest.mark.asyncio
    async def test_at_bottom_hint(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=4200, scroll_height=5000, client_height=800),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert "bottom of the page" in result
        assert '"atBottom": true' in result

    @pytest.mark.asyncio
    async def test_at_top_hint(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=0, scroll_height=5000, client_height=800),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page(direction="up")

        assert "top of the page" in result
        assert '"atTop": true' in result

    @pytest.mark.asyncio
    async def test_middle_position_no_hint(self):
        mock_session = _make_mock_session(
            scroll_result=_scroll_position(scroll_y=1000, scroll_height=5000, client_height=800),
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert "bottom of the page" not in result
        assert "top of the page" not in result
        assert '"atTop": false' in result
        assert '"atBottom": false' in result


# ── TestScrollErrors ──────────────────────────────────────────────


class TestScrollErrors:
    """Error handling for scroll_page."""

    @pytest.mark.asyncio
    async def test_browser_dead(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        mock_session.get_scroll_position = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_timeout(self):
        import pagemap.server as srv

        srv._last_page_map = object()
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.get_scroll_position = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.SCROLL_TIMEOUT_SECONDS", 0.1),
        ):
            result = await scroll_page()

        assert "timed out" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_dialog_warnings(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="alert", message="Loading...", dismissed=False)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await scroll_page()

        assert "JS dialog" in result
        assert "Loading..." in result


# ── TestScrollBrowserSession ──────────────────────────────────────


class TestScrollBrowserSession:
    """Tests for BrowserSession.scroll() and get_scroll_position()."""

    @pytest.mark.asyncio
    async def test_scroll_method_exists(self):
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        assert hasattr(session, "scroll")
        assert hasattr(session, "get_scroll_position")
        assert hasattr(session, "go_back")
