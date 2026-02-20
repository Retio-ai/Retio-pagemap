"""Tests for structured JSON action result responses.

Tests:
- _build_action_result / _build_action_error helper functions
- execute_action JSON response format for each change type
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.browser_session import DialogInfo
from pagemap.dom_change_detector import DomFingerprint
from pagemap.server import (
    _build_action_error,
    _build_action_result,
    execute_action,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
    return PageMap(
        url=url,
        title="Test Page",
        page_type="unknown",
        interactables=[
            Interactable(ref=1, role="button", name="Submit", affordance="click", region="main", tier=1),
            Interactable(ref=2, role="textbox", name="Search", affordance="type", region="main", tier=1),
            Interactable(ref=3, role="combobox", name="Sort by", affordance="select", region="main", tier=1),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(current_url: str = "https://example.com") -> MagicMock:
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
    page.locator = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

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
    import pagemap.server as srv

    srv._state.cache.invalidate_all()
    yield
    srv._state.cache.invalidate_all()


# ── TestBuildActionResult ────────────────────────────────────────────


class TestBuildActionResult:
    """Unit tests for _build_action_result helper."""

    def test_basic_success_json(self):
        result = _build_action_result(
            description="Clicked [1] button: Submit",
            current_url="https://example.com",
            change="none",
            refs_expired=False,
        )
        data = json.loads(result)
        assert data["description"] == "Clicked [1] button: Submit"
        assert data["current_url"] == "https://example.com"
        assert data["change"] == "none"
        assert data["refs_expired"] is False

    def test_navigation_result(self):
        result = _build_action_result(
            description="Clicked [1] link: Next",
            current_url="https://example.com/page2",
            change="navigation",
            refs_expired=True,
            change_details=["Navigated from https://example.com"],
        )
        data = json.loads(result)
        assert data["change"] == "navigation"
        assert data["refs_expired"] is True
        assert "Navigated from" in data["change_details"][0]

    def test_empty_change_details_omitted(self):
        result = _build_action_result(
            description="test",
            current_url="https://example.com",
            change="none",
            refs_expired=False,
            change_details=None,
        )
        data = json.loads(result)
        assert "change_details" not in data

    def test_empty_dialogs_omitted(self):
        result = _build_action_result(
            description="test",
            current_url="https://example.com",
            change="none",
            refs_expired=False,
            dialogs=None,
        )
        data = json.loads(result)
        assert "dialogs" not in data

    def test_dialogs_included(self):
        dialogs = [DialogInfo(dialog_type="alert", message="Hello!", dismissed=False)]
        result = _build_action_result(
            description="test",
            current_url="https://example.com",
            change="none",
            refs_expired=False,
            dialogs=dialogs,
        )
        data = json.loads(result)
        assert len(data["dialogs"]) == 1
        assert data["dialogs"][0]["type"] == "alert"
        assert data["dialogs"][0]["message"] == "Hello!"
        assert data["dialogs"][0]["action"] == "accepted"

    def test_dialog_dismissed(self):
        dialogs = [DialogInfo(dialog_type="confirm", message="Sure?", dismissed=True)]
        result = _build_action_result(
            description="test",
            current_url="https://example.com",
            change="none",
            refs_expired=False,
            dialogs=dialogs,
        )
        data = json.loads(result)
        assert data["dialogs"][0]["action"] == "dismissed"

    def test_korean_text_preserved(self):
        result = _build_action_result(
            description="Clicked [1] button: 장바구니 담기",
            current_url="https://example.com",
            change="none",
            refs_expired=False,
        )
        data = json.loads(result)
        assert "장바구니 담기" in data["description"]

    def test_new_tab_change(self):
        result = _build_action_result(
            description="Clicked [1] button: Open",
            current_url="https://newsite.com",
            change="new_tab",
            refs_expired=True,
            change_details=["New tab opened: https://newsite.com"],
        )
        data = json.loads(result)
        assert data["change"] == "new_tab"
        assert data["refs_expired"] is True

    def test_major_change(self):
        result = _build_action_result(
            description="Clicked [1] button: Submit",
            current_url="https://example.com",
            change="major",
            refs_expired=True,
            change_details=["Page content changed (dialog appeared)"],
        )
        data = json.loads(result)
        assert data["change"] == "major"
        assert data["refs_expired"] is True


# ── TestBuildActionError ─────────────────────────────────────────────


class TestBuildActionError:
    """Unit tests for _build_action_error helper."""

    def test_basic_error_json(self):
        result = _build_action_error("ref [99] not found. Valid refs: 1-15")
        data = json.loads(result)
        assert data["error"] == "ref [99] not found. Valid refs: 1-15"
        assert data["refs_expired"] is False

    def test_error_with_refs_expired(self):
        result = _build_action_error("No active Page Map.", refs_expired=True)
        data = json.loads(result)
        assert data["refs_expired"] is True

    def test_error_has_only_two_keys(self):
        result = _build_action_error("test error")
        data = json.loads(result)
        assert set(data.keys()) == {"error", "refs_expired"}


# ── TestExecuteActionJsonReturn ──────────────────────────────────────


class TestExecuteActionJsonReturn:
    """Integration tests: execute_action returns valid JSON for each path."""

    @pytest.mark.asyncio
    async def test_validation_error_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="invalid_action")
        data = json.loads(result)
        assert "error" in data
        assert "Invalid action" in data["error"]

    @pytest.mark.asyncio
    async def test_no_page_map_error_is_json(self):
        result = await execute_action(ref=1, action="click")
        data = json.loads(result)
        assert "error" in data
        assert "No active Page Map" in data["error"]
        assert data["refs_expired"] is True

    @pytest.mark.asyncio
    async def test_ref_not_found_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=99, action="click")
        data = json.loads(result)
        assert "error" in data
        assert "ref [99] not found" in data["error"]

    @pytest.mark.asyncio
    async def test_affordance_mismatch_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value="hello")
        data = json.loads(result)
        assert "error" in data
        assert "Cannot type" in data["error"]

    @pytest.mark.asyncio
    async def test_click_no_change_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        fp = _fp()
        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[fp, fp]),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "none"
        assert data["refs_expired"] is False
        assert "Clicked [1]" in data["description"]

    @pytest.mark.asyncio
    async def test_click_navigation_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session(current_url="https://example.com/page2")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "navigation"
        assert data["refs_expired"] is True
        assert data["current_url"] == "https://example.com/page2"
        assert "Navigated from" in data["change_details"][0]

    @pytest.mark.asyncio
    async def test_click_dom_major_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        pre = _fp(has_dialog=False)
        post = _fp(has_dialog=True)
        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[pre, post]),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "major"
        assert data["refs_expired"] is True

    @pytest.mark.asyncio
    async def test_click_dom_minor_is_json(self):
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
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "minor"
        assert data["refs_expired"] is False

    @pytest.mark.asyncio
    async def test_type_action_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]

    @pytest.mark.asyncio
    async def test_select_action_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        data = json.loads(result)
        assert "Selected option in [3]" in data["description"]

    @pytest.mark.asyncio
    async def test_press_key_action_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        data = json.loads(result)
        assert "Pressed key" in data["description"]

    @pytest.mark.asyncio
    async def test_dialog_in_json_response(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[
                DialogInfo(dialog_type="alert", message="Welcome!", dismissed=False),
            ]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert len(data["dialogs"]) == 1
        assert data["dialogs"][0]["type"] == "alert"
        assert data["dialogs"][0]["message"] == "Welcome!"

    @pytest.mark.asyncio
    async def test_timeout_error_is_json(self):
        import asyncio

        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.page.get_by_role.return_value.first.click = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.EXECUTE_ACTION_TIMEOUT_SECONDS", 0.1),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "error" in data
        assert "timed out" in data["error"]
        assert data["refs_expired"] is True

    @pytest.mark.asyncio
    async def test_browser_dead_error_is_json(self):
        from playwright.async_api import Error as PlaywrightError

        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.page.get_by_role.return_value.first.click = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "error" in data
        assert "Browser connection lost" in data["error"]
        assert data["refs_expired"] is True

    @pytest.mark.asyncio
    async def test_value_required_error_is_json(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=2, action="type")
        data = json.loads(result)
        assert "error" in data
        assert "'value' parameter required" in data["error"]
