# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for hybrid navigation strategy (Phase C)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pagemap.browser_session import BrowserConfig, BrowserSession, NavigationResult


class TestNavigationResult:
    def test_creation(self):
        nr = NavigationResult(strategy="networkidle", settle_metrics={"waited_ms": 100})
        assert nr.strategy == "networkidle"
        assert nr.settle_metrics == {"waited_ms": 100}

    def test_frozen(self):
        nr = NavigationResult(strategy="load", settle_metrics=None)
        with pytest.raises(AttributeError):
            nr.strategy = "other"


class TestHybridNavigate:
    """Unit tests for navigate() hybrid strategy using mocks."""

    def _make_session(self, *, wait_strategy="hybrid", networkidle_budget_ms=6000):
        config = BrowserConfig(
            wait_strategy=wait_strategy,
            networkidle_budget_ms=networkidle_budget_ms,
        )
        session = BrowserSession(config)
        # Mock page + context
        session._page = MagicMock()
        session._page.url = "about:blank"
        session._page.goto = AsyncMock()
        session._page.wait_for_load_state = AsyncMock()
        session._context = MagicMock()
        session._context.clear_cookies = AsyncMock()
        session._context.set_extra_http_headers = AsyncMock()
        return session

    async def test_networkidle_strategy(self):
        """Legacy networkidle strategy."""
        session = self._make_session(wait_strategy="networkidle")
        session._page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        result = await session.navigate("https://example.com")

        session._page.goto.assert_called_once_with("https://example.com", wait_until="networkidle", timeout=30000)
        assert result.strategy == "networkidle"

    async def test_load_strategy(self):
        """Pure load strategy (no networkidle attempt)."""
        session = self._make_session(wait_strategy="load")
        session._page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        result = await session.navigate("https://example.com")

        session._page.goto.assert_called_once_with("https://example.com", wait_until="load", timeout=30000)
        assert result.strategy == "load"

    async def test_hybrid_networkidle_achieved(self):
        """Hybrid: networkidle achieved within budget."""
        session = self._make_session(wait_strategy="hybrid")
        session._page.wait_for_load_state = AsyncMock()  # resolves immediately
        session._page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        result = await session.navigate("https://example.com")

        session._page.goto.assert_called_once_with("https://example.com", wait_until="load", timeout=30000)
        assert result.strategy == "networkidle"

    async def test_hybrid_networkidle_timeout(self):
        """Hybrid: networkidle budget exceeded → load+settle."""
        session = self._make_session(wait_strategy="hybrid", networkidle_budget_ms=100)

        # wait_for_load_state never resolves (long polling page)
        async def _hang_forever(state):
            await asyncio.sleep(999)

        session._page.wait_for_load_state = _hang_forever
        session._page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        result = await session.navigate("https://example.com")
        assert result.strategy == "load+settle"

    async def test_hybrid_networkidle_error_non_fatal(self):
        """Hybrid: networkidle completes with non-fatal error → load+settle."""
        session = self._make_session(wait_strategy="hybrid")

        async def _raise_error(state):
            raise RuntimeError("some playwright error")

        session._page.wait_for_load_state = _raise_error
        session._page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        result = await session.navigate("https://example.com")
        assert result.strategy == "load+settle"

    async def test_hybrid_browser_dead_propagates(self):
        """Hybrid: browser death error during networkidle → re-raised."""
        session = self._make_session(wait_strategy="hybrid")

        async def _raise_dead(state):
            raise RuntimeError("browser has been closed")

        session._page.wait_for_load_state = _raise_dead
        session._page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        with pytest.raises(RuntimeError, match="browser has been closed"):
            await session.navigate("https://example.com")
