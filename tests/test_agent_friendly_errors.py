"""Tests for agent-friendly error messages with recovery hints.

Verifies:
1. _safe_error() appends recovery hints for known tool contexts
2. Server busy messages include actionable detail
3. Inline error messages include recovery guidance
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.server import (
    _RECOVERY_HINTS,
    MAX_SELECT_VALUE_LENGTH,
    MAX_TYPE_VALUE_LENGTH,
    SCREENSHOT_TIMEOUT_SECONDS,
    _build_action_error,
    _safe_error,
    execute_action,
    get_page_map,
    take_screenshot,
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
                selector="#submit-btn",
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


# ── TestSafeErrorRecoveryHints ───────────────────────────────────────


class TestSafeErrorRecoveryHints:
    """Tests for _safe_error() recovery hint injection."""

    @pytest.mark.parametrize("context,expected_hint", list(_RECOVERY_HINTS.items()))
    def test_known_contexts_include_hint(self, context: str, expected_hint: str):
        exc = Exception("something went wrong")
        msg = _safe_error(context, exc)
        assert expected_hint in msg

    def test_unknown_context_no_hint(self):
        exc = Exception("something went wrong")
        msg = _safe_error("unknown_tool", exc)
        # Should NOT contain any recovery hint
        for hint in _RECOVERY_HINTS.values():
            assert hint not in msg

    def test_batch_prefix_matching(self):
        """batch [url] context should match the 'batch' hint via prefix."""
        exc = Exception("navigation failed")
        msg = _safe_error("batch [https://example.com]", exc)
        assert _RECOVERY_HINTS["batch"] in msg

    def test_hint_does_not_break_sanitization(self):
        """Recovery hint must not interfere with API key redaction."""
        exc = Exception("key=sk-ant-abc123xyz789def456 leaked")
        msg = _safe_error("get_page_map", exc)
        assert "sk-ant-abc123xyz789def456" not in msg
        assert "<redacted>" in msg
        assert _RECOVERY_HINTS["get_page_map"] in msg

    def test_hint_does_not_break_path_sanitization(self):
        """Recovery hint must not interfere with filesystem path redaction."""
        exc = Exception("Error in /Users/john/.ssh/id_rsa")
        msg = _safe_error("take_screenshot", exc)
        assert "/Users/john/.ssh/id_rsa" not in msg
        assert "<path>" in msg
        assert _RECOVERY_HINTS["take_screenshot"] in msg

    def test_truncation_safety(self):
        """When exc_msg exceeds 200 chars, hint is still appended safely."""
        exc = Exception("x" * 300)
        msg = _safe_error("get_page_map", exc)
        # Truncated message should end with "..." then ". <hint>"
        assert "..." in msg
        assert _RECOVERY_HINTS["get_page_map"] in msg


# ── TestServerBusyMessages ───────────────────────────────────────────


class TestServerBusyMessages:
    """Tests for improved server busy messages."""

    async def test_get_page_map_busy(self):
        import pagemap.server as srv

        srv._state.tool_lock = asyncio.Lock()
        # Hold the lock so get_page_map times out
        await srv._state.tool_lock.acquire()

        original_timeout = srv._TOOL_LOCK_TIMEOUT
        srv._TOOL_LOCK_TIMEOUT = 0.01
        try:
            result = await get_page_map(url="https://example.com")
        finally:
            srv._state.tool_lock.release()
            srv._TOOL_LOCK_TIMEOUT = original_timeout

        assert "another tool call is in progress" in result

    async def test_execute_action_busy(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        srv._state.tool_lock = asyncio.Lock()
        await srv._state.tool_lock.acquire()

        original_timeout = srv._TOOL_LOCK_TIMEOUT
        srv._TOOL_LOCK_TIMEOUT = 0.01
        try:
            result = await execute_action(ref=1, action="click")
        finally:
            srv._state.tool_lock.release()
            srv._TOOL_LOCK_TIMEOUT = original_timeout

        data = json.loads(result)
        assert "another tool call is in progress" in data["error"]

    async def test_take_screenshot_busy(self):
        import pagemap.server as srv

        srv._state.tool_lock = asyncio.Lock()
        await srv._state.tool_lock.acquire()

        original_timeout = srv._TOOL_LOCK_TIMEOUT
        srv._TOOL_LOCK_TIMEOUT = 0.01
        try:
            result = await take_screenshot()
        finally:
            srv._state.tool_lock.release()
            srv._TOOL_LOCK_TIMEOUT = original_timeout

        assert "another tool call is in progress" in result


# ── TestInlineErrorHints ─────────────────────────────────────────────


class TestInlineErrorHints:
    """Tests for inline error messages with recovery hints."""

    async def test_screenshot_timeout_hint(self):
        """Screenshot timeout should suggest calling get_page_map."""
        mock_session = MagicMock()
        mock_session.page = MagicMock()
        mock_session.page.screenshot = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_session.drain_dialogs = MagicMock(return_value=[])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await take_screenshot()

        assert "Call get_page_map" in result
        assert f"{SCREENSHOT_TIMEOUT_SECONDS}s" in result

    async def test_execute_action_failure_hint(self):
        """General execute_action failure should suggest refreshing refs."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = MagicMock()
        mock_session.get_page_url = AsyncMock(return_value="https://example.com")
        mock_session.consume_new_page = MagicMock(return_value=None)
        mock_session.drain_dialogs = MagicMock(return_value=[])

        locator = AsyncMock()
        locator.first = AsyncMock()
        locator.first.click = AsyncMock(side_effect=Exception("element not interactable"))
        locator.count = AsyncMock(return_value=1)

        page = MagicMock()
        page.get_by_role = MagicMock(return_value=locator)
        page.locator = MagicMock(return_value=locator)
        page.wait_for_timeout = AsyncMock()
        type(page).url = PropertyMock(return_value="https://example.com")
        mock_session.page = page

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "refresh refs and retry" in data["error"]

    async def test_url_validation_hint(self):
        """Invalid URL should hint about valid URL format."""
        result = await get_page_map(url="ftp://evil.com/file")
        assert "Provide a valid http:// or https:// URL" in result

    async def test_redirect_ssrf_hint(self):
        """Post-navigation SSRF block should suggest navigating elsewhere."""
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.get_page_url = AsyncMock(return_value="http://169.254.169.254/metadata")
        mock_session.consume_new_page = MagicMock(return_value=None)
        mock_session.drain_dialogs = MagicMock(return_value=[])
        mock_session.page = MagicMock()

        async def _fake_build(*a, **kw):
            return _make_page_map("http://169.254.169.254/metadata")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns", side_effect=[None, "Blocked: internal IP."]),
            patch("pagemap.page_map_builder.build_page_map_live", side_effect=_fake_build),
            patch("pagemap.server.capture_dom_fingerprint", new_callable=AsyncMock, return_value=None),
        ):
            result = await get_page_map(url="https://evil-redirect.com")

        assert "Navigate to a different URL" in result


# ── TestExecuteActionInputHints ──────────────────────────────────────


class TestExecuteActionInputHints:
    """Tests for recovery hints on execute_action input validation errors."""

    async def test_invalid_action_hint(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="invalid_action")
        data = json.loads(result)
        assert "Retry with a valid action" in data["error"]

    async def test_type_missing_value_hint(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="type", value=None)
        data = json.loads(result)
        assert "Provide the text to type" in data["error"]
        assert data["error"].count("'value' parameter") == 1

    async def test_type_value_too_long_hint(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        long_value = "x" * (MAX_TYPE_VALUE_LENGTH + 1)
        result = await execute_action(ref=1, action="type", value=long_value)
        data = json.loads(result)
        assert "Shorten the value" in data["error"]

    async def test_select_missing_value_hint(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=1, action="select", value=None)
        data = json.loads(result)
        assert "Provide the option text" in data["error"]
        assert data["error"].count("'value' parameter") == 1

    async def test_select_value_too_long_hint(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        long_value = "x" * (MAX_SELECT_VALUE_LENGTH + 1)
        result = await execute_action(ref=1, action="select", value=long_value)
        data = json.loads(result)
        assert "Shorten the value" in data["error"]

    async def test_ref_not_found_hint(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        result = await execute_action(ref=999, action="click")
        data = json.loads(result)
        assert "call get_page_map to refresh refs" in data["error"]
        assert "Valid refs:" in data["error"]

    def test_unexpected_action_hint(self):
        # Dead code path — only reachable if VALID_ACTIONS diverges from the
        # description builder's if/elif chain.  Verify the string directly.
        result = _build_action_error("Unexpected action. Retry with a valid action.")
        data = json.loads(result)
        assert "Retry with a valid action" in data["error"]
