"""Tests for the navigate_back MCP tool.

Verifies:
1. Basic navigation back with URL return + page_map invalidation
2. No history → message, page_map preserved
3. SSRF post-check on navigated URL
4. Browser death handling
5. Timeout handling
6. Dialog warnings
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap
from pagemap.server import (
    NAVIGATE_BACK_TIMEOUT_SECONDS,
    navigate_back,
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
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(go_back_url: str | None = "https://example.com/prev") -> MagicMock:
    """Create a mock BrowserSession with go_back support."""
    session = MagicMock()
    session.go_back = AsyncMock(return_value=go_back_url)
    session.drain_dialogs = MagicMock(return_value=[])

    page = MagicMock()
    page.goto = AsyncMock()
    session.page = page

    return session


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._last_page_map = None
    yield
    srv._last_page_map = None


# ── TestNavigateBackBasic ──────────────────────────────────────────


class TestNavigateBackBasic:
    """Basic navigate_back functionality."""

    @pytest.mark.asyncio
    async def test_returns_navigated_url(self):
        mock_session = _make_mock_session(go_back_url="https://example.com/prev")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await navigate_back()

        assert "https://example.com/prev" in result
        assert "Navigated back" in result

    @pytest.mark.asyncio
    async def test_invalidates_page_map(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            await navigate_back()

        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_suggests_get_page_map(self):
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await navigate_back()

        assert "get_page_map" in result

    @pytest.mark.asyncio
    async def test_constant_defined(self):
        assert NAVIGATE_BACK_TIMEOUT_SECONDS == 30


# ── TestNavigateBackNoHistory ──────────────────────────────────────


class TestNavigateBackNoHistory:
    """No history → informational message, page_map preserved."""

    @pytest.mark.asyncio
    async def test_no_history_returns_message(self):
        mock_session = _make_mock_session(go_back_url=None)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "No previous page" in result

    @pytest.mark.asyncio
    async def test_no_history_preserves_page_map(self):
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._last_page_map = page_map
        mock_session = _make_mock_session(go_back_url=None)

        with patch("pagemap.server._get_session", return_value=mock_session):
            await navigate_back()

        assert srv._last_page_map is page_map


# ── TestNavigateBackSsrf ──────────────────────────────────────────


class TestNavigateBackSsrf:
    """SSRF post-check on navigated URL."""

    @pytest.mark.asyncio
    async def test_blocked_url_resets_to_blank(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session(go_back_url="http://169.254.169.254/metadata")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "blocked" in result.lower()
        mock_session.page.goto.assert_called_once_with("about:blank")
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_private_ip_blocked(self):
        mock_session = _make_mock_session(go_back_url="http://192.168.1.1/admin")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_safe_url_allowed(self):
        mock_session = _make_mock_session(go_back_url="https://safe.example.com")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await navigate_back()

        assert "Navigated back" in result
        assert "safe.example.com" in result

    @pytest.mark.asyncio
    async def test_ssrf_error_includes_reason(self):
        mock_session = _make_mock_session(go_back_url="http://127.0.0.1/admin")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "blocked" in result.lower() or "Error" in result


# ── TestNavigateBackBrowserDead ──────────────────────────────────────


class TestNavigateBackBrowserDead:
    """Browser death during navigate_back."""

    @pytest.mark.asyncio
    async def test_target_closed(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        mock_session.go_back = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_browser_disconnected(self):
        mock_session = _make_mock_session()
        mock_session.go_back = AsyncMock(side_effect=PlaywrightError("Browser disconnected"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "Browser connection lost" in result


# ── TestNavigateBackTimeout ──────────────────────────────────────────


class TestNavigateBackTimeout:
    """Timeout handling for navigate_back."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        import pagemap.server as srv

        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.go_back = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.NAVIGATE_BACK_TIMEOUT_SECONDS", 0.1),
        ):
            result = await navigate_back()

        assert "timed out" in result
        assert srv._last_page_map is None


# ── TestNavigateBackDialogWarnings ──────────────────────────────────


class TestNavigateBackDialogWarnings:
    """Dialog warnings included in navigate_back responses."""

    @pytest.mark.asyncio
    async def test_dialog_warning_on_success(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="alert", message="Leaving page", dismissed=False)]
        )

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await navigate_back()

        assert "JS dialog" in result
        assert "Leaving page" in result

    @pytest.mark.asyncio
    async def test_dialog_warning_on_no_history(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session(go_back_url=None)
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="confirm", message="Sure?", dismissed=True)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await navigate_back()

        assert "No previous page" in result
        assert "JS dialog" in result
        assert "Sure?" in result


# ── TestNavigateBackPageMapNone ──────────────────────────────────────


class TestNavigateBackPageMapNone:
    """navigate_back works even when _last_page_map is already None."""

    @pytest.mark.asyncio
    async def test_works_with_no_existing_page_map(self):
        import pagemap.server as srv

        srv._last_page_map = None
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await navigate_back()

        assert "Navigated back" in result
