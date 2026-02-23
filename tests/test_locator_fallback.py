"""Tests for CSS selector fallback and duplicate role+name resolution.

Verifies:
1. _resolve_locator fallback chain: get_by_role -> CSS selector -> error
2. Duplicate role+name elements resolved via CSS selector
3. execute_action integration with fallback chain
4. Backward compatibility with Interactables lacking selector
5. Tier 3 batch selector storage
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.server import _resolve_locator, execute_action

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map_with_selectors(url: str = "https://example.com") -> PageMap:
    """Create a PageMap with elements that have CSS selectors."""
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
            Interactable(
                ref=4,
                role="button",
                name="Delete",
                affordance="click",
                region="main",
                tier=1,
                selector="#item-1 > button.delete",
            ),
            Interactable(
                ref=5,
                role="button",
                name="Delete",
                affordance="click",
                region="main",
                tier=1,
                selector="#item-2 > button.delete",
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_page_map_no_selectors(url: str = "https://example.com") -> PageMap:
    """Create a PageMap without CSS selectors (backward compat)."""
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
            Interactable(
                ref=2,
                role="textbox",
                name="Search",
                affordance="type",
                region="main",
                tier=1,
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(
    current_url: str = "https://example.com",
    role_count: int = 1,
    css_count: int = 1,
) -> MagicMock:
    """Create a mock BrowserSession with configurable locator counts."""
    session = MagicMock()
    session.get_page_url = AsyncMock(return_value=current_url)

    # Primary locator (from get_by_role)
    role_locator = AsyncMock()
    role_locator.first = AsyncMock()
    role_locator.first.click = AsyncMock()
    role_locator.first.fill = AsyncMock()
    role_locator.first.select_option = AsyncMock()
    role_locator.count = AsyncMock(return_value=role_count)

    # CSS fallback locator (from page.locator)
    css_locator = AsyncMock()
    css_locator.first = AsyncMock()
    css_locator.first.click = AsyncMock()
    css_locator.first.fill = AsyncMock()
    css_locator.first.select_option = AsyncMock()
    css_locator.count = AsyncMock(return_value=css_count)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=role_locator)
    page.locator = MagicMock(return_value=css_locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    session.page = page
    return session


# ── TestResolveLocatorUnit ───────────────────────────────────────────


class TestResolveLocatorUnit:
    """Unit tests for _resolve_locator(page, target) helper."""

    async def test_single_role_match_returns_role(self):
        """count=1 via get_by_role → returns role locator."""
        role_locator = AsyncMock()
        role_locator.count = AsyncMock(return_value=1)

        page = MagicMock()
        page.get_by_role = MagicMock(return_value=role_locator)

        target = Interactable(
            ref=1,
            role="button",
            name="Submit",
            affordance="click",
            region="main",
            tier=1,
            selector="#submit-btn",
        )

        locator, strategy = await _resolve_locator(page, target)

        assert strategy == "role"
        assert locator is role_locator
        page.get_by_role.assert_called_once_with("button", name="Submit", exact=True)

    async def test_zero_role_falls_to_css(self):
        """count=0 via get_by_role + selector available → CSS locator."""
        role_locator = AsyncMock()
        role_locator.count = AsyncMock(return_value=0)

        css_locator = AsyncMock()
        css_locator.count = AsyncMock(return_value=1)

        page = MagicMock()
        page.get_by_role = MagicMock(return_value=role_locator)
        page.locator = MagicMock(return_value=css_locator)

        target = Interactable(
            ref=1,
            role="button",
            name="Submit",
            affordance="click",
            region="main",
            tier=1,
            selector="#submit-btn",
        )

        locator, strategy = await _resolve_locator(page, target)

        assert strategy == "css"
        assert locator is css_locator
        page.locator.assert_called_once_with("#submit-btn")

    async def test_multiple_role_falls_to_css(self):
        """count=3 via get_by_role + selector available → CSS locator."""
        role_locator = AsyncMock()
        role_locator.count = AsyncMock(return_value=3)

        css_locator = AsyncMock()
        css_locator.count = AsyncMock(return_value=1)

        page = MagicMock()
        page.get_by_role = MagicMock(return_value=role_locator)
        page.locator = MagicMock(return_value=css_locator)

        target = Interactable(
            ref=1,
            role="button",
            name="Delete",
            affordance="click",
            region="main",
            tier=1,
            selector="#row-1 .delete-btn",
        )

        locator, strategy = await _resolve_locator(page, target)

        assert strategy == "css"
        page.locator.assert_called_once_with("#row-1 .delete-btn")

    async def test_multiple_role_no_css_returns_role_with_warning(self):
        """count=3, no selector → returns role locator (degraded)."""
        role_locator = AsyncMock()
        role_locator.count = AsyncMock(return_value=3)

        page = MagicMock()
        page.get_by_role = MagicMock(return_value=role_locator)

        target = Interactable(
            ref=1,
            role="button",
            name="Delete",
            affordance="click",
            region="main",
            tier=1,
        )

        locator, strategy = await _resolve_locator(page, target)

        assert strategy == "role"
        # page.locator should NOT be called (no selector)
        page.locator.assert_not_called()

    async def test_all_fail_raises_valueerror(self):
        """count=0, no selector → ValueError."""
        role_locator = AsyncMock()
        role_locator.count = AsyncMock(return_value=0)

        page = MagicMock()
        page.get_by_role = MagicMock(return_value=role_locator)

        target = Interactable(
            ref=1,
            role="button",
            name="Submit",
            affordance="click",
            region="main",
            tier=1,
        )

        with pytest.raises(ValueError, match="Could not locate"):
            await _resolve_locator(page, target)

    async def test_empty_name_skips_role_goes_to_css(self):
        """Empty name → skip get_by_role entirely, use CSS."""
        css_locator = AsyncMock()
        css_locator.count = AsyncMock(return_value=1)

        page = MagicMock()
        page.locator = MagicMock(return_value=css_locator)

        target = Interactable(
            ref=1,
            role="button",
            name="",
            affordance="click",
            region="main",
            tier=2,
            selector="button:nth-child(3)",
        )

        locator, strategy = await _resolve_locator(page, target)

        assert strategy == "css"
        page.get_by_role.assert_not_called()


# ── TestFallbackHappyPath ────────────────────────────────────────────


class TestFallbackHappyPath:
    """get_by_role succeeds → normal path, CSS not attempted."""

    async def test_click_role_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert "error" not in data
        assert "CSS selector" not in data["description"]
        mock_session.page.locator.assert_not_called()

    async def test_type_role_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert "CSS selector" not in data["description"]
        mock_session.page.locator.assert_not_called()

    async def test_select_role_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        data = json.loads(result)
        assert "Selected option in [3]" in data["description"]
        assert "CSS selector" not in data["description"]
        mock_session.page.locator.assert_not_called()


# ── TestFallbackToCSS ────────────────────────────────────────────────


class TestFallbackToCSS:
    """get_by_role count=0, CSS selector succeeds → fallback works."""

    async def test_click_falls_back_to_css(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert "CSS selector" in data["description"]
        mock_session.page.locator.assert_called_once_with("#submit-btn")

    async def test_type_falls_back_to_css(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="query")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert "CSS selector" in data["description"]
        mock_session.page.locator.assert_called_once_with("input.search-box")

    async def test_select_falls_back_to_css(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        data = json.loads(result)
        assert "Selected option in [3]" in data["description"]
        assert "CSS selector" in data["description"]
        mock_session.page.locator.assert_called_once_with("select.sort-dropdown")


# ── TestDuplicateResolution ──────────────────────────────────────────


class TestDuplicateResolution:
    """Multiple elements with same role+name resolved via CSS selector."""

    async def test_duplicate_buttons_first_uses_css(self):
        """Two 'Delete' buttons → ref 4 uses its specific CSS selector."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=2)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=4, action="click")

        data = json.loads(result)
        assert "Clicked [4]" in data["description"]
        assert "CSS selector" in data["description"]
        mock_session.page.locator.assert_called_once_with("#item-1 > button.delete")

    async def test_duplicate_buttons_second_uses_css(self):
        """Two 'Delete' buttons → ref 5 uses its specific CSS selector."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=2)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=5, action="click")

        data = json.loads(result)
        assert "Clicked [5]" in data["description"]
        assert "CSS selector" in data["description"]
        mock_session.page.locator.assert_called_once_with("#item-2 > button.delete")

    async def test_result_includes_css_note(self):
        """Fallback to CSS → result includes note."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "CSS selector" in data["description"]

    async def test_no_css_note_on_role_match(self):
        """Direct role match → no CSS note in result."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session(role_count=1)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "CSS selector" not in data["description"]


# ── TestAllFail ──────────────────────────────────────────────────────


class TestAllFail:
    """Both get_by_role and CSS selector fail → descriptive error."""

    async def test_both_fail_returns_error(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_no_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "error" in data

    async def test_error_suggests_refresh(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_no_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "get_page_map" in data["error"]

    async def test_no_selector_skips_css_fallback(self):
        """When selector is empty, page.locator is never called."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_no_selectors(), None)
        mock_session = _make_mock_session(role_count=0)

        with patch("pagemap.server._get_session", return_value=mock_session):
            await execute_action(ref=1, action="click")

        mock_session.page.locator.assert_not_called()


# ── TestBackwardCompat ───────────────────────────────────────────────


class TestBackwardCompat:
    """Existing Interactables without selector field still work."""

    def test_interactable_default_selector_empty(self):
        item = Interactable(
            ref=1,
            role="button",
            name="OK",
            affordance="click",
            region="main",
            tier=1,
        )
        assert item.selector == ""

    async def test_execute_action_works_without_selector(self):
        """PageMap from pre-Phase-2 code works normally."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_no_selectors(), None)
        mock_session = _make_mock_session(role_count=1)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert "error" not in data

    async def test_press_key_unaffected(self):
        """press_key action ignores selector field entirely."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map_with_selectors(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        data = json.loads(result)
        assert "Pressed key" in data["description"]
        mock_session.page.locator.assert_not_called()
        mock_session.page.get_by_role.assert_not_called()


# ── TestSelectorStorage ──────────────────────────────────────────────


class TestSelectorStorage:
    """Tests for CSS selector storage in Tier 3 batch processing."""

    def test_tier3_batch_stores_selector(self):
        from pagemap.interactive_detector import _process_tier3_batch

        elements = [
            {
                "tag": "div",
                "role": "div",
                "name": "Custom Button",
                "textFallback": "",
                "cssSelector": "div.custom-btn:nth-child(3)",
            }
        ]
        result = _process_tier3_batch(elements, set(), 0)
        assert len(result) == 1
        assert result[0].selector == "div.custom-btn:nth-child(3)"

    def test_tier3_batch_missing_selector_defaults_empty(self):
        from pagemap.interactive_detector import _process_tier3_batch

        elements = [
            {
                "tag": "div",
                "role": "div",
                "name": "Button",
                "textFallback": "",
            }
        ]
        result = _process_tier3_batch(elements, set(), 0)
        assert len(result) == 1
        assert result[0].selector == ""

    def test_interactable_str_excludes_selector(self):
        """__str__ should not leak CSS selectors."""
        item = Interactable(
            ref=1,
            role="button",
            name="OK",
            affordance="click",
            region="main",
            tier=1,
            selector="#ok-btn",
        )
        s = str(item)
        assert "#ok-btn" not in s
