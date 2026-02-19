"""Tests for browser session configuration and utilities.

Tests BrowserConfig defaults, security launch args, CDP AX tree conversion,
property guards, and S3 browser hardening. Does not require a running browser.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.browser_session import (
    BLOCKED_URL_SCHEMES,
    DEFAULT_LOCALE,
    DEFAULT_USER_AGENT,
    DEFAULT_VIEWPORT,
    BrowserConfig,
    BrowserSession,
    _cdp_ax_nodes_to_tree,
)

# ── BrowserConfig Defaults ─────────────────────────────────────────


class TestBrowserConfig:
    """Tests for BrowserConfig default values."""

    def test_default_headless(self):
        cfg = BrowserConfig()
        assert cfg.headless is True

    def test_default_locale(self):
        cfg = BrowserConfig()
        assert cfg.locale == "ko-KR"

    def test_default_viewport(self):
        cfg = BrowserConfig()
        assert cfg.viewport_width == 1280
        assert cfg.viewport_height == 800

    def test_default_timeout(self):
        cfg = BrowserConfig()
        assert cfg.timeout_ms == 30000

    def test_default_wait_until(self):
        cfg = BrowserConfig()
        assert cfg.wait_until == "networkidle"

    def test_custom_override(self):
        cfg = BrowserConfig(headless=True, timeout_ms=60000)
        assert cfg.headless is True
        assert cfg.timeout_ms == 60000


# ── Module Constants ───────────────────────────────────────────────


class TestModuleConstants:
    """Tests for module-level constants."""

    def test_default_viewport_dimensions(self):
        assert DEFAULT_VIEWPORT == {"width": 1280, "height": 800}

    def test_default_locale_value(self):
        assert DEFAULT_LOCALE == "ko-KR"

    def test_user_agent_looks_like_chrome(self):
        assert "Chrome" in DEFAULT_USER_AGENT
        assert "Mozilla" in DEFAULT_USER_AGENT


# ── Property Guards ────────────────────────────────────────────────


class TestPropertyGuards:
    """Tests for BrowserSession property access before start()."""

    def test_page_raises_before_start(self):
        session = BrowserSession()
        with pytest.raises(RuntimeError, match="not started"):
            _ = session.page

    def test_context_raises_before_start(self):
        session = BrowserSession()
        with pytest.raises(RuntimeError, match="not started"):
            _ = session.context

    def test_session_accepts_config(self):
        cfg = BrowserConfig(headless=True)
        session = BrowserSession(cfg)
        assert session.config.headless is True

    def test_session_default_config(self):
        session = BrowserSession()
        assert session.config is not None
        assert isinstance(session.config, BrowserConfig)


# ── CDP AX Tree Conversion ────────────────────────────────────────


class TestCdpAxNodesToTree:
    """Tests for _cdp_ax_nodes_to_tree() pure function."""

    def test_empty_list_returns_none(self):
        assert _cdp_ax_nodes_to_tree([]) is None

    def test_single_root_node(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "WebArea"},
                "name": {"value": "Test Page"},
                "childIds": [],
                "properties": [],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree is not None
        assert tree["role"] == "WebArea"
        assert tree["name"] == "Test Page"
        assert tree["children"] == []

    def test_parent_child_relationship(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "WebArea"},
                "name": {"value": ""},
                "childIds": ["2", "3"],
                "properties": [],
            },
            {
                "nodeId": "2",
                "role": {"value": "button"},
                "name": {"value": "Submit"},
                "childIds": [],
                "properties": [],
            },
            {
                "nodeId": "3",
                "role": {"value": "textbox"},
                "name": {"value": "Email"},
                "childIds": [],
                "properties": [],
            },
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert len(tree["children"]) == 2
        assert tree["children"][0]["role"] == "button"
        assert tree["children"][0]["name"] == "Submit"
        assert tree["children"][1]["role"] == "textbox"
        assert tree["children"][1]["name"] == "Email"

    def test_nested_tree(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "WebArea"},
                "name": {"value": ""},
                "childIds": ["2"],
                "properties": [],
            },
            {
                "nodeId": "2",
                "role": {"value": "navigation"},
                "name": {"value": "Main Nav"},
                "childIds": ["3"],
                "properties": [],
            },
            {
                "nodeId": "3",
                "role": {"value": "link"},
                "name": {"value": "Home"},
                "childIds": [],
                "properties": [],
            },
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        nav = tree["children"][0]
        assert nav["role"] == "navigation"
        link = nav["children"][0]
        assert link["role"] == "link"
        assert link["name"] == "Home"

    def test_value_extraction_from_properties(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "textbox"},
                "name": {"value": "Search"},
                "childIds": [],
                "properties": [
                    {"name": "value", "value": {"value": "hello world"}},
                ],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree["value"] == "hello world"

    def test_focused_extraction_from_properties(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "textbox"},
                "name": {"value": "Search"},
                "childIds": [],
                "properties": [
                    {"name": "focused", "value": {"value": True}},
                ],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree["focused"] is True

    def test_missing_child_ids_ignored(self):
        """childIds referencing non-existent nodes should be silently skipped."""
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "WebArea"},
                "name": {"value": ""},
                "childIds": ["2", "999"],  # 999 doesn't exist
                "properties": [],
            },
            {
                "nodeId": "2",
                "role": {"value": "button"},
                "name": {"value": "OK"},
                "childIds": [],
                "properties": [],
            },
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert len(tree["children"]) == 1
        assert tree["children"][0]["name"] == "OK"

    def test_role_as_string_fallback(self):
        """When role is a plain string instead of dict."""
        nodes = [
            {
                "nodeId": "1",
                "role": "WebArea",
                "name": {"value": ""},
                "childIds": [],
                "properties": [],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree["role"] == "WebArea"

    def test_name_as_string_fallback(self):
        """When name is a plain string instead of dict."""
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "button"},
                "name": "Click Me",
                "childIds": [],
                "properties": [],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree["name"] == "Click Me"

    def test_defaults_when_no_properties(self):
        """value defaults to '' and focused defaults to False."""
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "button"},
                "name": {"value": "OK"},
                "childIds": [],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree["value"] == ""
        assert tree["focused"] is False


# ── S3: Browser Launch Args ──────────────────────────────────────


def _build_mock_chain():
    """Build a full mock chain: async_playwright → browser → context → page → route."""
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


class TestBrowserLaunchArgs:
    """S3: Verify hardened Chromium launch args."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.mock_pw_cm, self.mock_chromium, self.mock_browser, self.mock_context, self.mock_page = _build_mock_chain()

    def _get_launch_args(self):
        """Extract the args list passed to chromium.launch()."""
        call_kwargs = self.mock_chromium.launch.call_args
        return call_kwargs.kwargs.get("args", call_kwargs[1].get("args", []))

    def _get_context_kwargs(self):
        """Extract kwargs passed to browser.new_context()."""
        return self.mock_browser.new_context.call_args.kwargs

    @pytest.mark.asyncio
    async def test_popup_blocking_arg_removed(self):
        """--block-new-web-contents must NOT be present (popups handled by context.on('page'))."""
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert "--block-new-web-contents" not in self._get_launch_args()

    @pytest.mark.asyncio
    async def test_webrtc_ip_leak_prevention(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in self._get_launch_args()

    @pytest.mark.asyncio
    async def test_disable_features_single_flag(self):
        """--disable-features must be a single arg to avoid last-wins behavior."""
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        args = self._get_launch_args()
        disable_features = [a for a in args if a.startswith("--disable-features=")]
        assert len(disable_features) == 1, "Multiple --disable-features flags found (last wins!)"
        assert "ServiceWorker" in disable_features[0]
        assert "WebRtcHideLocalIpsWithMdns" in disable_features[0]

    @pytest.mark.asyncio
    async def test_deny_permission_prompts(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert "--deny-permission-prompts" in self._get_launch_args()

    @pytest.mark.asyncio
    async def test_telemetry_suppression_args(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        args = self._get_launch_args()
        for flag in (
            "--disable-breakpad",
            "--no-pings",
            "--disable-domain-reliability",
            "--disable-component-update",
            "--disable-client-side-phishing-detection",
        ):
            assert flag in args, f"Missing telemetry suppression flag: {flag}"

    @pytest.mark.asyncio
    async def test_external_intent_blocking(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert "--disable-external-intent-requests" in self._get_launch_args()

    @pytest.mark.asyncio
    async def test_dialog_suppression_args(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        args = self._get_launch_args()
        assert "--noerrdialogs" in args
        assert "--disable-prompt-on-repost" in args

    @pytest.mark.asyncio
    async def test_no_sandbox_not_present(self):
        """--no-sandbox is a security downgrade and must never be included."""
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert "--no-sandbox" not in self._get_launch_args()

    @pytest.mark.asyncio
    async def test_context_service_workers_blocked(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert self._get_context_kwargs()["service_workers"] == "block"

    @pytest.mark.asyncio
    async def test_context_permissions_empty(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert self._get_context_kwargs()["permissions"] == []

    @pytest.mark.asyncio
    async def test_context_downloads_disabled(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()
        assert self._get_context_kwargs()["accept_downloads"] is False


# ── S3: Scheme Block Route ───────────────────────────────────────


class TestSchemeBlockRoute:
    """S3: Verify context-level URL scheme blocking."""

    @pytest.fixture
    def handler(self):
        """Extract the route handler from _install_scheme_block_route."""
        session = BrowserSession.__new__(BrowserSession)
        mock_context = AsyncMock()
        session._context = mock_context
        return session, mock_context

    async def _extract_handler(self, session, mock_context):
        await session._install_scheme_block_route()
        return mock_context.route.call_args[0][1]

    def _make_route(self, url: str):
        route = AsyncMock()
        route.request = MagicMock()
        route.request.url = url
        return route

    @pytest.mark.asyncio
    async def test_blocks_chrome_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("chrome://settings")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_devtools_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("devtools://devtools/bundled/inspector.html")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_chrome_extension_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("chrome-extension://abc/page.html")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_file_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("file:///etc/passwd")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_view_source_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("view-source://example.com")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_blob_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("blob:https://evil.com/abc")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_data_scheme(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("data:text/html,<script>alert(1)</script>")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_about_newtab(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("about:newtab")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocks_about_srcdoc(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("about:srcdoc")
        await h(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_allows_about_blank(self, handler):
        """about:blank must pass — used by SSRF reset in server.py."""
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("about:blank")
        await h(route)
        route.continue_.assert_called_once()
        route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_https(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("https://example.com")
        await h(route)
        route.continue_.assert_called_once()
        route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_http(self, handler):
        session, ctx = handler
        h = await self._extract_handler(session, ctx)
        route = self._make_route("http://example.com")
        await h(route)
        route.continue_.assert_called_once()
        route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_registered_on_context_not_page(self, handler):
        """Route must be installed at context level, not page level."""
        session, ctx = handler
        await session._install_scheme_block_route()
        ctx.route.assert_called_once()
        assert ctx.route.call_args[0][0] == "**/*"

    def test_blocked_url_schemes_completeness(self):
        """Verify all expected schemes are in the constant."""
        expected = {"chrome://", "devtools://", "chrome-extension://", "file://", "view-source://", "blob:", "data:"}
        assert set(BLOCKED_URL_SCHEMES) == expected


# ── Context Event Handler Registration ──────────────────────────────


class TestContextHandlerRegistration:
    """Verify dialog and page handlers are registered on context during start()."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.mock_pw_cm, self.mock_chromium, self.mock_browser, self.mock_context, self.mock_page = _build_mock_chain()

    @pytest.mark.asyncio
    async def test_dialog_and_page_handlers_registered(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()

        on_calls = self.mock_context.on.call_args_list
        event_names = [c[0][0] for c in on_calls]
        assert "dialog" in event_names
        assert "page" in event_names

    @pytest.mark.asyncio
    async def test_dialog_handler_is_session_method(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()

        on_calls = self.mock_context.on.call_args_list
        dialog_calls = [c for c in on_calls if c[0][0] == "dialog"]
        assert dialog_calls[0][0][1] == session._on_dialog

    @pytest.mark.asyncio
    async def test_page_handler_is_session_method(self):
        with patch("pagemap.browser_session.async_playwright", return_value=self.mock_pw_cm):
            session = BrowserSession()
            await session.start()

        on_calls = self.mock_context.on.call_args_list
        page_calls = [c for c in on_calls if c[0][0] == "page"]
        assert page_calls[0][0][1] == session._on_new_page


# ── New session methods: go_back, scroll, get_scroll_position ───────


class TestGoBackMethod:
    """Tests for BrowserSession.go_back() method."""

    @pytest.mark.asyncio
    async def test_go_back_returns_url_on_success(self):
        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        mock_page.go_back = AsyncMock(return_value=MagicMock())  # non-None = success
        mock_page.wait_for_timeout = AsyncMock()
        mock_page.url = "https://example.com/prev"
        session._page = mock_page

        result = await session.go_back()
        assert result == "https://example.com/prev"
        mock_page.go_back.assert_called_once_with(wait_until="load", timeout=30000)

    @pytest.mark.asyncio
    async def test_go_back_returns_none_on_no_history(self):
        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        mock_page.go_back = AsyncMock(return_value=None)
        session._page = mock_page

        result = await session.go_back()
        assert result is None
        mock_page.wait_for_timeout.assert_not_called()

    @pytest.mark.asyncio
    async def test_go_back_custom_params(self):
        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        mock_page.go_back = AsyncMock(return_value=MagicMock())
        mock_page.wait_for_timeout = AsyncMock()
        mock_page.url = "https://example.com"
        session._page = mock_page

        await session.go_back(wait_until="domcontentloaded", timeout_ms=10000)
        mock_page.go_back.assert_called_once_with(wait_until="domcontentloaded", timeout=10000)


class TestScrollMethods:
    """Tests for BrowserSession.scroll() and get_scroll_position()."""

    @pytest.mark.asyncio
    async def test_get_scroll_position_calls_evaluate(self):
        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        expected = {
            "scrollX": 0,
            "scrollY": 100,
            "scrollWidth": 1280,
            "scrollHeight": 5000,
            "clientWidth": 1280,
            "clientHeight": 800,
        }
        mock_page.evaluate = AsyncMock(return_value=expected)
        session._page = mock_page

        result = await session.get_scroll_position()
        assert result == expected

    @pytest.mark.asyncio
    async def test_scroll_calls_evaluate_with_params(self):
        from pagemap.browser_session import _SCROLL_POSITION_JS

        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        pos_result = {"scrollX": 0, "scrollY": 500}
        mock_page.evaluate = AsyncMock(return_value=pos_result)
        mock_page.wait_for_timeout = AsyncMock()
        session._page = mock_page

        result = await session.scroll(delta_x=0, delta_y=800)

        # First call: scrollBy, second call: position query
        assert mock_page.evaluate.call_count == 2
        first_call = mock_page.evaluate.call_args_list[0]
        assert first_call[0][0] == "([dx, dy]) => window.scrollBy(dx, dy)"
        assert first_call[0][1] == [0, 800]

    @pytest.mark.asyncio
    async def test_scroll_settle_time(self):
        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={})
        mock_page.wait_for_timeout = AsyncMock()
        session._page = mock_page

        await session.scroll(delta_y=100)
        mock_page.wait_for_timeout.assert_called_once_with(500)

    @pytest.mark.asyncio
    async def test_scroll_parameterized_not_fstring(self):
        """Verify scroll uses parameterized evaluate (security: no f-string injection)."""
        session = BrowserSession.__new__(BrowserSession)
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={})
        mock_page.wait_for_timeout = AsyncMock()
        session._page = mock_page

        # Even with suspicious values, they're passed as parameters, not interpolated
        await session.scroll(delta_x=0, delta_y=100)
        first_call = mock_page.evaluate.call_args_list[0]
        js_code = first_call[0][0]
        # The JS code should be a static string, not contain the actual values
        assert "100" not in js_code
        assert "0" not in js_code or js_code == "([dx, dy]) => window.scrollBy(dx, dy)"
