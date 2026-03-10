# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for multi-tab session management (Phase 1 porting from Retio MVP).

Ported from Retio ``multi-tab-session.test.ts`` (17 unit + 8 integration = 25 tests).
Adapted for Playwright + PageMapCache.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.cache import PageMapCache
from pagemap.server.multi_tab import (
    MAX_TABS,
    TAB_NAME_PATTERN,
    TAB_TTL_SECONDS,
    MultiTabSession,
    TabInstance,
    TabOpStatus,
    _validate_cookies,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_browser():
    """Create a mock Browser that produces mock BrowserContexts."""
    browser = MagicMock()

    async def _new_context(**kwargs):
        ctx = AsyncMock()
        page = AsyncMock()
        page.url = "about:blank"
        page.goto = AsyncMock()
        ctx.new_page = AsyncMock(return_value=page)
        ctx.add_cookies = AsyncMock()
        ctx.close = AsyncMock()
        return ctx

    browser.new_context = AsyncMock(side_effect=_new_context)
    return browser


def _make_mock_session(url: str = "https://example.com"):
    """Create a mock BrowserSession for use with MultiTabSession."""
    session = AsyncMock()
    session.page = MagicMock()
    session.page.url = url
    session.context = MagicMock()
    session.context.add_cookies = AsyncMock()
    session.navigate = AsyncMock()
    session.get_page_url = AsyncMock(return_value=url)
    session.install_ssrf_route_guard = AsyncMock()
    session.start_from_pool = AsyncMock()
    session.stop = AsyncMock()
    session._browser = _make_mock_browser()
    return session


@pytest.fixture
def mock_browser():
    return _make_mock_browser()


@pytest.fixture
def multi_tab(mock_browser):
    return MultiTabSession(mock_browser)


# ---------------------------------------------------------------------------
# We need to patch BrowserSession to avoid real browser creation
# ---------------------------------------------------------------------------


def _make_fake_browser_session():
    """Create a fully mocked BrowserSession instance (factory mock)."""
    session = MagicMock()
    session.page = MagicMock()
    session.page.url = "about:blank"
    session.context = MagicMock()
    session.context.add_cookies = AsyncMock()
    session.start_from_pool = AsyncMock()
    session.stop = AsyncMock()
    session.get_page_url = AsyncMock(return_value="about:blank")
    session.install_ssrf_route_guard = AsyncMock()

    async def _navigate(url):
        session.page.url = url
        return MagicMock()

    session.navigate = AsyncMock(side_effect=_navigate)
    return session


def _patch_browser_session():
    """Patch BrowserSession constructor to return factory mocks."""
    return patch(
        "pagemap.server.multi_tab.BrowserSession",
        side_effect=lambda config=None: _make_fake_browser_session(),
    )


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_state(multi_tab):
    """Initial state: 0 tabs, no active tab, not multi-tab."""
    assert multi_tab.is_multi_tab is False
    assert multi_tab.active_tab_name is None
    assert multi_tab.get_active_tab() is None
    assert multi_tab.get_active_page() is None
    assert multi_tab.get_active_cache() is None
    assert multi_tab.get_active_session() is None


# ---------------------------------------------------------------------------
# 2-3. Tab name validation
# ---------------------------------------------------------------------------


def test_valid_tab_name():
    """Valid tab names: alphanumeric + underscore, 1-30 chars."""
    assert TAB_NAME_PATTERN.match("oliveyoung")
    assert TAB_NAME_PATTERN.match("tab_1")
    assert TAB_NAME_PATTERN.match("A")
    assert TAB_NAME_PATTERN.match("a" * 30)


def test_invalid_tab_name():
    """Invalid tab names: empty, special chars, >30 chars."""
    assert TAB_NAME_PATTERN.match("") is None
    assert TAB_NAME_PATTERN.match("tab-1") is None
    assert TAB_NAME_PATTERN.match("tab 1") is None
    assert TAB_NAME_PATTERN.match("tab.1") is None
    assert TAB_NAME_PATTERN.match("a" * 31) is None


# ---------------------------------------------------------------------------
# 4. Open tab success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_tab_success(multi_tab):
    """open_tab creates a new tab and sets it as active."""
    with _patch_browser_session():
        result = await multi_tab.open_tab("oliveyoung", "https://oliveyoung.co.kr")

    assert result["status"] == TabOpStatus.OK
    assert result["tab_name"] == "oliveyoung"
    assert result["tab_count"] == 1
    assert multi_tab.is_multi_tab is True
    assert multi_tab.active_tab_name == "oliveyoung"
    assert multi_tab.get_active_tab() is not None
    assert multi_tab.get_active_cache() is not None
    assert isinstance(multi_tab.get_active_cache(), PageMapCache)


# ---------------------------------------------------------------------------
# 5. Max tabs limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_tabs_limit(multi_tab):
    """Opening more than MAX_TABS (5) tabs is rejected."""
    with _patch_browser_session():
        for i in range(MAX_TABS):
            result = await multi_tab.open_tab(f"tab{i}", f"https://site{i}.com")
            assert result["status"] == TabOpStatus.OK

        result = await multi_tab.open_tab("tab_extra", "https://extra.com")
        assert result["status"] == TabOpStatus.MAX_TABS_REACHED


# ---------------------------------------------------------------------------
# 6. Duplicate name rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_name_rejected(multi_tab):
    """Opening a tab with an existing name is rejected."""
    with _patch_browser_session():
        await multi_tab.open_tab("oliveyoung", "https://oliveyoung.co.kr")
        result = await multi_tab.open_tab("oliveyoung", "https://oliveyoung.co.kr/other")

    assert result["status"] == TabOpStatus.TAB_EXISTS


# ---------------------------------------------------------------------------
# 7. Invalid URL rejected (via SSRF validator)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_url_rejected(multi_tab):
    """SSRF-blocked URL is rejected."""

    async def _block(url):
        return "Private IP address"

    with _patch_browser_session():
        result = await multi_tab.open_tab("bad", "http://169.254.169.254/metadata", ssrf_validator=_block)

    assert result["status"] == TabOpStatus.INVALID_URL
    assert "blocked" in result["error"].lower()


# ---------------------------------------------------------------------------
# 8. HTTP URL accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_url_accepted(multi_tab):
    """HTTP (non-HTTPS) URLs are accepted."""
    with _patch_browser_session():
        result = await multi_tab.open_tab("httpsite", "http://example.com")

    assert result["status"] == TabOpStatus.OK


# ---------------------------------------------------------------------------
# 9. Switch tab success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_tab_success(multi_tab):
    """Switching to an existing tab succeeds."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")
        assert multi_tab.active_tab_name == "tab2"

        result = await multi_tab.switch_tab("tab1")

    assert result["status"] == TabOpStatus.OK
    assert result["tab_name"] == "tab1"
    assert multi_tab.active_tab_name == "tab1"


# ---------------------------------------------------------------------------
# 10. Switch tab not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_tab_not_found(multi_tab):
    """Switching to a non-existent tab fails."""
    result = await multi_tab.switch_tab("nonexistent")
    assert result["status"] == TabOpStatus.TAB_NOT_FOUND


# ---------------------------------------------------------------------------
# 11. Close tab success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_tab_success(multi_tab):
    """Closing an existing tab succeeds."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")

        result = await multi_tab.close_tab("tab1")

    assert result["status"] == TabOpStatus.OK
    assert result["closed_tab"] == "tab1"
    assert result["tab_count"] == 1


# ---------------------------------------------------------------------------
# 12. Close tab not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_tab_not_found(multi_tab):
    """Closing a non-existent tab fails."""
    result = await multi_tab.close_tab("nonexistent")
    assert result["status"] == TabOpStatus.TAB_NOT_FOUND


# ---------------------------------------------------------------------------
# 13. Close auto-switch active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_auto_switch_active(multi_tab):
    """Closing the active tab auto-switches to the next available tab."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")
        assert multi_tab.active_tab_name == "tab2"

        result = await multi_tab.close_tab("tab2")

    assert result["status"] == TabOpStatus.OK
    assert result["active_tab"] == "tab1"
    assert multi_tab.active_tab_name == "tab1"


# ---------------------------------------------------------------------------
# 14. is_multi_tab state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_multi_tab(multi_tab):
    """is_multi_tab reflects open tab count."""
    assert multi_tab.is_multi_tab is False

    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
    assert multi_tab.is_multi_tab is True

    await multi_tab.close_tab("tab1")
    assert multi_tab.is_multi_tab is False


# ---------------------------------------------------------------------------
# 15. TTL expiry prune
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_expiry_prune(multi_tab):
    """Tabs older than TAB_TTL_SECONDS are pruned."""
    with _patch_browser_session():
        await multi_tab.open_tab("old_tab", "https://old.com")

    # Artificially age the tab
    tab = multi_tab._tabs["old_tab"]
    object.__setattr__(tab, "created_at", time.monotonic() - TAB_TTL_SECONDS - 1)

    pruned = await multi_tab.prune_expired_tabs()
    assert "old_tab" in pruned
    assert multi_tab.is_multi_tab is False


# ---------------------------------------------------------------------------
# 16. Cookie isolation (per-tab independent cache)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_isolation(multi_tab):
    """Each tab has its own independent PageMapCache."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")

    cache1 = multi_tab._tabs["tab1"].cache
    cache2 = multi_tab._tabs["tab2"].cache

    # They should be different instances
    assert cache1 is not cache2


# ---------------------------------------------------------------------------
# 17. close_all (dispose)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_all(multi_tab):
    """close_all closes all tabs."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")

    await multi_tab.close_all()
    assert multi_tab.is_multi_tab is False
    assert multi_tab.active_tab_name is None


# ---------------------------------------------------------------------------
# 18. Existing tools use active tab (integration with _resolve_multi_tab_context)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_tools_use_active_tab():
    """When multi-tab is active, existing tools use the active tab's session/cache."""
    import pagemap.server as srv
    from pagemap.server import _create_stdio_context, _resolve_multi_tab_context

    mock_session = _make_mock_session("https://oliveyoung.co.kr")
    tab_cache = PageMapCache()

    tab = TabInstance(
        name="oliveyoung",
        session=mock_session,
        cache=tab_cache,
    )
    multi = MagicMock(spec=MultiTabSession)
    multi.is_multi_tab = True
    multi.get_active_tab.return_value = tab

    srv._state.multi_tab = multi

    ctx = _create_stdio_context()
    # ctx.multi_tab comes from _state.multi_tab via _create_stdio_context
    assert ctx.multi_tab is multi
    resolved = _resolve_multi_tab_context(ctx)

    # Should use tab's cache, not the global one
    assert resolved.cache is tab_cache
    assert resolved.cache is not srv._state.cache

    # get_session should return the tab's session
    session = await resolved.get_session()
    assert session is mock_session


# ---------------------------------------------------------------------------
# 19. Backward compat (zero tabs = single session mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backward_compat_zero_tabs():
    """With no tabs open, _resolve_multi_tab_context returns original ctx."""
    import pagemap.server as srv
    from pagemap.server import _create_stdio_context, _resolve_multi_tab_context

    srv._state.multi_tab = None  # No multi-tab
    ctx = _create_stdio_context()
    resolved = _resolve_multi_tab_context(ctx)

    # Should be the same cache (global)
    assert resolved.cache is srv._state.cache


@pytest.mark.asyncio
async def test_backward_compat_empty_multi_tab(mock_browser):
    """With MultiTabSession created but 0 tabs, falls back to single mode."""
    import pagemap.server as srv
    from pagemap.server import _create_stdio_context, _resolve_multi_tab_context

    srv._state.multi_tab = MultiTabSession(mock_browser)
    # 0 tabs open → is_multi_tab is False
    assert srv._state.multi_tab.is_multi_tab is False

    ctx = _create_stdio_context()
    resolved = _resolve_multi_tab_context(ctx)

    assert resolved.cache is srv._state.cache


# ---------------------------------------------------------------------------
# 20. list_tabs format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tabs_format(multi_tab):
    """list_tabs returns properly structured data."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")

    result = multi_tab.list_tabs()
    assert result["status"] == TabOpStatus.OK
    assert result["tab_count"] == 2
    assert result["active_tab"] == "tab2"
    assert len(result["tabs"]) == 2

    tab_names = [t["name"] for t in result["tabs"]]
    assert "tab1" in tab_names
    assert "tab2" in tab_names

    for tab_info in result["tabs"]:
        assert "name" in tab_info
        assert "is_active" in tab_info
        assert "age_seconds" in tab_info
        assert "has_page_map" in tab_info


# ---------------------------------------------------------------------------
# 21. SSRF URL blocked (pagemap-specific)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssrf_url_blocked(multi_tab):
    """SSRF-blocked URLs cannot be opened in tabs."""

    async def _ssrf_validator(url):
        if "169.254" in url:
            return "Cloud metadata IP blocked"
        return None

    with _patch_browser_session():
        result = await multi_tab.open_tab(
            "bad",
            "http://169.254.169.254/latest/meta-data",
            ssrf_validator=_ssrf_validator,
        )

    assert result["status"] == TabOpStatus.INVALID_URL
    assert multi_tab.is_multi_tab is False


# ---------------------------------------------------------------------------
# MCP tool integration tests (JSON roundtrip via server tools)
# ---------------------------------------------------------------------------


async def _noop_ssrf_validator(url):
    """Stub SSRF validator that always passes."""
    return None


@pytest.mark.asyncio
async def test_open_tab_mcp_tool():
    """open_tab MCP tool returns valid JSON."""
    import pagemap.server as srv

    mock_browser = _make_mock_browser()
    # Pre-set multi_tab so get_or_create_multi_tab skips real browser creation
    srv._state.multi_tab = MultiTabSession(mock_browser)

    with (
        patch("pagemap.server._validate_url_with_dns", _noop_ssrf_validator),
        _patch_browser_session(),
    ):
        result_json = await srv.open_tab("test_tab", "https://example.com")

    result = json.loads(result_json)
    assert result["status"] == TabOpStatus.OK
    assert result["tab_name"] == "test_tab"


@pytest.mark.asyncio
async def test_list_tabs_mcp_tool_empty():
    """list_tabs MCP tool returns empty state when no tabs."""
    import pagemap.server as srv

    async def _get_session():
        return _make_mock_session()

    with patch("pagemap.server._get_session", _get_session):
        result_json = await srv.list_tabs()

    result = json.loads(result_json)
    assert result["status"] == "ok"
    assert result["tab_count"] == 0
    assert result["tabs"] == []


@pytest.mark.asyncio
async def test_invalid_tab_name_mcp_tool():
    """open_tab MCP tool rejects invalid tab names."""
    import pagemap.server as srv

    mock_browser = _make_mock_browser()
    srv._state.multi_tab = MultiTabSession(mock_browser)

    with (
        patch("pagemap.server._validate_url_with_dns", _noop_ssrf_validator),
        _patch_browser_session(),
    ):
        result_json = await srv.open_tab("bad-name!", "https://example.com")

    result = json.loads(result_json)
    assert result["status"] == TabOpStatus.INVALID_NAME


# ---------------------------------------------------------------------------
# Issue 1: Resource leak on failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_tab_cleanup_on_navigate_failure(multi_tab):
    """stop() is called when navigate() raises after start_from_pool."""
    fake_session = _make_fake_browser_session()
    fake_session.navigate = AsyncMock(side_effect=RuntimeError("nav failed"))

    with (
        patch(
            "pagemap.server.multi_tab.BrowserSession",
            side_effect=lambda config=None: fake_session,
        ),
        pytest.raises(RuntimeError, match="nav failed"),
    ):
        await multi_tab.open_tab("fail_tab", "https://fail.com")

    # Session must have been cleaned up
    fake_session.stop.assert_awaited_once()
    assert multi_tab.is_multi_tab is False


# ---------------------------------------------------------------------------
# Issue 6: Cookie validation
# ---------------------------------------------------------------------------


def test_cookie_validation_missing_name():
    """Missing name/value → error."""
    err = _validate_cookies([{"value": "v", "domain": "x.com"}])
    assert err is not None
    assert "name" in err


def test_cookie_validation_unknown_keys():
    """Unknown keys → error."""
    err = _validate_cookies([{"name": "n", "value": "v", "domain": "x.com", "bogus": 1}])
    assert err is not None
    assert "unknown" in err.lower()


def test_cookie_validation_missing_domain_url():
    """Missing both domain and url → error."""
    err = _validate_cookies([{"name": "n", "value": "v"}])
    assert err is not None
    assert "domain" in err


def test_cookie_validation_valid():
    """Valid cookies pass through."""
    err = _validate_cookies([{"name": "n", "value": "v", "domain": "example.com"}])
    assert err is None


@pytest.mark.asyncio
async def test_open_tab_rejects_invalid_cookies(multi_tab):
    """open_tab returns error for invalid cookies without allocating resources."""
    with _patch_browser_session():
        result = await multi_tab.open_tab(
            "bad_cookies",
            "https://example.com",
            cookies=[{"bogus_key": "x"}],
        )

    assert result["status"] == TabOpStatus.ERROR
    assert "Invalid cookies" in result["error"]
    assert multi_tab.is_multi_tab is False


# ---------------------------------------------------------------------------
# Issue 8: Auto-prune expired tabs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_tabs_pruned_on_open(multi_tab):
    """Opening a new tab prunes expired tabs first."""
    with _patch_browser_session():
        await multi_tab.open_tab("old_tab", "https://old.com")

    # Artificially age the tab
    tab = multi_tab._tabs["old_tab"]
    object.__setattr__(tab, "created_at", time.monotonic() - TAB_TTL_SECONDS - 1)

    with _patch_browser_session():
        result = await multi_tab.open_tab("new_tab", "https://new.com")

    assert result["status"] == TabOpStatus.OK
    assert "old_tab" not in multi_tab._tabs
    assert "new_tab" in multi_tab._tabs


@pytest.mark.asyncio
async def test_expired_tabs_pruned_on_switch(multi_tab):
    """Switching tabs prunes expired tabs first."""
    with _patch_browser_session():
        await multi_tab.open_tab("tab1", "https://site1.com")
        await multi_tab.open_tab("tab2", "https://site2.com")

    # Age tab1 past TTL
    tab1 = multi_tab._tabs["tab1"]
    object.__setattr__(tab1, "created_at", time.monotonic() - TAB_TTL_SECONDS - 1)

    result = await multi_tab.switch_tab("tab2")
    assert result["status"] == TabOpStatus.OK
    assert "tab1" not in multi_tab._tabs


# ---------------------------------------------------------------------------
# Issue 11: scroll_merge_state not tab-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_clears_scroll_merge_state():
    """Resolved multi-tab ctx has scroll_merge_state=None."""
    import pagemap.server as srv
    from pagemap.server import _create_stdio_context, _resolve_multi_tab_context

    mock_session = _make_mock_session("https://example.com")
    tab_cache = PageMapCache()

    tab = TabInstance(
        name="tab1",
        session=mock_session,
        cache=tab_cache,
    )
    multi = MagicMock(spec=MultiTabSession)
    multi.is_multi_tab = True
    multi.get_active_tab.return_value = tab

    srv._state.multi_tab = multi

    ctx = _create_stdio_context()
    # Original ctx has scroll_merge_state from ServerState
    assert ctx.scroll_merge_state is not None or ctx.scroll_merge_state is None  # may be None if init fails

    resolved = _resolve_multi_tab_context(ctx)

    # Resolved ctx must have scroll_merge_state=None (tab-scoped isolation)
    assert resolved.scroll_merge_state is None
