# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for batch_get_page_map (Phase F)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pagemap.server as srv
from pagemap.browser_session import BrowserSession
from pagemap.cache import PageMapCache


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset server state before each test."""
    srv._state.cache = PageMapCache()
    srv._state.template_cache = srv.InMemoryTemplateCache()
    srv._state.tool_lock = asyncio.Lock()
    yield


def _make_page_map(url="https://example.com"):
    """Minimal PageMap for testing."""
    from pagemap import PageMap

    return PageMap(
        url=url,
        title="Test",
        page_type="unknown",
        interactables=[],
        pruned_context="test",
        pruned_tokens=10,
        generation_ms=50.0,
        images=[],
        metadata={},
        warnings=[],
    )


class TestBatchInputValidation:
    @pytest.mark.asyncio
    async def test_empty_urls(self):
        result = await srv._batch_get_page_map_impl([], 5)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_too_many_urls(self):
        urls = [f"https://example.com/{i}" for i in range(15)]
        result = await srv._batch_get_page_map_impl(urls, 5)
        data = json.loads(result)
        assert "error" in data
        assert "Maximum" in data["error"]

    @pytest.mark.asyncio
    async def test_ssrf_blocked_url(self):
        urls = ["https://169.254.169.254/latest/meta-data/"]
        result = await srv._batch_get_page_map_impl(urls, 5)
        data = json.loads(result)
        # Should have error for blocked URL but still return structured response
        assert "results" in data
        assert data["results"][0]["status"] == "error"


class TestBatchProcessing:
    @pytest.mark.asyncio
    async def test_deduplication(self):
        """Duplicate URLs should be deduplicated."""
        # We need mocks for the session
        mock_session = MagicMock(spec=BrowserSession)
        mock_session.config = MagicMock()
        mock_session.config.timeout_ms = 30000
        mock_session.config.settle_quiet_ms = 200
        mock_session.config.settle_max_ms = 3000

        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Test")
        mock_page.content = AsyncMock(return_value="<html><body>test</body></html>")
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value={"waited_ms": 50, "mutations": 0, "reason": "quiet"})

        mock_session.create_batch_page = AsyncMock(return_value=mock_page)
        mock_session.close_batch_page = AsyncMock()
        mock_session.wait_for_dom_settle_on = AsyncMock(return_value=None)

        page_map = _make_page_map()

        with (
            patch("pagemap.server._get_session", new=AsyncMock(return_value=mock_session)),
            patch(
                "pagemap.server._validate_url_with_dns",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "pagemap.page_map_builder.build_page_map_from_page",
                new=AsyncMock(return_value=page_map),
            ),
            patch(
                "pagemap.server.capture_dom_fingerprint",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await srv._batch_get_page_map_impl(["https://example.com", "https://example.com"], 5)
            data = json.loads(result)
            # Only 1 result since duplicates are removed
            assert data["summary"]["total"] == 2
            assert len(data["results"]) == 1


class TestCacheStoreInLruOnly:
    def test_store_in_lru_only_does_not_affect_active(self):
        cache = PageMapCache()
        pm = _make_page_map("https://a.com")
        cache.store(pm, None)
        assert cache.active is pm

        pm2 = _make_page_map("https://b.com")
        cache.store_in_lru_only(pm2, None)

        # Active should still be pm (not pm2)
        assert cache.active is pm
        # But pm2 should be in LRU
        entry = cache.lookup("https://b.com")
        assert entry is not None
        assert entry.page_map is pm2

    def test_store_in_lru_only_eviction(self):
        cache = PageMapCache(max_entries=2)
        for i in range(3):
            pm = _make_page_map(f"https://example.com/{i}")
            cache.store_in_lru_only(pm, None)

        # Oldest should have been evicted
        assert cache.lru_size == 2


class TestBrowserSessionBatch:
    @pytest.mark.asyncio
    async def test_create_and_close_batch_page(self):
        session = BrowserSession.__new__(BrowserSession)
        session._batch_pages = set()

        mock_page = MagicMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.close = AsyncMock()

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        session._context = mock_context

        page = await session.create_batch_page()
        assert page is mock_page
        assert page in session._batch_pages

        await session.close_batch_page(page)
        assert page not in session._batch_pages
        mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_new_page_skips_batch_pages(self):
        session = BrowserSession.__new__(BrowserSession)
        session._pending_new_page = None
        session._batch_pages = set()

        mock_page = MagicMock()
        mock_page.url = "https://batch.com"
        session._batch_pages.add(mock_page)

        await session._on_new_page(mock_page)
        # Should NOT store as pending (it's a batch page)
        assert session._pending_new_page is None
