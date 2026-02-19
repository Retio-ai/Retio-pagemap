"""Tests for the fill_form MCP tool.

Verifies:
1. Constants: FormField model, FILL_FORM_VALID_ACTIONS, MAX_FILL_FORM_FIELDS
2. Input validation: empty list, too many fields, invalid action, missing value, etc.
3. Basic operations: type, select, click, mixed batch
4. Navigation detection: mid-batch navigation, SSRF blocking
5. Popup handling: popup detected + switch, SSRF blocked popup
6. DOM change detection: major, minor, none
7. Error handling: locator error, Playwright error, browser dead, timeout
8. Dialog warnings appended to results
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError

from pagemap import Interactable, PageMap
from pagemap.dom_change_detector import DomFingerprint
from pagemap.server import (
    _FILL_FORM_SETTLE_MS,
    FILL_FORM_TIMEOUT_SECONDS,
    FILL_FORM_VALID_ACTIONS,
    MAX_FILL_FORM_FIELDS,
    MAX_SELECT_VALUE_LENGTH,
    MAX_TYPE_VALUE_LENGTH,
    FormField,
    _format_fill_form_result,
    _truncate,
    fill_form,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
    """Create a PageMap with 5 interactables for fill_form testing."""
    return PageMap(
        url=url,
        title="Test Form",
        page_type="unknown",
        interactables=[
            Interactable(
                ref=1,
                role="textbox",
                name="Email",
                affordance="type",
                region="main",
                tier=1,
                selector="input#email",
            ),
            Interactable(
                ref=2,
                role="textbox",
                name="Password",
                affordance="type",
                region="main",
                tier=1,
                selector="input#password",
            ),
            Interactable(
                ref=3,
                role="combobox",
                name="Country",
                affordance="select",
                region="main",
                tier=1,
                selector="select#country",
            ),
            Interactable(
                ref=4,
                role="button",
                name="Submit",
                affordance="click",
                region="main",
                tier=1,
                selector="button#submit",
            ),
            Interactable(
                ref=5,
                role="checkbox",
                name="Remember me",
                affordance="click",
                region="main",
                tier=1,
                selector="input#remember",
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(current_url: str = "https://example.com") -> MagicMock:
    """Create a mock BrowserSession for fill_form."""
    session = MagicMock()
    session.get_page_url = AsyncMock(return_value=current_url)
    session.consume_new_page = MagicMock(return_value=None)
    session.drain_dialogs = MagicMock(return_value=[])

    locator = AsyncMock()
    locator.first = AsyncMock()
    locator.first.click = AsyncMock()
    locator.first.fill = AsyncMock()
    locator.first.select_option = AsyncMock()
    locator.first.hover = AsyncMock()
    locator.count = AsyncMock(return_value=1)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=locator)
    page.locator = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.goto = AsyncMock()
    type(page).url = PropertyMock(return_value=current_url)

    session.page = page
    return session


def _fp(
    *,
    total_interactives: int = 10,
    has_dialog: bool = False,
    body_child_count: int = 5,
    title: str = "Test Form",
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
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._last_page_map = None
    yield
    srv._last_page_map = None


# ── TestFillFormConstants ──────────────────────────────────────────


class TestFillFormConstants:
    """Verify fill_form constants."""

    def test_valid_actions(self):
        assert frozenset({"type", "select", "click"}) == FILL_FORM_VALID_ACTIONS

    def test_max_fields(self):
        assert MAX_FILL_FORM_FIELDS == 20

    def test_timeout(self):
        assert FILL_FORM_TIMEOUT_SECONDS == 60

    def test_settle_ms(self):
        assert _FILL_FORM_SETTLE_MS == 300


# ── TestFormFieldModel ──────────────────────────────────────────


class TestFormFieldModel:
    """Verify FormField Pydantic model."""

    def test_create_with_value(self):
        f = FormField(ref=1, action="type", value="hello")
        assert f.ref == 1
        assert f.action == "type"
        assert f.value == "hello"

    def test_create_without_value(self):
        f = FormField(ref=1, action="click")
        assert f.value is None

    def test_from_dict(self):
        f = FormField(**{"ref": 2, "action": "select", "value": "US"})
        assert f.ref == 2
        assert f.action == "select"
        assert f.value == "US"


# ── TestTruncate ──────────────────────────────────────────────────


class TestTruncate:
    """Verify _truncate helper."""

    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_long_text_truncated(self):
        result = _truncate("a" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_exact_length(self):
        assert _truncate("hello", 5) == "hello"


# ── TestFormatFillFormResult ──────────────────────────────────────


class TestFormatFillFormResult:
    """Verify _format_fill_form_result helper."""

    def test_all_completed(self):
        result = _format_fill_form_result(
            ['[1] textbox "Email": typed', '[2] textbox "Password": typed'],
            2,
            2,
        )
        assert "2/2 fields completed" in result
        assert "typed" in result

    def test_stopped_reason(self):
        result = _format_fill_form_result(
            ['[1] textbox "Email": typed'],
            1,
            3,
            stopped_reason="locator error",
        )
        assert "1/3 fields completed (stopped: locator error)" in result

    def test_nav_warning(self):
        result = _format_fill_form_result(
            ['[1] textbox "Email": typed'],
            1,
            2,
            nav_warning="⚠ Page navigated",
        )
        assert "⚠ Page navigated" in result


# ── TestFillFormInputValidation ──────────────────────────────────


class TestFillFormInputValidation:
    """Input validation for fill_form."""

    @pytest.mark.asyncio
    async def test_empty_fields(self):
        result = await fill_form(fields=[])
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_too_many_fields(self):
        fields = [FormField(ref=1, action="click") for _ in range(21)]
        result = await fill_form(fields=fields)
        assert "Too many fields" in result
        assert "20" in result

    @pytest.mark.asyncio
    async def test_invalid_action(self):
        fields = [FormField(ref=1, action="hover")]
        result = await fill_form(fields=fields)
        assert "invalid action" in result.lower()

    @pytest.mark.asyncio
    async def test_type_missing_value(self):
        fields = [FormField(ref=1, action="type", value=None)]
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        result = await fill_form(fields=fields)
        assert "requires a 'value'" in result

    @pytest.mark.asyncio
    async def test_select_missing_value(self):
        fields = [FormField(ref=3, action="select", value=None)]
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        result = await fill_form(fields=fields)
        assert "requires a 'value'" in result

    @pytest.mark.asyncio
    async def test_type_value_too_long(self):
        fields = [FormField(ref=1, action="type", value="x" * (MAX_TYPE_VALUE_LENGTH + 1))]
        result = await fill_form(fields=fields)
        assert "too long" in result

    @pytest.mark.asyncio
    async def test_select_value_too_long(self):
        fields = [FormField(ref=3, action="select", value="x" * (MAX_SELECT_VALUE_LENGTH + 1))]
        result = await fill_form(fields=fields)
        assert "too long" in result

    @pytest.mark.asyncio
    async def test_no_page_map(self):
        fields = [FormField(ref=1, action="click")]
        result = await fill_form(fields=fields)
        assert "No active Page Map" in result

    @pytest.mark.asyncio
    async def test_ref_not_found(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        fields = [FormField(ref=999, action="click")]
        result = await fill_form(fields=fields)
        assert "ref [999] not found" in result

    @pytest.mark.asyncio
    async def test_affordance_mismatch(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        # Try to type on a button (affordance=click)
        fields = [FormField(ref=4, action="type", value="text")]
        result = await fill_form(fields=fields)
        assert "cannot type" in result.lower()
        assert 'action="click"' in result


# ── TestFillFormBasic ──────────────────────────────────────────────


class TestFillFormBasic:
    """Basic fill_form operations."""

    @pytest.mark.asyncio
    async def test_single_type(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=1, action="type", value="test@email.com")])

        assert "1/1 fields completed" in result
        assert "typed" in result

    @pytest.mark.asyncio
    async def test_single_select(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=3, action="select", value="US")])

        assert "1/1 fields completed" in result
        assert "selected" in result

    @pytest.mark.asyncio
    async def test_single_click(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=5, action="click")])

        assert "1/1 fields completed" in result
        assert "clicked" in result

    @pytest.mark.asyncio
    async def test_mixed_batch(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[
                FormField(ref=1, action="type", value="user@email.com"),
                FormField(ref=2, action="type", value="secret123"),
                FormField(ref=5, action="click"),
            ])

        assert "3/3 fields completed" in result
        assert "typed" in result
        assert "clicked" in result

    @pytest.mark.asyncio
    async def test_click_without_value(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=4, action="click", value=None)])

        assert "1/1 fields completed" in result

    @pytest.mark.asyncio
    async def test_response_format_header(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=1, action="type", value="hi")])

        lines = result.strip().split("\n")
        assert lines[0].startswith("fill_form:")

    @pytest.mark.asyncio
    async def test_css_fallback_noted(self):
        """CSS selector fallback should be noted in result."""
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        page = mock_session.page

        # Make role locator return multiple matches → force CSS fallback
        role_locator = MagicMock()
        role_locator.count = AsyncMock(return_value=3)

        css_locator = AsyncMock()
        css_locator.count = AsyncMock(return_value=1)
        css_locator.first = AsyncMock()
        css_locator.first.fill = AsyncMock()

        page.get_by_role = MagicMock(return_value=role_locator)
        page.locator = MagicMock(return_value=css_locator)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=1, action="type", value="test")])

        assert "CSS selector" in result


# ── TestFillFormNavigation ──────────────────────────────────────────


class TestFillFormNavigation:
    """Navigation detection during fill_form batch."""

    @pytest.mark.asyncio
    async def test_mid_batch_navigation_stops(self):
        """Navigation after 2nd field → stop, report partial."""
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        call_count = 0

        async def _url_changes(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return "https://example.com/dashboard"
            return "https://example.com"

        mock_session.get_page_url = _url_changes

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await fill_form(fields=[
                FormField(ref=1, action="type", value="user"),
                FormField(ref=2, action="type", value="pass"),
                FormField(ref=4, action="click"),
            ])

        assert "navigated" in result.lower()
        assert "dashboard" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_ssrf_navigation_blocked(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        mock_session.get_page_url = AsyncMock(return_value="http://169.254.169.254/metadata")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
        ):
            result = await fill_form(fields=[FormField(ref=1, action="type", value="test")])

        assert "blocked" in result.lower()
        mock_session.page.goto.assert_called_once_with("about:blank")
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_last_field_navigation(self):
        """Navigation after last field → full completion + nav warning."""
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        call_count = 0

        async def _url_after_last(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                return "https://example.com/success"
            return "https://example.com"

        mock_session.get_page_url = _url_after_last

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await fill_form(fields=[FormField(ref=4, action="click")])

        assert "1/1 fields completed" in result
        assert "navigated" in result.lower()

    @pytest.mark.asyncio
    async def test_partial_completion_report(self):
        """Partial report shows which fields completed before stop."""
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        # Navigate after first field
        mock_session.get_page_url = AsyncMock(return_value="https://other.com")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await fill_form(fields=[
                FormField(ref=1, action="type", value="user"),
                FormField(ref=2, action="type", value="pass"),
            ])

        assert "1/2 fields completed" in result
        assert "typed" in result


# ── TestFillFormPopup ──────────────────────────────────────────────


class TestFillFormPopup:
    """Popup handling during fill_form."""

    @pytest.mark.asyncio
    async def test_popup_detected_switches(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        popup_page = MagicMock()
        popup_page.is_closed = MagicMock(return_value=False)
        popup_page.wait_for_load_state = AsyncMock()
        type(popup_page).url = PropertyMock(return_value="https://popup.example.com")

        mock_session.consume_new_page = MagicMock(return_value=popup_page)
        mock_session.switch_page = AsyncMock()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server._validate_url_with_dns", return_value=None),
        ):
            result = await fill_form(fields=[FormField(ref=4, action="click")])

        assert "popup" in result.lower() or "New tab" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_popup_ssrf_blocked(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        popup_page = MagicMock()
        popup_page.is_closed = MagicMock(return_value=False)
        popup_page.wait_for_load_state = AsyncMock()
        popup_page.close = AsyncMock()
        type(popup_page).url = PropertyMock(return_value="http://127.0.0.1/evil")

        mock_session.consume_new_page = MagicMock(return_value=popup_page)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
        ):
            result = await fill_form(fields=[FormField(ref=4, action="click")])

        assert "blocked" in result.lower()


# ── TestFillFormDomChange ──────────────────────────────────────────


class TestFillFormDomChange:
    """DOM change detection after fill_form batch."""

    @pytest.mark.asyncio
    async def test_major_dom_change(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        pre = _fp(has_dialog=False)
        post = _fp(has_dialog=True)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[pre, post]),
        ):
            result = await fill_form(fields=[FormField(ref=1, action="type", value="hi")])

        assert "Page content changed" in result
        assert "get_page_map" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_minor_dom_change(self):
        import pagemap.server as srv
        page_map = _make_page_map()
        srv._last_page_map = page_map
        mock_session = _make_mock_session()

        pre = _fp(total_interactives=100)
        post = _fp(total_interactives=101)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[pre, post]),
        ):
            result = await fill_form(fields=[FormField(ref=1, action="type", value="hi")])

        assert "Page content updated" in result
        # Minor change should preserve page map
        assert srv._last_page_map is page_map

    @pytest.mark.asyncio
    async def test_no_dom_change(self):
        import pagemap.server as srv
        page_map = _make_page_map()
        srv._last_page_map = page_map
        mock_session = _make_mock_session()

        fp = _fp()

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", side_effect=[fp, fp]),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=1, action="type", value="hi")])

        assert "Page content changed" not in result
        assert "Page content updated" not in result
        assert srv._last_page_map is page_map


# ── TestFillFormErrors ──────────────────────────────────────────────


class TestFillFormErrors:
    """Error handling for fill_form."""

    @pytest.mark.asyncio
    async def test_locator_error_stops(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.count = AsyncMock(return_value=0)

        # No CSS selector match either
        css_locator = MagicMock()
        css_locator.count = AsyncMock(return_value=0)
        mock_session.page.locator = MagicMock(return_value=css_locator)

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
        ):
            result = await fill_form(fields=[
                FormField(ref=1, action="type", value="test"),
                FormField(ref=2, action="type", value="pass"),
            ])

        assert "locator error" in result
        assert "0/2" in result

    @pytest.mark.asyncio
    async def test_playwright_error_stops(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Element is disabled"))

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
        ):
            result = await fill_form(fields=[
                FormField(ref=1, action="type", value="test"),
                FormField(ref=2, action="type", value="pass"),
            ])

        assert "action error" in result
        assert "0/2" in result

    @pytest.mark.asyncio
    async def test_browser_dead(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value
        locator.first.fill = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await fill_form(fields=[FormField(ref=1, action="type", value="test")])

        assert "Browser connection lost" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_timeout(self):
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)

        locator = mock_session.page.get_by_role.return_value
        locator.first.fill = _hang

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.FILL_FORM_TIMEOUT_SECONDS", 0.1),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
        ):
            result = await fill_form(fields=[FormField(ref=1, action="type", value="test")])

        assert "timed out" in result
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_partial_result_on_error(self):
        """First field succeeds, second fails → partial report."""
        import pagemap.server as srv
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        locator = mock_session.page.get_by_role.return_value

        call_count = 0

        async def _fail_on_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise PlaywrightError("Element is disabled")

        locator.first.fill = _fail_on_second

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
        ):
            result = await fill_form(fields=[
                FormField(ref=1, action="type", value="user"),
                FormField(ref=2, action="type", value="pass"),
            ])

        assert "1/2" in result
        assert "typed" in result
        assert "Error" in result


# ── TestFillFormDialogs ──────────────────────────────────────────────


class TestFillFormDialogs:
    """Dialog warnings appended to fill_form results."""

    @pytest.mark.asyncio
    async def test_dialog_warning_appended(self):
        import pagemap.server as srv
        from pagemap.browser_session import DialogInfo
        srv._last_page_map = _make_page_map()
        mock_session = _make_mock_session()
        mock_session.drain_dialogs = MagicMock(
            return_value=[DialogInfo(dialog_type="alert", message="Saved!", dismissed=False)]
        )

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server.capture_dom_fingerprint", return_value=_fp()),
            patch("pagemap.server.detect_dom_changes") as mock_detect,
        ):
            from pagemap.dom_change_detector import DomChangeVerdict
            mock_detect.return_value = DomChangeVerdict(changed=False, severity="none", reasons=[])
            result = await fill_form(fields=[FormField(ref=1, action="type", value="hi")])

        assert "JS dialog" in result
        assert "Saved!" in result
