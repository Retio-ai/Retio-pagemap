"""Tests for the hover action in execute_action.

Verifies:
1. Constants: VALID_ACTIONS, ACTION_AFFORDANCE_COMPAT include hover
2. Basic hover on various affordance elements
3. DOM change detection after hover (dropdowns/tooltips)
4. Retry on transient errors (not visible, intercept)
5. CSS selector fallback on ambiguous role
6. Browser death handling
7. Timeout handling
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap
from pagemap.dom_change_detector import DomFingerprint
from pagemap.server import (
    ACTION_AFFORDANCE_COMPAT,
    VALID_ACTIONS,
    _is_retryable_error,
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
                name="Menu",
                affordance="click",
                region="main",
                tier=1,
                selector="#menu-btn",
            ),
            Interactable(
                ref=2,
                role="link",
                name="Products",
                affordance="click",
                region="nav",
                tier=1,
                selector="a.products",
            ),
            Interactable(
                ref=3,
                role="textbox",
                name="Search",
                affordance="type",
                region="main",
                tier=1,
                selector="input.search",
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(current_url: str = "https://example.com") -> MagicMock:
    """Create a mock BrowserSession."""
    session = MagicMock()
    session.get_page_url = AsyncMock(return_value=current_url)
    session.consume_new_page = MagicMock(return_value=None)
    session.drain_dialogs = MagicMock(return_value=[])

    locator = AsyncMock()
    locator.first = AsyncMock()
    locator.first.click = AsyncMock()
    locator.first.hover = AsyncMock()
    locator.first.fill = AsyncMock()
    locator.first.select_option = AsyncMock()
    locator.count = AsyncMock(return_value=1)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=locator)
    page.locator = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    type(page).url = PropertyMock(return_value=current_url)

    session.page = page
    return session


def _fp(
    *,
    total_interactives: int = 10,
    has_dialog: bool = False,
    body_child_count: int = 5,
    title: str = "Test Page",
) -> DomFingerprint:
    return DomFingerprint(
        interactive_counts={"button": total_interactives},
        total_interactives=total_interactives,
        has_dialog=has_dialog,
        body_child_count=body_child_count,
        title=title,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._state.cache.invalidate_all()
    yield
    srv._state.cache.invalidate_all()


# ── TestHoverConstants ──────────────────────────────────────────────


class TestHoverConstants:
    """Verify hover is included in action constants."""

    def test_hover_in_valid_actions(self):
        assert "hover" in VALID_ACTIONS

    def test_hover_in_affordance_compat(self):
        assert "hover" in ACTION_AFFORDANCE_COMPAT

    def test_hover_compat_is_none(self):
        """hover should work on any element (affordance=None)."""
        assert ACTION_AFFORDANCE_COMPAT["hover"] is None

    def test_hover_retryable_like_click(self):
        """hover uses click-safe retry patterns (not visible, not stable, intercept)."""
        exc_visible = PlaywrightError("Element is not visible")
        assert _is_retryable_error(exc_visible, "hover") is True

        exc_intercept = PlaywrightError("Element is intercepted by another element")
        assert _is_retryable_error(exc_intercept, "hover") is True

        exc_stable = PlaywrightError("Element is not stable")
        assert _is_retryable_error(exc_stable, "hover") is True

    def test_hover_not_retryable_on_timeout(self):
        """Timeout is NOT retryable for hover (same as click)."""
        exc = PlaywrightError("Timeout 5000ms exceeded")
        assert _is_retryable_error(exc, "hover") is False

    def test_hover_not_retryable_on_detached(self):
        """Detached is NOT retryable for hover (same as click)."""
        exc = PlaywrightError("Element is detached from the DOM")
        assert _is_retryable_error(exc, "hover") is False


# ── TestHoverBasic ──────────────────────────────────────────────────


class TestHoverBasic:
    """Basic hover action tests."""

    @pytest.mark.asyncio
    async def test_hover_button_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1] button: Menu" in data["description"]
        mock_session.page.get_by_role.return_value.first.hover.assert_called_once()

    @pytest.mark.asyncio
    async def test_hover_link_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="hover")

        data = json.loads(result)
        assert "Hovered over [2] link: Products" in data["description"]

    @pytest.mark.asyncio
    async def test_hover_on_type_affordance(self):
        """hover should work on any affordance including type."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="hover")

        data = json.loads(result)
        assert "Hovered over [3] textbox: Search" in data["description"]

    @pytest.mark.asyncio
    async def test_hover_settle_time_500ms(self):
        """hover uses 500ms settle time for CSS transitions."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await execute_action(ref=1, action="hover")

        mock_session.page.wait_for_timeout.assert_called_with(500)

    @pytest.mark.asyncio
    async def test_hover_no_value_required(self):
        """hover does not require a value parameter."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover", value=None)

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]

    @pytest.mark.asyncio
    async def test_hover_no_page_map(self):
        """hover without page map returns error."""
        result = await execute_action(ref=1, action="hover")
        data = json.loads(result)
        assert "No active Page Map" in data["error"]

    @pytest.mark.asyncio
    async def test_hover_invalid_ref(self):
        """hover on non-existent ref returns error."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=999, action="hover")
        data = json.loads(result)
        assert "ref [999] not found" in data["error"]


# ── TestHoverDomDetection ──────────────────────────────────────────


class TestHoverDomDetection:
    """Verify DOM change detection works with hover."""

    @pytest.mark.asyncio
    async def test_hover_major_dom_change_clears_page_map(self):
        """hover that opens dropdown → major DOM change → refs expired."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        pre = _fp(has_dialog=False)
        post = _fp(has_dialog=True)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[pre, post]),
        ):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert data["change"] == "major"
        assert data["refs_expired"] is True
        assert srv._state.cache.active is None

    @pytest.mark.asyncio
    async def test_hover_minor_dom_change_preserves_page_map(self):
        """hover causes small change → minor warning, page map kept."""
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()

        pre = _fp(total_interactives=100)
        post = _fp(total_interactives=101)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[pre, post]),
        ):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert data["change"] == "minor"
        assert data["refs_expired"] is False
        assert srv._state.cache.active is page_map

    @pytest.mark.asyncio
    async def test_hover_no_dom_change(self):
        """hover with no DOM change → no warning."""
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()

        fp = _fp()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[fp, fp]),
        ):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert data["change"] == "none"
        assert data["refs_expired"] is False
        assert srv._state.cache.active is page_map


# ── TestHoverRetry ──────────────────────────────────────────────────


class TestHoverRetry:
    """Verify retry behavior for hover action."""

    @pytest.mark.asyncio
    async def test_hover_not_visible_retried(self):
        """'not visible' error on first attempt → retry succeeds."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.hover = AsyncMock(side_effect=[PlaywrightError("Element is not visible"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert locator.first.hover.call_count == 2

    @pytest.mark.asyncio
    async def test_hover_intercept_retried(self):
        """'intercept' error on first attempt → retry succeeds."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.hover = AsyncMock(
            side_effect=[PlaywrightError("Element is intercepted by another element"), None]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert locator.first.hover.call_count == 2


# ── TestHoverCssFallback ──────────────────────────────────────────


class TestHoverCssFallback:
    """Verify CSS selector fallback for hover."""

    @pytest.mark.asyncio
    async def test_hover_ambiguous_role_uses_css(self):
        """Multiple role matches → fallback to CSS selector."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        role_locator = MagicMock()
        role_locator.count = AsyncMock(return_value=3)

        css_locator = AsyncMock()
        css_locator.count = AsyncMock(return_value=1)
        css_locator.first = AsyncMock()
        css_locator.first.hover = AsyncMock()

        page.get_by_role = MagicMock(return_value=role_locator)
        page.locator = MagicMock(return_value=css_locator)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert "CSS selector" in data["description"]


# ── TestHoverBrowserDead ──────────────────────────────────────────


class TestHoverBrowserDead:
    """Browser death during hover → recovery message."""

    @pytest.mark.asyncio
    async def test_hover_target_closed(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.hover = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Browser connection lost" in data["error"]
        assert srv._state.cache.active is None

    @pytest.mark.asyncio
    async def test_hover_browser_disconnected(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.hover = AsyncMock(side_effect=PlaywrightError("Browser disconnected"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Browser connection lost" in data["error"]
        assert srv._state.cache.active is None


# ── TestHoverTimeout ──────────────────────────────────────────────


class TestHoverTimeout:
    """Overall timeout handling for hover."""

    @pytest.mark.asyncio
    async def test_hover_overall_timeout(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        locator.first.hover = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.EXECUTE_ACTION_TIMEOUT_SECONDS", 0.1),
        ):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "timed out" in data["error"]
        assert srv._state.cache.active is None


# ── TestHoverDialogWarning ──────────────────────────────────────────


class TestHoverDialogWarning:
    """Dialog warnings included in hover responses."""

    @pytest.mark.asyncio
    async def test_hover_with_dialog_warning(self):
        import pagemap.server as srv
        from pagemap.browser_session import DialogInfo

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="alert", message="Welcome!", dismissed=False)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="hover")

        data = json.loads(result)
        assert "Hovered over [1]" in data["description"]
        assert len(data["dialogs"]) == 1
        assert data["dialogs"][0]["message"] == "Welcome!"
