"""Tests for affordance-action mismatch validation in execute_action.

Verifies that execute_action returns a descriptive warning when the requested
action is incompatible with the target element's affordance, instead of
letting Playwright error out.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from pagemap import Interactable, PageMap
from pagemap.server import (
    ACTION_AFFORDANCE_COMPAT,
    VALID_ACTIONS,
    execute_action,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
    """Create a PageMap with elements of each affordance type."""
    return PageMap(
        url=url,
        title="Test Page",
        page_type="unknown",
        interactables=[
            Interactable(ref=1, role="button", name="Submit", affordance="click", region="main", tier=1),
            Interactable(ref=2, role="link", name="Home", affordance="click", region="navigation", tier=1),
            Interactable(ref=3, role="textbox", name="Search", affordance="type", region="main", tier=1),
            Interactable(ref=4, role="searchbox", name="Query", affordance="type", region="main", tier=1),
            Interactable(ref=5, role="combobox", name="Sort by", affordance="select", region="main", tier=1),
            Interactable(ref=6, role="listbox", name="Category", affordance="select", region="main", tier=1),
            Interactable(ref=7, role="checkbox", name="Agree", affordance="click", region="main", tier=1),
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


# ── TestAffordanceMismatchBlocked ────────────────────────────────────


class TestAffordanceMismatchBlocked:
    """Actions that SHOULD be blocked due to affordance mismatch."""

    async def test_type_on_button_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")

        data = json.loads(result)
        assert "Cannot type" in data["error"]
        assert "[1]" in data["error"]
        assert "affordance=click" in data["error"]

    async def test_type_on_link_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Cannot type" in data["error"]
        assert "[2]" in data["error"]

    async def test_type_on_checkbox_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=7, action="type", value="hello")

        data = json.loads(result)
        assert "Cannot type" in data["error"]
        assert "[7]" in data["error"]
        assert "affordance=click" in data["error"]

    async def test_type_on_combobox_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=5, action="type", value="hello")

        data = json.loads(result)
        assert "Cannot type" in data["error"]
        assert "[5]" in data["error"]
        assert "affordance=select" in data["error"]

    async def test_type_on_listbox_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=6, action="type", value="hello")

        data = json.loads(result)
        assert "Cannot type" in data["error"]
        assert "[6]" in data["error"]
        assert "affordance=select" in data["error"]

    async def test_select_on_button_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="select", value="opt1")

        data = json.loads(result)
        assert "Cannot select" in data["error"]
        assert "[1]" in data["error"]
        assert "affordance=click" in data["error"]

    async def test_select_on_link_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=2, action="select", value="opt1")

        data = json.loads(result)
        assert "Cannot select" in data["error"]
        assert "[2]" in data["error"]

    async def test_select_on_textbox_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=3, action="select", value="opt1")

        data = json.loads(result)
        assert "Cannot select" in data["error"]
        assert "[3]" in data["error"]
        assert "affordance=type" in data["error"]

    async def test_select_on_searchbox_blocked(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=4, action="select", value="opt1")

        data = json.loads(result)
        assert "Cannot select" in data["error"]
        assert "[4]" in data["error"]
        assert "affordance=type" in data["error"]


# ── TestAffordanceCompatibleAllowed ──────────────────────────────────


class TestAffordanceCompatibleAllowed:
    """Actions that SHOULD be allowed through (compatible affordances)."""

    async def test_click_on_button_allowed(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert "error" not in data

    async def test_click_on_textbox_allowed(self):
        """click is universal — works on type-affordance elements too."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="click")

        data = json.loads(result)
        assert "Clicked [3]" in data["description"]
        assert "error" not in data

    async def test_click_on_combobox_allowed(self):
        """click is universal — works on select-affordance elements too."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=5, action="click")

        data = json.loads(result)
        assert "Clicked [5]" in data["description"]
        assert "error" not in data

    async def test_type_on_textbox_allowed(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [3]" in data["description"]
        assert "error" not in data

    async def test_type_on_searchbox_allowed(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=4, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [4]" in data["description"]
        assert "error" not in data

    async def test_select_on_combobox_allowed(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=5, action="select", value="opt1")

        data = json.loads(result)
        assert "Selected option in [5]" in data["description"]
        assert "error" not in data

    async def test_select_on_listbox_allowed(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=6, action="select", value="opt1")

        data = json.loads(result)
        assert "Selected option in [6]" in data["description"]
        assert "error" not in data

    async def test_press_key_skips_affordance_check(self):
        """press_key is a global keyboard action — no affordance check."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        data = json.loads(result)
        assert "Pressed key" in data["description"]
        assert "error" not in data


# ── TestAffordanceErrorMessageFormat ─────────────────────────────────


class TestAffordanceErrorMessageFormat:
    """Verify error message structure contains all required information."""

    async def test_error_is_json_with_error_key(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert "error" in data

    async def test_error_contains_ref_number(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert "[1]" in data["error"]

    async def test_error_contains_element_role(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert "button" in data["error"]

    async def test_error_contains_element_name(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert "Submit" in data["error"]

    async def test_error_contains_affordance(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert "affordance=click" in data["error"]

    async def test_error_contains_suggestion(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert 'action="click"' in data["error"]


# ── TestAffordanceConstants ──────────────────────────────────────────


class TestAffordanceConstants:
    """Verify constant definitions and integration."""

    def test_click_compatible_with_all(self):
        assert ACTION_AFFORDANCE_COMPAT["click"] is None

    def test_type_only_type_affordance(self):
        assert ACTION_AFFORDANCE_COMPAT["type"] == frozenset({"type"})

    def test_select_only_select_affordance(self):
        assert ACTION_AFFORDANCE_COMPAT["select"] == frozenset({"select"})

    def test_press_key_no_check(self):
        assert ACTION_AFFORDANCE_COMPAT["press_key"] is None

    def test_all_valid_actions_have_compat_entry(self):
        for action in VALID_ACTIONS:
            assert action in ACTION_AFFORDANCE_COMPAT, f"{action} missing from ACTION_AFFORDANCE_COMPAT"

    async def test_mismatch_returns_early_no_playwright_call(self):
        """Affordance mismatch returns before reaching Playwright dispatch."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)

        with patch("pagemap.server._get_session") as get_sess:
            result = await execute_action(ref=1, action="type", value="hello")

        data = json.loads(result)
        assert "error" in data
        get_sess.assert_not_called()
