"""Tests for H1-H4 latency optimizations.

H2: Unused regex removal (extract_text_lines regression)
H1: Orchestrator parallelization (_detect_all_safe + asyncio.gather)
H3: CDP session reuse (get_ax_tree with cached session)
H4: Dynamic navigation wait (wait_for_dom_settle + MutationObserver)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.browser_session import _DOM_SETTLE_JS, BrowserConfig, BrowserSession
from pagemap.pruned_context_builder import _extract_text_lines

# ── H2: _extract_text_lines regression ──────────────────────────────


class TestExtractTextLinesRegression:
    """Verify _extract_text_lines preserves headings and filters correctly after regex removal."""

    def test_preserves_heading_text(self):
        html = "<h1>Product Title</h1><p>Description here</p>"
        lines = _extract_text_lines(html)
        assert any("Product Title" in line for line in lines)

    def test_preserves_nested_heading_text(self):
        html = "<h2><span>Category</span> Items</h2><p>More text</p>"
        lines = _extract_text_lines(html)
        assert any("Category" in line for line in lines)

    def test_removes_script_style(self):
        html = "<script>var x=1;</script><style>.a{}</style><p>Visible</p>"
        lines = _extract_text_lines(html)
        assert any("Visible" in line for line in lines)
        assert not any("var x" in line for line in lines)
        assert not any(".a{}" in line for line in lines)

    def test_empty_html_returns_empty(self):
        assert _extract_text_lines("") == []

    def test_golden_output_mixed_content(self):
        html = (
            "<h1>Main Title</h1>"
            "<script>alert('x')</script>"
            "<noscript>Enable JS</noscript>"
            "<p>First paragraph</p>"
            "<h2>Subtitle</h2>"
            "<p>Second paragraph</p>"
        )
        lines = _extract_text_lines(html)
        text = " ".join(lines)
        assert "Main Title" in text
        assert "First paragraph" in text
        assert "Subtitle" in text
        assert "Second paragraph" in text
        assert "alert" not in text
        assert "Enable JS" not in text


# ── H1: _detect_all_safe ────────────────────────────────────────────


class TestDetectAllSafe:
    """Test error isolation wrapper for detect_all."""

    async def test_success_passthrough(self):
        from pagemap import Interactable
        from pagemap.page_map_builder import _detect_all_safe

        mock_interactable = Interactable(ref=1, role="button", name="Buy", affordance="click", region="main", tier=1)
        with patch("pagemap.page_map_builder.detect_all") as mock_detect:
            mock_detect.return_value = ([mock_interactable], ["warn1"])
            result, warnings = await _detect_all_safe(MagicMock(), True)
        assert len(result) == 1
        assert result[0].name == "Buy"
        assert warnings == ["warn1"]

    async def test_error_isolation(self):
        from pagemap.page_map_builder import _detect_all_safe

        with patch("pagemap.page_map_builder.detect_all") as mock_detect:
            mock_detect.side_effect = RuntimeError("CDP crash")
            result, warnings = await _detect_all_safe(MagicMock(), True)
        assert result == []
        assert len(warnings) == 1
        assert "RuntimeError" in warnings[0]

    async def test_cancelled_error_propagates(self):
        from pagemap.page_map_builder import _detect_all_safe

        with patch("pagemap.page_map_builder.detect_all") as mock_detect:
            mock_detect.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await _detect_all_safe(MagicMock(), True)


# ── H1: Parallel PageMap build ──────────────────────────────────────


class TestParallelPageMapBuild:
    """Verify build_page_map_live calls detect + HTML in parallel."""

    def _make_session(self):
        session = MagicMock()
        session.page = MagicMock()
        session.navigate = AsyncMock()
        session.get_page_url = AsyncMock(return_value="https://example.com/products/1")
        session.get_page_title = AsyncMock(return_value="Test Product")
        session.get_page_html = AsyncMock(return_value="<html><body><h1>Product</h1></body></html>")
        return session

    async def test_both_called(self):
        from pagemap.page_map_builder import build_page_map_live

        session = self._make_session()
        with patch("pagemap.page_map_builder._detect_all_safe") as mock_safe:
            mock_safe.return_value = ([], [])
            pm = await build_page_map_live(session, enable_tier3=False)
        mock_safe.assert_called_once()
        session.get_page_html.assert_called_once()
        assert pm.url == "https://example.com/products/1"

    async def test_detect_failure_yields_valid_pagemap(self):
        from pagemap.page_map_builder import build_page_map_live

        session = self._make_session()
        with patch("pagemap.page_map_builder._detect_all_safe") as mock_safe:
            mock_safe.return_value = ([], ["detection failed"])
            pm = await build_page_map_live(session, enable_tier3=False)
        assert pm.interactables == []
        assert "detection failed" in pm.warnings

    async def test_html_failure_propagates(self):
        from pagemap.page_map_builder import build_page_map_live

        session = self._make_session()
        session.get_page_html.side_effect = RuntimeError("page crashed")
        with patch("pagemap.page_map_builder._detect_all_safe") as mock_safe:
            mock_safe.return_value = ([], [])
            with pytest.raises(RuntimeError, match="page crashed"):
                await build_page_map_live(session, enable_tier3=False)


# ── H3: CDP session reuse ──────────────────────────────────────────


class TestCdpSessionReuse:
    """Test get_ax_tree reuses cached CDP session."""

    def _make_session(self):
        session = BrowserSession.__new__(BrowserSession)
        session.config = BrowserConfig()
        session._cdp_session = None
        mock_context = AsyncMock()
        session._context = mock_context
        mock_page = AsyncMock()
        session._page = mock_page
        return session, mock_context

    async def test_reuses_existing_session(self):
        session, mock_context = self._make_session()
        mock_cdp = AsyncMock()
        mock_cdp.send = AsyncMock(
            return_value={
                "nodes": [
                    {
                        "nodeId": "1",
                        "role": {"value": "WebArea"},
                        "name": {"value": "Page"},
                        "childIds": [],
                        "properties": [],
                    }
                ]
            }
        )
        session._cdp_session = mock_cdp

        tree = await session.get_ax_tree()
        assert tree is not None
        assert tree["role"] == "WebArea"
        # Should NOT have created a new session
        mock_context.new_cdp_session.assert_not_called()

    async def test_creates_session_when_none(self):
        session, mock_context = self._make_session()
        mock_cdp = AsyncMock()
        mock_cdp.send = AsyncMock(
            return_value={
                "nodes": [
                    {
                        "nodeId": "1",
                        "role": {"value": "WebArea"},
                        "name": {"value": "Page"},
                        "childIds": [],
                        "properties": [],
                    }
                ]
            }
        )
        mock_context.new_cdp_session = AsyncMock(return_value=mock_cdp)

        tree = await session.get_ax_tree()
        assert tree is not None
        mock_context.new_cdp_session.assert_called_once()

    async def test_stale_session_reconnects(self):
        session, mock_context = self._make_session()

        stale_cdp = AsyncMock()
        stale_cdp.send = AsyncMock(side_effect=Exception("session closed"))
        stale_cdp.detach = AsyncMock()
        session._cdp_session = stale_cdp

        fresh_cdp = AsyncMock()
        fresh_cdp.send = AsyncMock(
            return_value={
                "nodes": [
                    {
                        "nodeId": "1",
                        "role": {"value": "WebArea"},
                        "name": {"value": "Reconnected"},
                        "childIds": [],
                        "properties": [],
                    }
                ]
            }
        )
        mock_context.new_cdp_session = AsyncMock(return_value=fresh_cdp)

        tree = await session.get_ax_tree()
        assert tree is not None
        assert tree["name"] == "Reconnected"
        stale_cdp.detach.assert_called_once()
        mock_context.new_cdp_session.assert_called_once()

    async def test_double_failure_raises(self):
        session, mock_context = self._make_session()

        stale_cdp = AsyncMock()
        stale_cdp.send = AsyncMock(side_effect=Exception("session closed"))
        stale_cdp.detach = AsyncMock()
        session._cdp_session = stale_cdp

        fresh_cdp = AsyncMock()
        fresh_cdp.send = AsyncMock(side_effect=Exception("still broken"))
        mock_context.new_cdp_session = AsyncMock(return_value=fresh_cdp)

        with pytest.raises(Exception, match="still broken"):
            await session.get_ax_tree()


# ── H4: wait_for_dom_settle ─────────────────────────────────────────


class TestWaitForDomSettle:
    """Test DOM settle method."""

    def _make_session(self):
        session = BrowserSession.__new__(BrowserSession)
        session.config = BrowserConfig()
        mock_page = AsyncMock()
        session._page = mock_page
        return session, mock_page

    async def test_passes_default_params(self):
        session, mock_page = self._make_session()
        mock_page.evaluate = AsyncMock(return_value={"waited_ms": 200, "mutations": 0, "reason": "quiet"})

        result = await session.wait_for_dom_settle()
        mock_page.evaluate.assert_called_once_with(_DOM_SETTLE_JS, [200, 3000])
        assert result["reason"] == "quiet"

    async def test_custom_override(self):
        session, mock_page = self._make_session()
        mock_page.evaluate = AsyncMock(return_value={"waited_ms": 100, "mutations": 5, "reason": "quiet"})

        result = await session.wait_for_dom_settle(quiet_ms=100, max_ms=1500)
        mock_page.evaluate.assert_called_once_with(_DOM_SETTLE_JS, [100, 1500])
        assert result is not None

    async def test_failure_returns_none(self):
        session, mock_page = self._make_session()
        mock_page.evaluate = AsyncMock(side_effect=Exception("page crashed"))

        result = await session.wait_for_dom_settle()
        assert result is None

    async def test_metrics_returned(self):
        session, mock_page = self._make_session()
        mock_page.evaluate = AsyncMock(return_value={"waited_ms": 450, "mutations": 12, "reason": "timeout"})

        result = await session.wait_for_dom_settle()
        assert result["waited_ms"] == 450
        assert result["mutations"] == 12
        assert result["reason"] == "timeout"


# ── H4: Dynamic navigate wait ──────────────────────────────────────


class TestDynamicNavigateWait:
    """Test that navigate/click/go_back/scroll use wait_for_dom_settle."""

    def _make_session(self):
        session = BrowserSession.__new__(BrowserSession)
        session.config = BrowserConfig()
        mock_page = AsyncMock()
        mock_page.url = "https://example.com"
        session._page = mock_page
        session._context = AsyncMock()
        session.wait_for_dom_settle = AsyncMock(return_value=None)
        return session, mock_page

    async def test_navigate_uses_dom_settle(self):
        session, mock_page = self._make_session()
        mock_page.goto = AsyncMock()

        await session.navigate("https://example.com/page")
        session.wait_for_dom_settle.assert_called_once_with()
        # Must NOT call wait_for_timeout(1500)
        mock_page.wait_for_timeout.assert_not_called()

    async def test_navigate_clears_cookies_on_domain_change(self):
        session, mock_page = self._make_session()
        mock_page.url = "https://old-domain.com/page"
        mock_page.goto = AsyncMock()

        await session.navigate("https://new-domain.com/page")
        session._context.clear_cookies.assert_called_once()

    async def test_navigate_no_cookie_clear_same_domain(self):
        session, mock_page = self._make_session()
        mock_page.url = "https://example.com/page1"
        mock_page.goto = AsyncMock()

        await session.navigate("https://example.com/page2")
        session._context.clear_cookies.assert_not_called()
