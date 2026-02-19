"""Tests for stale ref detection in execute_action.

Verifies that execute_action detects page navigation and warns the agent
to refresh the page map, preventing stale ref usage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.dom_change_detector import DomFingerprint
from pagemap.server import execute_action


def _make_page_map(url: str = "https://example.com") -> PageMap:
    """Create a minimal PageMap for testing."""
    return PageMap(
        url=url,
        title="Test Page",
        page_type="unknown",
        interactables=[
            Interactable(
                ref=1,
                role="link",
                name="Next Page",
                affordance="click",
                region="main",
                tier=1,
            ),
            Interactable(
                ref=2,
                role="textbox",
                name="Search",
                affordance="type",
                region="main",
                tier=1,
            ),
            Interactable(
                ref=3,
                role="combobox",
                name="Sort by",
                affordance="select",
                region="main",
                tier=1,
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

    locator = AsyncMock()
    locator.first = AsyncMock()
    locator.first.click = AsyncMock()
    locator.first.fill = AsyncMock()
    locator.first.select_option = AsyncMock()
    locator.count = AsyncMock(return_value=1)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    session.page = page
    return session


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._last_page_map = None
    yield
    srv._last_page_map = None


class TestStaleRefDetection:
    """Tests for navigation detection after action execution."""

    @pytest.mark.asyncio
    async def test_click_url_changed_warns_and_clears(self):
        """click that causes navigation → warning + _last_page_map = None."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._last_page_map = page_map
        new_url = "https://example.com/page2"
        mock_session = _make_mock_session(current_url=new_url)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Clicked [1]" in result
        assert new_url in result
        assert "Page navigated" in result
        assert "get_page_map" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_click_url_same_no_warning(self):
        """click without navigation → no warning, _last_page_map preserved."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._last_page_map = page_map
        mock_session = _make_mock_session(current_url="https://example.com")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Clicked [1]" in result
        assert "Page navigated" not in result
        assert srv._last_page_map is page_map

    @pytest.mark.asyncio
    async def test_type_url_changed_warns(self):
        """type action that triggers navigation → warning."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com/search?q=test")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="test query")

        assert "Typed into [2]" in result
        assert "Page navigated" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_select_url_changed_warns(self):
        """select action that triggers navigation → warning."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com/sorted")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        assert "Selected option in [3]" in result
        assert "Page navigated" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_press_key_url_changed_warns(self):
        """press_key (Enter) that triggers navigation → warning."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com/submitted")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        assert "Pressed key 'Enter'" in result
        assert "Page navigated" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_press_key_url_same_no_warning(self):
        """press_key without navigation → no warning."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Tab")

        assert "Pressed key 'Tab'" in result
        assert "Page navigated" not in result
        assert srv._last_page_map is not None


class TestNoActivePageMap:
    """Tests for improved error message when _last_page_map is None."""

    @pytest.mark.asyncio
    async def test_no_page_map_returns_improved_error(self):
        """execute_action with no page map → descriptive error."""
        import pagemap.server as srv

        srv._last_page_map = None

        result = await execute_action(ref=1, action="click")

        assert "No active Page Map" in result
        assert "get_page_map" in result

    @pytest.mark.asyncio
    async def test_after_navigation_clears_then_error(self):
        """After navigation clears page map, next call gets improved error."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com/page2")

        with patch("pagemap.server._get_session", return_value=mock_session):
            # First call: navigation detected, clears page map
            result1 = await execute_action(ref=1, action="click")
            assert "Page navigated" in result1
            assert srv._last_page_map is None

            # Second call: no page map → improved error
            result2 = await execute_action(ref=1, action="click")
            assert "No active Page Map" in result2
            assert "may have navigated" in result2


# =========================================================================
# Helpers for DOM change detection tests
# =========================================================================


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


class TestDomChangeDetection:
    """Integration tests for DOM change detection in execute_action."""

    @pytest.mark.asyncio
    async def test_click_dom_major_change_warns_and_clears(self):
        """click causes dialog appearance → major warning, _last_page_map cleared."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        pre = _fp(has_dialog=False)
        post = _fp(has_dialog=True)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[pre, post],
            ),
        ):
            result = await execute_action(ref=1, action="click")

        assert "Page content changed" in result
        assert "dialog appeared" in result
        assert "get_page_map" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_click_dom_minor_change_warns_preserves(self):
        """click causes small interactive change → minor warning, page map kept."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._last_page_map = page_map
        mock_session = _make_mock_session(current_url="https://example.com")

        pre = _fp(total_interactives=100)
        post = _fp(total_interactives=101)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[pre, post],
            ),
        ):
            result = await execute_action(ref=1, action="click")

        assert "Page content updated" in result
        assert "Consider calling" in result
        assert srv._last_page_map is page_map

    @pytest.mark.asyncio
    async def test_click_dom_no_change_no_warning(self):
        """click with identical DOM → no warning."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._last_page_map = page_map
        mock_session = _make_mock_session(current_url="https://example.com")

        fp = _fp()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[fp, fp],
            ),
        ):
            result = await execute_action(ref=1, action="click")

        assert "Page content changed" not in result
        assert "Page content updated" not in result
        assert srv._last_page_map is page_map

    @pytest.mark.asyncio
    async def test_press_key_dom_major_change_warns(self):
        """press_key (Escape) causes DOM change → major warning."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        pre = _fp(title="Page - Modal")
        post = _fp(title="Page")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[pre, post],
            ),
        ):
            result = await execute_action(ref=1, action="press_key", value="Escape")

        assert "Page content changed" in result
        assert "title changed" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_type_dom_major_change_warns(self):
        """type action causes large DOM change → major warning."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        pre = _fp(total_interactives=10)
        post = _fp(total_interactives=20)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[pre, post],
            ),
        ):
            result = await execute_action(ref=2, action="type", value="search term")

        assert "Page content changed" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_url_change_takes_precedence(self):
        """URL change → navigation warning (DOM check not run)."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com/page2")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
            ) as mock_capture,
        ):
            # Pre-fingerprint is called once, but post should NOT be called
            mock_capture.return_value = _fp()
            result = await execute_action(ref=1, action="click")

        assert "Page navigated" in result
        # capture called for pre only; post is in else branch (not reached)
        assert mock_capture.call_count == 1

    @pytest.mark.asyncio
    async def test_dom_fingerprint_failure_graceful(self):
        """capture failure → URL-only fallback, no DOM warning."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._last_page_map = page_map
        mock_session = _make_mock_session(current_url="https://example.com")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                return_value=None,
            ),
        ):
            result = await execute_action(ref=1, action="click")

        assert "Page content changed" not in result
        assert "Page content updated" not in result
        assert srv._last_page_map is page_map

    @pytest.mark.asyncio
    async def test_pre_fingerprint_none_skips_post_capture(self):
        """pre=None → post capture not called."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                return_value=None,
            ) as mock_capture,
        ):
            await execute_action(ref=1, action="click")

        # Called once for pre (returned None), post not attempted
        assert mock_capture.call_count == 1

    @pytest.mark.asyncio
    async def test_dom_change_clears_then_second_call_errors(self):
        """DOM change clears page map → next call gets 'No active Page Map'."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        pre = _fp(has_dialog=False)
        post = _fp(has_dialog=True)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[pre, post],
            ),
        ):
            result1 = await execute_action(ref=1, action="click")
            assert "Page content changed" in result1
            assert srv._last_page_map is None

            result2 = await execute_action(ref=1, action="click")
            assert "No active Page Map" in result2

    @pytest.mark.asyncio
    async def test_mock_call_count_verification(self):
        """Verify capture is called exactly twice (pre + post) on same-URL action."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        fp = _fp()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                side_effect=[fp, fp],
            ) as mock_capture,
        ):
            await execute_action(ref=1, action="click")

        assert mock_capture.call_count == 2
