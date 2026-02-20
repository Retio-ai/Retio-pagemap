"""Tests for popup/new tab handling.

Tests:
- BrowserSession popup tracking (_on_new_page, consume_new_page, switch_page)
- Popup detection in execute_action (switch + SSRF block)
- Page handler registration on context
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.browser_session import BrowserSession
from pagemap.server import execute_action

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
                name="Open Link",
                affordance="click",
                region="main",
                tier=1,
                selector="#link-btn",
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
    session.switch_page = AsyncMock()

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


def _make_mock_new_page(url: str = "https://newsite.com", closed: bool = False) -> MagicMock:
    new_page = MagicMock()
    new_page.url = url
    new_page.is_closed = MagicMock(return_value=closed)
    new_page.close = AsyncMock()
    new_page.wait_for_load_state = AsyncMock()
    return new_page


@pytest.fixture(autouse=True)
def _reset_state():
    import pagemap.server as srv

    srv._state.cache.invalidate_all()
    yield
    srv._state.cache.invalidate_all()


# ── TestBrowserSessionPopupTracking ──────────────────────────────────


class TestBrowserSessionPopupTracking:
    @pytest.mark.asyncio
    async def test_on_new_page_stores_page(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_new_page = None
        new_page = _make_mock_new_page("https://popup.com")

        await session._on_new_page(new_page)

        assert session._pending_new_page is new_page

    def test_consume_returns_and_clears(self):
        session = BrowserSession.__new__(BrowserSession)
        page = _make_mock_new_page()
        session._pending_new_page = page

        result = session.consume_new_page()

        assert result is page
        assert session._pending_new_page is None

    def test_consume_none_when_empty(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_new_page = None

        assert session.consume_new_page() is None

    @pytest.mark.asyncio
    async def test_multiple_popups_latest_wins(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_new_page = None

        page1 = _make_mock_new_page("https://a.com")
        page2 = _make_mock_new_page("https://b.com")

        await session._on_new_page(page1)
        await session._on_new_page(page2)

        assert session._pending_new_page is page2

    @pytest.mark.asyncio
    async def test_multiple_popups_closes_unclaimed(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_new_page = None

        page1 = _make_mock_new_page("https://a.com")
        page2 = _make_mock_new_page("https://b.com")

        await session._on_new_page(page1)
        await session._on_new_page(page2)

        page1.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unclaimed_already_closed_not_reclosed(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_new_page = None

        page1 = _make_mock_new_page("https://a.com", closed=True)
        page2 = _make_mock_new_page("https://b.com")

        await session._on_new_page(page1)
        await session._on_new_page(page2)

        page1.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_switch_page_updates_reference(self):
        session = BrowserSession.__new__(BrowserSession)
        old_page = _make_mock_new_page("https://old.com")
        new_page = _make_mock_new_page("https://new.com")
        session._page = old_page
        session._cdp_session = None

        await session.switch_page(new_page)

        assert session._page is new_page

    @pytest.mark.asyncio
    async def test_switch_page_detaches_cdp(self):
        session = BrowserSession.__new__(BrowserSession)
        old_page = _make_mock_new_page("https://old.com")
        cdp = AsyncMock()
        session._page = old_page
        session._cdp_session = cdp

        new_page = _make_mock_new_page("https://new.com")
        await session.switch_page(new_page)

        cdp.detach.assert_awaited_once()
        assert session._cdp_session is None

    @pytest.mark.asyncio
    async def test_switch_page_closes_old(self):
        session = BrowserSession.__new__(BrowserSession)
        old_page = _make_mock_new_page("https://old.com")
        session._page = old_page
        session._cdp_session = None

        new_page = _make_mock_new_page("https://new.com")
        await session.switch_page(new_page)

        old_page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_switch_page_old_already_closed(self):
        session = BrowserSession.__new__(BrowserSession)
        old_page = _make_mock_new_page("https://old.com", closed=True)
        session._page = old_page
        session._cdp_session = None

        new_page = _make_mock_new_page("https://new.com")
        await session.switch_page(new_page)

        old_page.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_switch_page_none_old(self):
        """switch_page with no previous page should not error."""
        session = BrowserSession.__new__(BrowserSession)
        session._page = None
        session._cdp_session = None

        new_page = _make_mock_new_page("https://new.com")
        await session.switch_page(new_page)

        assert session._page is new_page


# ── TestPageHandlerRegistration ──────────────────────────────────────


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


class TestPageHandlerRegistration:
    @pytest.mark.asyncio
    async def test_page_handler_registered_on_context(self):
        mock_pw_cm, _, _, mock_context, _ = _build_mock_chain()

        with patch("pagemap.browser_session.async_playwright", return_value=mock_pw_cm):
            session = BrowserSession()
            await session.start()

        on_calls = mock_context.on.call_args_list
        page_calls = [c for c in on_calls if c[0][0] == "page"]
        assert len(page_calls) == 1
        assert page_calls[0][0][1] == session._on_new_page


# ── TestPopupInExecuteAction ─────────────────────────────────────────


class TestPopupInExecuteAction:
    @pytest.mark.asyncio
    async def test_click_popup_switches_and_reports(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        new_page = _make_mock_new_page("https://newsite.com")
        mock_session.consume_new_page = MagicMock(return_value=new_page)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "new_tab"
        assert data["refs_expired"] is True
        assert any("New tab opened" in d for d in data.get("change_details", []))
        mock_session.switch_page.assert_awaited_once_with(new_page)
        assert srv._state.cache.active is None

    @pytest.mark.asyncio
    async def test_popup_ssrf_blocked_closes_and_warns(self):
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()
        new_page = _make_mock_new_page("http://169.254.169.254/metadata")
        mock_session.consume_new_page = MagicMock(return_value=new_page)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server._validate_url_with_dns",
                return_value="Access to cloud metadata is blocked.",
            ),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert any("blocked" in d for d in data.get("change_details", []))
        assert data["change"] == "none"
        new_page.close.assert_awaited_once()
        mock_session.switch_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_popup_closed_immediately_ignored(self):
        """Popup that closed before we check → skip popup logic."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        new_page = _make_mock_new_page("https://flash.com", closed=True)
        mock_session.consume_new_page = MagicMock(return_value=new_page)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] != "new_tab"
        assert "change_details" not in data or not any("New tab" in d for d in data.get("change_details", []))
        mock_session.switch_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_popup_load_timeout_still_switches(self):
        """Popup that times out on load_state still switches."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        new_page = _make_mock_new_page("https://slow.com")
        new_page.wait_for_load_state = AsyncMock(side_effect=TimeoutError("slow"))
        mock_session.consume_new_page = MagicMock(return_value=new_page)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "new_tab"
        mock_session.switch_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_popup_preserves_existing_behavior(self):
        """No popup → normal click flow."""
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert data["change"] != "new_tab"
        mock_session.switch_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_popup_invalidates_page_map(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        new_page = _make_mock_new_page("https://newsite.com")
        mock_session.consume_new_page = MagicMock(return_value=new_page)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            await execute_action(ref=1, action="click")

        assert srv._state.cache.active is None

    @pytest.mark.asyncio
    async def test_popup_with_dialog(self):
        """Popup + dialog → both reported in JSON."""
        import pagemap.server as srv
        from pagemap.browser_session import DialogInfo

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        new_page = _make_mock_new_page("https://newsite.com")
        mock_session.consume_new_page = MagicMock(return_value=new_page)
        mock_session.drain_dialogs = MagicMock(
            return_value=[
                DialogInfo(dialog_type="alert", message="Popup alert!", dismissed=False),
            ]
        )

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert data["change"] == "new_tab"
        assert len(data["dialogs"]) == 1
        assert data["dialogs"][0]["message"] == "Popup alert!"
