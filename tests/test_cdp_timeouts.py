"""Tests for CDP call individual timeouts (#258) and session leak safety (#260).

Covers:
1. Timeout constants validation
2. detect_interactables_ax() — AX tree timeout, CSS budget timeout
3. detect_interactables_cdp() — Tier 3 Runtime.evaluate timeout
4. browser_session.get_ax_tree() — timeout + reconnect behaviour
5. _cdp_session() detach safety on exceptions and cancellation
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.interactive_detector import (
    _CDP_AX_TREE_TIMEOUT,
    _CDP_CSS_BUDGET,
    _CDP_TIER3_TIMEOUT,
    _cdp_session,
    detect_all,
    detect_interactables_ax,
    detect_interactables_cdp,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_mock_cdp(send_side_effect=None):
    cdp = AsyncMock()
    if send_side_effect is not None:
        cdp.send = AsyncMock(side_effect=send_side_effect)
    cdp.detach = AsyncMock()
    return cdp


def _make_mock_page(cdp):
    page = MagicMock()
    page.context = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=cdp)
    return page


def _make_ax_tree_result(n_nodes=3):
    """Minimal valid AX tree CDP result matching _cdp_ax_nodes_to_tree format."""
    child_ids = [str(i) for i in range(2, n_nodes + 1)]
    nodes = [
        {
            "nodeId": "1",
            "role": {"value": "WebArea"},
            "name": {"value": "Page"},
            "childIds": child_ids,
        },
    ]
    for i in range(2, n_nodes + 1):
        nodes.append(
            {
                "nodeId": str(i),
                "role": {"value": "button"},
                "name": {"value": f"Button {i}"},
                "backendDOMNodeId": 100 + i,
            }
        )
    return {"nodes": nodes}


# ── TestCdpTimeoutConstants ──────────────────────────────────────────


class TestCdpTimeoutConstants:
    """Timeout constants are valid and less than pipeline budget."""

    def test_constants_positive(self):
        assert _CDP_AX_TREE_TIMEOUT > 0
        assert _CDP_CSS_BUDGET > 0
        assert _CDP_TIER3_TIMEOUT > 0

    def test_constants_are_floats(self):
        assert isinstance(_CDP_AX_TREE_TIMEOUT, float)
        assert isinstance(_CDP_CSS_BUDGET, float)
        assert isinstance(_CDP_TIER3_TIMEOUT, float)

    def test_constants_less_than_pipeline_timeout(self):
        """Individual timeouts must be well under the 60s pipeline budget."""
        pipeline_timeout = 60.0
        assert pipeline_timeout > _CDP_AX_TREE_TIMEOUT
        assert pipeline_timeout > _CDP_CSS_BUDGET
        assert pipeline_timeout > _CDP_TIER3_TIMEOUT

    def test_combined_budget_under_pipeline(self):
        """AX + CSS + Tier3 combined should fit within pipeline budget."""
        pipeline_timeout = 60.0
        total = _CDP_AX_TREE_TIMEOUT + _CDP_CSS_BUDGET + _CDP_TIER3_TIMEOUT
        assert pipeline_timeout > total


# ── TestDetectAxTimeout ──────────────────────────────────────────────


class TestDetectAxTimeout:
    """detect_interactables_ax() AX tree and CSS budget timeouts."""

    async def test_happy_path_within_timeout(self):
        """Normal AX tree response returns interactables."""
        cdp = _make_mock_cdp()
        cdp.send = AsyncMock(return_value=_make_ax_tree_result())
        page = _make_mock_page(cdp)

        result = await detect_interactables_ax(page)

        assert len(result) >= 1
        # First call is getFullAXTree, subsequent calls are CSS resolution
        assert cdp.send.call_args_list[0][0][0] == "Accessibility.getFullAXTree"

    async def test_ax_tree_timeout_propagates_to_detect_all(self):
        """AX tree timeout propagates as Exception → detect_all returns [] + warning."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        with patch("pagemap.interactive_detector._CDP_AX_TREE_TIMEOUT", 0.05):
            cdp = _make_mock_cdp(send_side_effect=_slow_send)
            page = _make_mock_page(cdp)

            elements, warnings = await detect_all(page, enable_tier3=False)

            assert elements == []
            assert len(warnings) == 1
            assert "failed" in warnings[0].lower()

    async def test_css_budget_timeout_preserves_partial_selectors(self):
        """CSS budget timeout preserves already-resolved selectors."""

        call_count = 0

        async def _slow_css_send(method, params=None):
            nonlocal call_count
            if method == "Accessibility.getFullAXTree":
                return _make_ax_tree_result(n_nodes=4)
            # First DOM.resolveNode succeeds, subsequent ones are slow
            call_count += 1
            if call_count <= 2:
                # First pair (resolveNode + callFunctionOn) succeeds
                if method == "DOM.resolveNode":
                    return {"object": {"objectId": "obj-1"}}
                if method == "Runtime.callFunctionOn":
                    return {"result": {"value": "#resolved-btn"}}
            # After first pair, be slow to trigger timeout
            await asyncio.sleep(999)

        with patch("pagemap.interactive_detector._CDP_CSS_BUDGET", 0.1):
            cdp = _make_mock_cdp(send_side_effect=_slow_css_send)
            page = _make_mock_page(cdp)

            result = await detect_interactables_ax(page)

            # Should still return interactables (AX tree succeeded)
            assert len(result) >= 1
            # At least one selector was resolved before timeout
            assert any(r.selector for r in result)

    async def test_css_budget_timeout_logs_warning(self, caplog):
        """CSS budget timeout logs a warning with resolution count."""

        async def _slow_css_send(method, params=None):
            if method == "Accessibility.getFullAXTree":
                return _make_ax_tree_result(n_nodes=3)
            # All CSS resolution calls are slow
            await asyncio.sleep(999)

        import logging

        with (
            patch("pagemap.interactive_detector._CDP_CSS_BUDGET", 0.05),
            caplog.at_level(logging.WARNING),
        ):
            cdp = _make_mock_cdp(send_side_effect=_slow_css_send)
            page = _make_mock_page(cdp)

            await detect_interactables_ax(page)

            assert any("CSS selector resolution timed out" in r.message for r in caplog.records)

    async def test_ax_timeout_detaches_session(self):
        """AX tree timeout still triggers cdp.detach() via _cdp_session."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        with patch("pagemap.interactive_detector._CDP_AX_TREE_TIMEOUT", 0.05):
            cdp = _make_mock_cdp(send_side_effect=_slow_send)
            page = _make_mock_page(cdp)

            with pytest.raises(TimeoutError):
                await detect_interactables_ax(page)

            cdp.detach.assert_called_once()


# ── TestDetectCdpTimeout ─────────────────────────────────────────────


class TestDetectCdpTimeout:
    """detect_interactables_cdp() Tier 3 timeout handling."""

    async def test_happy_path_runtime_eval(self):
        """Normal Runtime.evaluate returns Tier 3 elements."""
        cdp = _make_mock_cdp()
        cdp.send = AsyncMock(
            return_value={
                "result": {
                    "value": {
                        "error": None,
                        "elements": [
                            {"tag": "div", "role": "div", "name": "Action", "textFallback": ""},
                        ],
                    }
                }
            }
        )
        page = _make_mock_page(cdp)

        result = await detect_interactables_cdp(page)

        assert len(result) == 1
        assert result[0].name == "Action"

    async def test_runtime_eval_timeout_returns_empty(self):
        """Tier 3 timeout returns empty list, not exception."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        with patch("pagemap.interactive_detector._CDP_TIER3_TIMEOUT", 0.05):
            cdp = _make_mock_cdp(send_side_effect=_slow_send)
            page = _make_mock_page(cdp)

            result = await detect_interactables_cdp(page)

            assert result == []

    async def test_timeout_detaches_session(self):
        """Tier 3 timeout still calls cdp.detach()."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        with patch("pagemap.interactive_detector._CDP_TIER3_TIMEOUT", 0.05):
            cdp = _make_mock_cdp(send_side_effect=_slow_send)
            page = _make_mock_page(cdp)

            await detect_interactables_cdp(page)

            cdp.detach.assert_called_once()

    async def test_session_creation_failure_propagates(self):
        """new_cdp_session failure propagates (detect_all isolates it)."""
        page = MagicMock()
        page.context = MagicMock()
        page.context.new_cdp_session = AsyncMock(side_effect=Exception("Target closed"))

        with pytest.raises(Exception, match="Target closed"):
            await detect_interactables_cdp(page)

    async def test_session_creation_failure_isolated_by_detect_all(self):
        """detect_all isolates Tier 3 session creation failure."""
        # Tier 1-2: succeeds with empty AX tree
        cdp_ax = _make_mock_cdp()
        cdp_ax.send = AsyncMock(return_value={"nodes": []})

        mock_page = MagicMock()
        mock_page.context = MagicMock()

        call_count = 0

        async def _new_cdp_session(page):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cdp_ax  # Tier 1-2 session
            raise Exception("Target closed")  # Tier 3 session fails

        mock_page.context.new_cdp_session = AsyncMock(side_effect=_new_cdp_session)

        elements, warnings = await detect_all(mock_page, enable_tier3=True)

        assert elements == []  # empty AX tree, Tier 3 failed
        assert any("Tier 3" in w for w in warnings)


# ── TestGetAxTreeTimeout ─────────────────────────────────────────────


class TestGetAxTreeTimeout:
    """browser_session.BrowserSession.get_ax_tree() timeout behavior."""

    def _make_session(self, cdp):
        """Create a minimal BrowserSession-like mock with get_ax_tree."""
        from pagemap.browser_session import BrowserSession

        session = MagicMock(spec=BrowserSession)
        session._cdp_session = cdp
        session.get_cdp_session = AsyncMock(return_value=cdp)
        # Bind the real method
        session.get_ax_tree = BrowserSession.get_ax_tree.__get__(session, BrowserSession)
        return session

    async def test_timeout_attempt0_triggers_reconnect(self):
        """Timeout on first attempt triggers reconnect (attempt 0)."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        cdp_stale = _make_mock_cdp(send_side_effect=_slow_send)
        cdp_fresh = _make_mock_cdp()
        cdp_fresh.send = AsyncMock(return_value=_make_ax_tree_result())

        call_count = 0

        async def _get_cdp():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cdp_stale
            return cdp_fresh

        with patch("pagemap.browser_session._CDP_AX_TREE_TIMEOUT", 0.05):
            session = self._make_session(cdp_stale)
            session.get_cdp_session = AsyncMock(side_effect=_get_cdp)

            result = await session.get_ax_tree()

            assert result is not None
            assert session._cdp_session is None or call_count == 2

    async def test_timeout_both_attempts_raises(self):
        """Timeout on both attempts raises TimeoutError."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        cdp = _make_mock_cdp(send_side_effect=_slow_send)

        with patch("pagemap.browser_session._CDP_AX_TREE_TIMEOUT", 0.05):
            session = self._make_session(cdp)

            with pytest.raises(TimeoutError):
                await session.get_ax_tree()

    async def test_reconnect_success_after_timeout(self):
        """After timeout+reconnect, second attempt succeeds."""

        async def _slow_send(*args, **kwargs):
            await asyncio.sleep(999)

        cdp_stale = _make_mock_cdp(send_side_effect=_slow_send)
        cdp_ok = _make_mock_cdp()
        cdp_ok.send = AsyncMock(return_value={"nodes": []})

        calls = []

        async def _get_cdp():
            calls.append(1)
            if len(calls) == 1:
                return cdp_stale
            return cdp_ok

        with patch("pagemap.browser_session._CDP_AX_TREE_TIMEOUT", 0.05):
            session = self._make_session(cdp_stale)
            session.get_cdp_session = AsyncMock(side_effect=_get_cdp)

            result = await session.get_ax_tree()

            # Empty nodes → returns None
            assert result is None
            assert len(calls) == 2

    async def test_normal_exception_also_reconnects(self):
        """Non-timeout Exception on attempt 0 still reconnects."""
        cdp_bad = _make_mock_cdp(send_side_effect=Exception("Protocol error"))
        cdp_ok = _make_mock_cdp()
        cdp_ok.send = AsyncMock(return_value=_make_ax_tree_result())

        calls = []

        async def _get_cdp():
            calls.append(1)
            if len(calls) == 1:
                return cdp_bad
            return cdp_ok

        session = self._make_session(cdp_bad)
        session.get_cdp_session = AsyncMock(side_effect=_get_cdp)

        result = await session.get_ax_tree()

        assert result is not None
        assert len(calls) == 2


# ── TestDetachSafety ─────────────────────────────────────────────────


class TestDetachSafety:
    """_cdp_session() ensures detach is always called."""

    async def test_detach_always_called_on_timeout(self):
        """detach() called even when body raises TimeoutError."""
        cdp = _make_mock_cdp()
        page = _make_mock_page(cdp)

        with pytest.raises(TimeoutError):
            async with _cdp_session(page) as _cdp:
                raise TimeoutError("test")

        cdp.detach.assert_called_once()

    async def test_detach_failure_suppressed(self):
        """If detach() itself raises, it's suppressed (no double exception)."""
        cdp = _make_mock_cdp()
        cdp.detach = AsyncMock(side_effect=Exception("detach failed"))
        page = _make_mock_page(cdp)

        # Should not raise despite detach failure
        async with _cdp_session(page) as _cdp:
            pass

        cdp.detach.assert_called_once()

    async def test_detach_on_non_timeout_exception(self):
        """detach() called on arbitrary exceptions."""
        cdp = _make_mock_cdp()
        page = _make_mock_page(cdp)

        with pytest.raises(RuntimeError):
            async with _cdp_session(page) as _cdp:
                raise RuntimeError("arbitrary error")

        cdp.detach.assert_called_once()

    async def test_detach_called_on_normal_exit(self):
        """detach() called even on clean context manager exit."""
        cdp = _make_mock_cdp()
        page = _make_mock_page(cdp)

        async with _cdp_session(page) as _cdp:
            pass

        cdp.detach.assert_called_once()


# ── TestCancellationSafety ───────────────────────────────────────────


class TestCancellationSafety:
    """_cdp_session() handles outer task cancellation (asyncio.wait_for)."""

    async def test_outer_cancel_still_detaches(self):
        """When outer task is cancelled, detach still runs via shield()."""
        cdp = _make_mock_cdp()
        page = _make_mock_page(cdp)

        async def _use_cdp():
            async with _cdp_session(page) as _cdp:
                await asyncio.sleep(999)  # will be cancelled

        task = asyncio.create_task(_use_cdp())
        await asyncio.sleep(0.01)  # let task start
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Shield ensures detach was called despite cancellation
        cdp.detach.assert_called_once()

    async def test_cancelled_error_not_swallowed_by_cdp_session(self):
        """CancelledError propagates through _cdp_session (not caught as Exception)."""
        cdp = _make_mock_cdp()
        page = _make_mock_page(cdp)

        async def _use_cdp():
            async with _cdp_session(page) as _cdp:
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _use_cdp()

        cdp.detach.assert_called_once()

    async def test_wait_for_cancellation_detaches(self):
        """asyncio.wait_for timeout cancels task but detach still runs."""
        cdp = _make_mock_cdp()
        page = _make_mock_page(cdp)

        async def _use_cdp():
            async with _cdp_session(page) as _cdp:
                await asyncio.sleep(999)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_use_cdp(), timeout=0.05)

        cdp.detach.assert_called_once()
