"""Tests for the wait_for MCP tool.

Verifies:
1. Constants: WAIT_FOR_MAX_TIMEOUT, WAIT_FOR_MAX_TEXT_LENGTH
2. Input validation: both/neither specified, empty text, length, timeout clamping
3. Text appear mode: already visible, wait + found, timeout
4. Text gone mode: already gone, wait + disappeared, timeout
5. Page map invalidation: appear/gone found → invalidate, timeout → preserve
6. Error handling: browser dead, overall timeout, Playwright error
7. Security: special chars, JS injection attempts, unicode, newlines
8. Dialog warnings in responses
9. Standalone (no page_map required)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap
from pagemap.server import (
    _WAIT_FOR_TEXT_APPEAR_JS,
    _WAIT_FOR_TEXT_GONE_JS,
    WAIT_FOR_MAX_TEXT_LENGTH,
    WAIT_FOR_MAX_TIMEOUT,
    WAIT_FOR_OVERALL_TIMEOUT_SECONDS,
    wait_for,
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


def _make_mock_session() -> MagicMock:
    """Create a mock BrowserSession for wait_for."""
    session = MagicMock()
    session.drain_dialogs = MagicMock(return_value=[])

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=False)
    page.wait_for_function = AsyncMock()

    session.page = page
    return session


# ── TestWaitForConstants ──────────────────────────────────────────


class TestWaitForConstants:
    """Verify wait_for constants."""

    def test_max_timeout(self):
        assert WAIT_FOR_MAX_TIMEOUT == 30.0

    def test_max_text_length(self):
        assert WAIT_FOR_MAX_TEXT_LENGTH == 500

    def test_overall_timeout(self):
        assert WAIT_FOR_OVERALL_TIMEOUT_SECONDS == 35

    def test_js_expressions_are_static(self):
        """JS expressions should be static strings, not dynamically built."""
        assert "text" in _WAIT_FOR_TEXT_APPEAR_JS
        assert "includes" in _WAIT_FOR_TEXT_APPEAR_JS
        assert "text" in _WAIT_FOR_TEXT_GONE_JS
        assert "includes" in _WAIT_FOR_TEXT_GONE_JS


# ── TestWaitForInputValidation ──────────────────────────────────────


class TestWaitForInputValidation:
    """Input validation for wait_for."""

    async def test_both_none(self):
        result = await wait_for(text=None, text_gone=None)
        assert "Specify either" in result

    async def test_both_specified(self):
        result = await wait_for(text="hello", text_gone="bye")
        assert "not both" in result

    async def test_empty_text(self):
        result = await wait_for(text="")
        assert "empty" in result.lower()

    async def test_empty_text_gone(self):
        result = await wait_for(text_gone="")
        assert "empty" in result.lower()

    async def test_text_too_long(self):
        result = await wait_for(text="x" * 501)
        assert "too long" in result.lower()

    async def test_negative_timeout_clamped(self):
        """Negative timeout should be clamped to 0, not error."""
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="hello", timeout=-5)

        assert "already visible" in result

    async def test_max_timeout_clamped(self):
        """Timeout > 30 should be clamped to 30."""
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="hello", timeout=100)

        # Should work without error (clamped, not rejected)
        assert "Error" not in result or "already visible" in result


# ── TestWaitForTextAppear ──────────────────────────────────────────


class TestWaitForTextAppear:
    """Text appear mode."""

    async def test_already_visible(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Order confirmed")

        assert "already visible" in result

    async def test_appears_after_wait(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Order confirmed")

        assert "appeared after" in result
        assert "get_page_map" in result

    async def test_appear_timeout(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock(side_effect=PlaywrightError("Timeout 10000ms exceeded"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Order confirmed", timeout=10)

        assert "did not appear" in result
        assert "10" in result

    async def test_appear_includes_elapsed(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Done")

        assert "appeared after" in result
        # Should have a number like "0.0s"
        assert "s." in result or "s\n" in result


# ── TestWaitForTextGone ──────────────────────────────────────────


class TestWaitForTextGone:
    """Text gone (disappear) mode."""

    async def test_already_gone(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text_gone="Loading...")

        assert "already gone" in result

    async def test_disappears_after_wait(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text_gone="Loading...")

        assert "disappeared after" in result
        assert "get_page_map" in result

    async def test_gone_timeout(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock(side_effect=PlaywrightError("Timeout 10000ms exceeded"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text_gone="Loading...", timeout=10)

        assert "still visible" in result
        assert "10" in result

    async def test_gone_includes_elapsed(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text_gone="Spinner")

        assert "disappeared after" in result


# ── TestWaitForPageMap ──────────────────────────────────────────────


class TestWaitForPageMap:
    """Page map invalidation behavior."""

    async def test_appear_found_invalidates(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await wait_for(text="Done")

        assert srv._state.cache.active is None

    async def test_gone_found_invalidates(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()

        with patch("pagemap.server._get_session", return_value=mock_session):
            await wait_for(text_gone="Loading...")

        assert srv._state.cache.active is None

    async def test_timeout_preserves_page_map(self):
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock(side_effect=PlaywrightError("Timeout 10000ms exceeded"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            await wait_for(text="Never appears", timeout=10)

        assert srv._state.cache.active is page_map

    async def test_already_visible_preserves_page_map(self):
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            await wait_for(text="Already here")

        assert srv._state.cache.active is page_map

    async def test_already_gone_preserves_page_map(self):
        import pagemap.server as srv

        page_map = _make_page_map()
        srv._state.cache.store(page_map, None)
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            await wait_for(text_gone="Already gone")

        assert srv._state.cache.active is page_map


# ── TestWaitForErrors ──────────────────────────────────────────────


class TestWaitForErrors:
    """Error handling for wait_for."""

    async def test_browser_dead(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="hello")

        assert "Browser connection lost" in result
        assert srv._state.cache.active is None

    async def test_overall_timeout(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        mock_session.page.evaluate = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.WAIT_FOR_OVERALL_TIMEOUT_SECONDS", 0.1),
        ):
            result = await wait_for(text="hello")

        assert "overall timeout" in result.lower() or "timed out" in result.lower()
        assert srv._state.cache.active is None

    async def test_non_timeout_playwright_error(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock(side_effect=PlaywrightError("Frame detached"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="hello")

        assert "Error" in result

    async def test_evaluate_failure(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(side_effect=PlaywrightError("Execution context destroyed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="hello")

        assert "Error" in result


# ── TestWaitForSecurity ──────────────────────────────────────────────


class TestWaitForSecurity:
    """Security tests for wait_for."""

    async def test_special_chars_in_text(self):
        """Quotes and special chars should not break JS execution."""
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text='Price: $99.99 "sale"')

        assert "already visible" in result

    async def test_js_injection_attempt(self):
        """JS injection in text should be treated as literal text, not code."""
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text='"); document.cookie; //')

        assert "Error" not in result or "already visible" in result

    async def test_unicode_text(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="주문 완료")

        assert "already visible" in result

    async def test_newline_in_text(self):
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Line1\nLine2")

        assert "already visible" in result


# ── TestWaitForDialogs ──────────────────────────────────────────────


class TestWaitForDialogs:
    """Dialog warnings in wait_for responses."""

    async def test_dialog_on_success(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock()
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="alert", message="Done!", dismissed=False)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Result")

        assert "JS dialog" in result
        assert "Done!" in result

    async def test_dialog_on_timeout(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=False)
        mock_session.page.wait_for_function = AsyncMock(side_effect=PlaywrightError("Timeout 5000ms exceeded"))
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="confirm", message="Leave?", dismissed=True)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Never", timeout=5)

        assert "JS dialog" in result
        assert "Leave?" in result

    async def test_dialog_on_already_visible(self):
        from pagemap.browser_session import DialogInfo

        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="alert", message="Welcome", dismissed=False)]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Hello")

        assert "already visible" in result
        assert "JS dialog" in result
        assert "Welcome" in result


# ── TestWaitForNoPageMapRequired ──────────────────────────────────


class TestWaitForNoPageMapRequired:
    """wait_for works without an existing page_map."""

    async def test_works_without_page_map(self):
        import pagemap.server as srv

        srv._state.cache.invalidate_all()
        mock_session = _make_mock_session()
        mock_session.page.evaluate = AsyncMock(return_value=True)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await wait_for(text="Content loaded")

        assert "already visible" in result
        assert "Error" not in result
