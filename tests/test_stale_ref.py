"""Tests for stale ref detection in execute_action.

Verifies that execute_action detects page navigation and warns the agent
to refresh the page map, preventing stale ref usage.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
    session.consume_new_page = MagicMock(return_value=None)
    session.drain_dialogs = MagicMock(return_value=[])

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


class TestStaleRefDetection:
    """Tests for navigation detection after action execution."""

    async def test_click_url_changed_warns_and_clears(self):
        """click that causes navigation → JSON change=navigation + _last_page_map = None."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._state.cache.store(page_map, None)
        new_url = "https://example.com/page2"
        mock_session = _make_mock_session(current_url=new_url)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert data["current_url"] == new_url
        assert data["change"] == "navigation"
        assert data["refs_expired"] is True
        assert srv._state.cache.active is None

    async def test_click_url_same_no_warning(self):
        """click without navigation → no warning, _last_page_map preserved."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session(current_url="https://example.com")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert data["change"] != "navigation"
        assert srv._state.cache.active is page_map

    async def test_type_url_changed_warns(self):
        """type action that triggers navigation → JSON change=navigation."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session(current_url="https://example.com/search?q=test")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="test query")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert data["change"] == "navigation"
        assert srv._state.cache.active is None

    async def test_select_url_changed_warns(self):
        """select action that triggers navigation → JSON change=navigation."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session(current_url="https://example.com/sorted")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        data = json.loads(result)
        assert "Selected option in [3]" in data["description"]
        assert data["change"] == "navigation"
        assert srv._state.cache.active is None

    async def test_press_key_url_changed_warns(self):
        """press_key (Enter) that triggers navigation → JSON change=navigation."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session(current_url="https://example.com/submitted")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        data = json.loads(result)
        assert "Pressed key 'Enter'" in data["description"]
        assert data["change"] == "navigation"
        assert srv._state.cache.active is None

    async def test_press_key_url_same_no_warning(self):
        """press_key without navigation → no warning."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session(current_url="https://example.com")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Tab")

        data = json.loads(result)
        assert "Pressed key 'Tab'" in data["description"]
        assert data["change"] != "navigation"
        assert srv._state.cache.active is not None


class TestNoActivePageMap:
    """Tests for improved error message when _last_page_map is None."""

    async def test_no_page_map_returns_improved_error(self):
        """execute_action with no page map → JSON error."""
        import pagemap.server as srv

        srv._state.cache.invalidate_all()

        result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "No active Page Map" in data["error"]
        assert "get_page_map" in data["error"]

    async def test_after_navigation_clears_then_error(self):
        """After navigation clears page map, next call gets JSON error."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session(current_url="https://example.com/page2")

        with patch("pagemap.server._get_session", return_value=mock_session):
            # First call: navigation detected, clears page map
            result1 = await execute_action(ref=1, action="click")
            data1 = json.loads(result1)
            assert data1["change"] == "navigation"
            assert srv._state.cache.active is None

            # Second call: no page map → JSON error
            result2 = await execute_action(ref=1, action="click")
            data2 = json.loads(result2)
            assert "No active Page Map" in data2["error"]
            assert "may have navigated" in data2["error"]


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

    async def test_click_dom_major_change_warns_and_clears(self):
        """click causes dialog appearance → JSON change=major, _last_page_map cleared."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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

        data = json.loads(result)
        assert data["change"] == "major"
        assert data["refs_expired"] is True
        assert any("dialog appeared" in d for d in data.get("change_details", []))
        assert srv._state.cache.active is None

    async def test_click_dom_minor_change_warns_preserves(self):
        """click causes small interactive change → JSON change=minor, page map kept."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._state.cache.store(page_map, None)
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

        data = json.loads(result)
        assert data["change"] == "minor"
        assert data["refs_expired"] is False
        assert srv._state.cache.active is page_map

    async def test_click_dom_no_change_no_warning(self):
        """click with identical DOM → JSON change=none."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._state.cache.store(page_map, None)
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

        data = json.loads(result)
        assert data["change"] == "none"
        assert data["refs_expired"] is False
        assert srv._state.cache.active is page_map

    async def test_press_key_dom_major_change_warns(self):
        """press_key (Escape) causes DOM change → JSON change=major."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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

        data = json.loads(result)
        assert data["change"] == "major"
        assert data["refs_expired"] is True
        assert any("title changed" in d for d in data.get("change_details", []))
        assert srv._state.cache.active is None

    async def test_type_dom_major_change_warns(self):
        """type action causes large DOM change → JSON change=major."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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

        data = json.loads(result)
        assert data["change"] == "major"
        assert srv._state.cache.active is None

    async def test_url_change_takes_precedence(self):
        """URL change → JSON change=navigation (DOM check not run)."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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

        data = json.loads(result)
        assert data["change"] == "navigation"
        # capture called for pre only; post is in else branch (not reached)
        assert mock_capture.call_count == 1

    async def test_dom_fingerprint_failure_graceful(self):
        """capture failure → URL-only fallback, no DOM warning."""
        import pagemap.server as srv

        page_map = _make_page_map("https://example.com")
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session(current_url="https://example.com")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                return_value=None,
            ),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "none"
        assert srv._state.cache.active is page_map

    async def test_pre_fingerprint_none_skips_post_capture(self):
        """pre=None → post capture not called."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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

    async def test_dom_change_clears_then_second_call_errors(self):
        """DOM change clears page map → next call gets JSON error."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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
            data1 = json.loads(result1)
            assert data1["change"] == "major"
            assert srv._state.cache.active is None

            result2 = await execute_action(ref=1, action="click")
            data2 = json.loads(result2)
            assert "No active Page Map" in data2["error"]

    async def test_mock_call_count_verification(self):
        """Verify capture is called exactly twice (pre + post) on same-URL action."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
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
