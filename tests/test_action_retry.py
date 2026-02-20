"""Tests for execute_action retry logic (Phase 3).

Verifies:
1. Allow-list error classifier (_is_retryable_error)
2. Retry success on first attempt (backward compat)
3. Retry recovery from transient failures
4. Exhausted retries surface error
5. Non-retryable errors fail immediately
6. press_key bypasses retry path
7. URL change between retries aborts retry loop
8. Wall-clock budget enforcement
9. Click double-submission safety (Timeout not retried for click)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap  # noqa: F401
from pagemap.server import (
    _execute_locator_action_with_retry,
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
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(
    current_url: str = "https://example.com",
    role_count: int = 1,
) -> MagicMock:
    """Create a mock BrowserSession."""
    session = MagicMock()
    session.get_page_url = AsyncMock(return_value=current_url)

    locator = AsyncMock()
    locator.first = AsyncMock()
    locator.first.click = AsyncMock()
    locator.first.fill = AsyncMock()
    locator.first.select_option = AsyncMock()
    locator.count = AsyncMock(return_value=role_count)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=locator)
    page.locator = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    # page.url is a sync property in Playwright
    type(page).url = PropertyMock(return_value=current_url)

    session.page = page
    return session


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._state.cache.invalidate_all()
    yield
    srv._state.cache.invalidate_all()


# ── TestIsRetryableError ─────────────────────────────────────────────


class TestIsRetryableError:
    """Unit tests for allow-list error classifier."""

    def test_timeout_retryable_for_type(self):
        exc = PlaywrightError("Timeout 5000ms exceeded")
        assert _is_retryable_error(exc, "type") is True

    def test_not_visible_retryable_for_click(self):
        exc = PlaywrightError("Element is not visible")
        assert _is_retryable_error(exc, "click") is True

    def test_not_stable_retryable_for_click(self):
        exc = PlaywrightError("Element is not stable")
        assert _is_retryable_error(exc, "click") is True

    def test_intercept_retryable_for_click(self):
        exc = PlaywrightError("Element click intercepted by another element")
        assert _is_retryable_error(exc, "click") is True

    def test_timeout_not_retryable_for_click(self):
        """Timeout is ambiguous for click — might be post-dispatch."""
        exc = PlaywrightError("Timeout 5000ms exceeded")
        assert _is_retryable_error(exc, "click") is False

    def test_target_closed_not_retryable(self):
        exc = PlaywrightError("Target closed")
        assert _is_retryable_error(exc, "type") is False

    def test_unknown_error_not_retryable(self):
        """Allow-list: unregistered errors are never retried."""
        exc = PlaywrightError("Something completely unexpected")
        assert _is_retryable_error(exc, "type") is False

    def test_not_attached_retryable_for_type(self):
        exc = PlaywrightError("Element is not attached to the DOM")
        assert _is_retryable_error(exc, "type") is True

    def test_detached_retryable_for_select(self):
        exc = PlaywrightError("Element was detached from the DOM")
        assert _is_retryable_error(exc, "select") is True

    def test_not_attached_not_retryable_for_click(self):
        """Click only retries on visible/stable/intercept patterns."""
        exc = PlaywrightError("Element is not attached to the DOM")
        assert _is_retryable_error(exc, "click") is False


# ── TestRetrySuccessFirstAttempt ─────────────────────────────────────


class TestRetrySuccessFirstAttempt:
    """First attempt succeeds → no retry, 100% backward compat."""

    @pytest.mark.asyncio
    async def test_click_first_attempt_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert "error" not in data

    @pytest.mark.asyncio
    async def test_type_first_attempt_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert "error" not in data

    @pytest.mark.asyncio
    async def test_select_first_attempt_success(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        data = json.loads(result)
        assert "Selected option in [3]" in data["description"]
        assert "error" not in data


# ── TestRetryOnTransientFailure ──────────────────────────────────────


class TestRetryOnTransientFailure:
    """Transient failure → retry → success."""

    @pytest.mark.asyncio
    async def test_type_succeeds_after_one_retry(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        # get_by_role returns a locator whose fill fails once then succeeds
        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=[PlaywrightError("Timeout 5000ms exceeded"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert locator.first.fill.call_count == 2

    @pytest.mark.asyncio
    async def test_click_succeeds_on_not_visible(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=[PlaywrightError("Element is not visible"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert locator.first.click.call_count == 2

    @pytest.mark.asyncio
    async def test_click_not_retried_on_timeout(self):
        """Timeout is ambiguous for click → immediate failure."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Timeout 5000ms exceeded"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        # _safe_error may not return JSON, just check it's an error
        assert "Error" in result or "error" in result
        assert locator.first.click.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_re_resolves_locator(self):
        """Locator is re-resolved on each retry (role→CSS switch possible)."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=[PlaywrightError("Timeout 5000ms exceeded"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            await execute_action(ref=2, action="type", value="test")

        # get_by_role called twice — once per attempt (re-resolved)
        assert page.get_by_role.call_count == 2

    @pytest.mark.asyncio
    async def test_select_retried_on_not_attached(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.select_option = AsyncMock(side_effect=[PlaywrightError("Element is not attached"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=3, action="select", value="price")

        data = json.loads(result)
        assert "Selected option in [3]" in data["description"]
        assert locator.first.select_option.call_count == 2


# ── TestRetryExhausted ───────────────────────────────────────────────


class TestRetryExhausted:
    """All retries fail → sanitized error returned."""

    @pytest.mark.asyncio
    async def test_all_retries_fail(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Timeout 5000ms exceeded"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        # _safe_error may not return JSON
        assert "Error" in result or "error" in result
        # 3 attempts total (1 + 2 retries)
        assert locator.first.fill.call_count == 3


# ── TestNonRetryableError ────────────────────────────────────────────


class TestNonRetryableError:
    """Non-retryable errors fail immediately, no retry."""

    @pytest.mark.asyncio
    async def test_target_closed_not_retried(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "error" in data
        assert locator.first.fill.call_count == 1

    @pytest.mark.asyncio
    async def test_unknown_error_not_retried(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Completely unexpected error"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        # _safe_error may not return JSON
        assert "Error" in result or "error" in result
        assert locator.first.click.call_count == 1


# ── TestPressKeyNotRetried ───────────────────────────────────────────


class TestPressKeyNotRetried:
    """press_key bypasses retry helper entirely."""

    @pytest.mark.asyncio
    async def test_press_key_no_retry(self):
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        data = json.loads(result)
        assert "Pressed key" in data["description"]
        # get_by_role should NOT be called (press_key uses keyboard.press)
        mock_session.page.get_by_role.assert_not_called()


# ── TestUrlCheckBetweenRetries ───────────────────────────────────────


class TestUrlCheckBetweenRetries:
    """URL change between retries aborts retry loop."""

    @pytest.mark.asyncio
    async def test_url_changed_aborts_retry(self):
        """Attempt 1 fails + URL changes → retry aborted."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session("https://example.com")
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Timeout 5000ms exceeded"))

        # URL changes after first attempt
        url_values = ["https://example.com/new-page"]
        type(page).url = PropertyMock(side_effect=url_values)

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        # _safe_error may not return JSON
        assert "Error" in result or "error" in result
        # Only 1 attempt — retry aborted due to URL change
        assert locator.first.fill.call_count == 1

    @pytest.mark.asyncio
    async def test_url_unchanged_continues_retry(self):
        """URL stays the same → retry proceeds normally."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session("https://example.com")
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=[PlaywrightError("Timeout 5000ms exceeded"), None])

        # URL stays the same
        type(page).url = PropertyMock(return_value="https://example.com")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert locator.first.fill.call_count == 2


# ── TestWallClockBudget ──────────────────────────────────────────────


def _fake_monotonic(*values):
    """Create a monotonic mock that returns scripted values then real time.

    Only the retry helper calls time.monotonic() via `import time` inside
    the function body. asyncio.sleep also calls time.monotonic() internally,
    so we must fall back to real time once our scripted values run out.
    """
    import time as _time

    real = _time.monotonic
    it = iter(values)

    def _mock():
        try:
            return next(it)
        except StopIteration:
            return real()

    return _mock


class TestWallClockBudget:
    """Wall-clock budget enforcement."""

    @pytest.mark.asyncio
    async def test_budget_exhausted_skips_retry(self):
        """Elapsed > budget → retry skipped."""
        target = Interactable(
            ref=2,
            role="textbox",
            name="Search",
            affordance="type",
            region="main",
            tier=1,
            selector="input.search-box",
        )

        mock_session = _make_mock_session("https://example.com")
        page = mock_session.page
        type(page).url = PropertyMock(return_value="https://example.com")

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Timeout 5000ms exceeded"))

        # t0=0, elapsed check=11s → remaining=4s < 5s min → skip retry
        with (
            patch("time.monotonic", side_effect=_fake_monotonic(0.0, 11.0)),
            pytest.raises(PlaywrightError, match="Timeout"),
        ):
            await _execute_locator_action_with_retry(
                page,
                target,
                "type",
                "hello",
                "test-req",
                "https://example.com",
            )

        # Only 1 attempt — budget exhausted before retry
        assert locator.first.fill.call_count == 1

    @pytest.mark.asyncio
    async def test_budget_sufficient_allows_retry(self):
        """Enough budget remaining → retry proceeds."""
        target = Interactable(
            ref=2,
            role="textbox",
            name="Search",
            affordance="type",
            region="main",
            tier=1,
            selector="input.search-box",
        )

        mock_session = _make_mock_session("https://example.com")
        page = mock_session.page
        type(page).url = PropertyMock(return_value="https://example.com")

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=[PlaywrightError("Timeout 5000ms exceeded"), None])

        # t0=0, elapsed check=2s → remaining=13s > 5s → retry allowed
        with patch("time.monotonic", side_effect=_fake_monotonic(0.0, 2.0)):
            method = await _execute_locator_action_with_retry(
                page,
                target,
                "type",
                "hello",
                "test-req",
                "https://example.com",
            )

        assert method == "role"
        assert locator.first.fill.call_count == 2


# ── TestRetryWithStaleRef ────────────────────────────────────────────


class TestRetryWithStaleRef:
    """Retry + subsequent navigation combination."""

    @pytest.mark.asyncio
    async def test_success_after_retry_then_navigation(self):
        """Retry succeeds → post-action URL change → JSON change=navigation."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map("https://example.com"), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=[PlaywrightError("Element is not visible"), None])

        # URL same during retry check, but changes after action
        type(page).url = PropertyMock(return_value="https://example.com")
        mock_session.get_page_url = AsyncMock(return_value="https://example.com/page2")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert data["change"] == "navigation"
        assert srv._state.cache.active is None


# ── TestClickDoubleSubmissionSafety ──────────────────────────────────


class TestClickDoubleSubmissionSafety:
    """Click vs type: same error, different retry behavior."""

    @pytest.mark.asyncio
    async def test_click_timeout_not_retried(self):
        """TimeoutError on click → immediate failure (not safe to retry)."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.click = AsyncMock(side_effect=PlaywrightError("Timeout 5000ms exceeded"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        # _safe_error may not return JSON
        assert "Error" in result or "error" in result
        assert locator.first.click.call_count == 1

    @pytest.mark.asyncio
    async def test_type_timeout_is_retried(self):
        """Same TimeoutError on type → retried (idempotent)."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=[PlaywrightError("Timeout 5000ms exceeded"), None])

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=2, action="type", value="hello")

        data = json.loads(result)
        assert "Typed into [2]" in data["description"]
        assert locator.first.fill.call_count == 2

    @pytest.mark.asyncio
    async def test_click_intercept_is_retried(self):
        """Intercept on click → retried (pre-dispatch failure)."""
        import pagemap.server as srv

        srv._state.cache.store(_make_page_map(), None)
        mock_session = _make_mock_session()
        page = mock_session.page

        locator = page.get_by_role.return_value
        locator.first.click = AsyncMock(
            side_effect=[
                PlaywrightError("Element click intercepted by <div>overlay</div>"),
                None,
            ]
        )

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        data = json.loads(result)
        assert "Clicked [1]" in data["description"]
        assert locator.first.click.call_count == 2
