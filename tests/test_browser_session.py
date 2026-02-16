"""Tests for browser session configuration and utilities.

Tests BrowserConfig defaults, security launch args, CDP AX tree conversion,
and property guards. Does not require a running browser.
"""

import pytest

from pagemap.browser_session import (
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
