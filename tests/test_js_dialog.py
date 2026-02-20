"""Tests for JS dialog auto-handling (alert/confirm/prompt/beforeunload).

Tests:
- _on_dialog() behavior for each dialog type
- DialogInfo storage and buffer overflow
- drain_dialogs() returns and clears
- Handler registration on context
- Dialog warnings in execute_action output
- _format_dialog_warnings() formatting
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.browser_session import (
    _MAX_DIALOG_BUFFER,
    BrowserSession,
    DialogInfo,
)
from pagemap.server import _format_dialog_warnings, execute_action

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
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


def _make_dialog(dtype: str = "alert", message: str = "Hello") -> AsyncMock:
    dialog = AsyncMock()
    dialog.type = dtype
    dialog.message = message
    dialog.accept = AsyncMock()
    dialog.dismiss = AsyncMock()
    return dialog


@pytest.fixture(autouse=True)
def _reset_state():
    import pagemap.server as srv

    srv._state.cache.invalidate_all()
    yield
    srv._state.cache.invalidate_all()


# ── TestDialogHandlerBehavior ─────────────────────────────────────────


class TestDialogHandlerBehavior:
    """Test _on_dialog() calls accept/dismiss per dialog type."""

    @pytest.mark.asyncio
    async def test_alert_calls_accept(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []
        dialog = _make_dialog("alert", "Alert!")

        await session._on_dialog(dialog)

        dialog.accept.assert_awaited_once()
        dialog.dismiss.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_calls_dismiss(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []
        dialog = _make_dialog("confirm", "Are you sure?")

        await session._on_dialog(dialog)

        dialog.dismiss.assert_awaited_once()
        dialog.accept.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prompt_calls_dismiss(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []
        dialog = _make_dialog("prompt", "Enter value:")

        await session._on_dialog(dialog)

        dialog.dismiss.assert_awaited_once()
        dialog.accept.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_beforeunload_calls_accept(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []
        dialog = _make_dialog("beforeunload", "")

        await session._on_dialog(dialog)

        dialog.accept.assert_awaited_once()
        dialog.dismiss.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dialog_info_stored_correctly(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []
        dialog = _make_dialog("alert", "Test message")

        await session._on_dialog(dialog)

        assert len(session._pending_dialogs) == 1
        info = session._pending_dialogs[0]
        assert info.dialog_type == "alert"
        assert info.message == "Test message"
        assert info.dismissed is False  # alert → accept

    @pytest.mark.asyncio
    async def test_confirm_info_dismissed_true(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []
        dialog = _make_dialog("confirm", "OK?")

        await session._on_dialog(dialog)

        assert session._pending_dialogs[0].dismissed is True

    @pytest.mark.asyncio
    async def test_buffer_overflow_keeps_latest(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []

        for i in range(_MAX_DIALOG_BUFFER + 5):
            dialog = _make_dialog("alert", f"msg-{i}")
            await session._on_dialog(dialog)

        assert len(session._pending_dialogs) == _MAX_DIALOG_BUFFER
        # Oldest should be trimmed, latest should be present
        assert session._pending_dialogs[-1].message == f"msg-{_MAX_DIALOG_BUFFER + 4}"
        assert session._pending_dialogs[0].message == "msg-5"

    @pytest.mark.asyncio
    async def test_handler_exception_falls_back_to_dismiss(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []

        dialog = AsyncMock()
        dialog.type = "alert"
        dialog.message = "error"
        dialog.accept = AsyncMock(side_effect=RuntimeError("fail"))
        dialog.dismiss = AsyncMock()

        await session._on_dialog(dialog)

        dialog.dismiss.assert_awaited_once()
        assert len(session._pending_dialogs) == 0


# ── TestDrainDialogs ─────────────────────────────────────────────────


class TestDrainDialogs:
    def test_drain_returns_and_clears(self):
        session = BrowserSession.__new__(BrowserSession)
        info = DialogInfo(dialog_type="alert", message="Hi", dismissed=False)
        session._pending_dialogs = [info]

        result = session.drain_dialogs()

        assert result == [info]
        assert session._pending_dialogs == []

    def test_drain_empty_returns_empty(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_dialogs = []

        result = session.drain_dialogs()

        assert result == []


# ── TestDialogHandlerRegistration ─────────────────────────────────────


def _build_mock_chain():
    mock_page = AsyncMock()
    mock_page.route = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.route = AsyncMock()
    mock_context.on = MagicMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    mock_chromium = AsyncMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium

    mock_pw_cm = AsyncMock()
    mock_pw_cm.start = AsyncMock(return_value=mock_pw)

    return mock_pw_cm, mock_chromium, mock_browser, mock_context, mock_page


class TestDialogHandlerRegistration:
    @pytest.mark.asyncio
    async def test_dialog_handler_registered_on_context(self):
        mock_pw_cm, _, _, mock_context, _ = _build_mock_chain()

        with patch("pagemap.browser_session.async_playwright", return_value=mock_pw_cm):
            session = BrowserSession()
            await session.start()

        # Find dialog handler registration
        on_calls = mock_context.on.call_args_list
        dialog_calls = [c for c in on_calls if c[0][0] == "dialog"]
        assert len(dialog_calls) == 1
        assert dialog_calls[0][0][1] == session._on_dialog


# ── TestDialogDuringExecuteAction ────────────────────────────────────


class TestDialogDuringExecuteAction:
    @pytest.mark.asyncio
    async def test_click_with_alert_shows_warning(self):
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
        assert "Clicked [1]" in data["description"]
        assert len(data["dialogs"]) == 1
        assert data["dialogs"][0]["type"] == "alert"
        assert data["dialogs"][0]["message"] == "Welcome!"
        assert data["dialogs"][0]["action"] == "accepted"

    @pytest.mark.asyncio
    async def test_no_dialog_no_warning(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "dialogs" not in data

    @pytest.mark.asyncio
    async def test_multiple_dialogs_all_reported(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[
                DialogInfo(dialog_type="alert", message="Hi", dismissed=False),
                DialogInfo(dialog_type="confirm", message="Delete?", dismissed=True),
            ]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert len(data["dialogs"]) == 2
        assert data["dialogs"][0]["type"] == "alert"
        assert data["dialogs"][0]["message"] == "Hi"
        assert data["dialogs"][0]["action"] == "accepted"
        assert data["dialogs"][1]["type"] == "confirm"
        assert data["dialogs"][1]["message"] == "Delete?"
        assert data["dialogs"][1]["action"] == "dismissed"

    @pytest.mark.asyncio
    async def test_dialog_with_navigation(self):
        """Dialog + URL change → both reported in JSON."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session(current_url="https://example.com/page2")
        mock_session.drain_dialogs = MagicMock(
            return_value=[
                DialogInfo(dialog_type="beforeunload", message="", dismissed=False),
            ]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "navigation"
        assert len(data["dialogs"]) == 1
        assert data["dialogs"][0]["type"] == "beforeunload"


# ── TestFormatDialogWarnings ──────────────────────────────────────────


class TestFormatDialogWarnings:
    def test_empty_returns_empty(self):
        assert _format_dialog_warnings([]) == ""

    def test_single_alert_format(self):
        dialogs = [DialogInfo(dialog_type="alert", message="Hello", dismissed=False)]
        result = _format_dialog_warnings(dialogs)
        assert "JS dialog(s) appeared" in result
        assert 'JS alert() accepted: "Hello"' in result

    def test_single_confirm_format(self):
        dialogs = [DialogInfo(dialog_type="confirm", message="Sure?", dismissed=True)]
        result = _format_dialog_warnings(dialogs)
        assert 'JS confirm() dismissed: "Sure?"' in result

    def test_multiple_format(self):
        dialogs = [
            DialogInfo(dialog_type="alert", message="A", dismissed=False),
            DialogInfo(dialog_type="prompt", message="B", dismissed=True),
        ]
        result = _format_dialog_warnings(dialogs)
        assert 'JS alert() accepted: "A"' in result
        assert 'JS prompt() dismissed: "B"' in result
        # Both on separate lines
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header + 2 items
