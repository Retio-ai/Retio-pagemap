"""Tests for PageMap cache module.

Tests: store/lookup/invalidate, LRU eviction, TTL expiry, URL normalization,
InvalidationReason hard/soft classification, CacheStats counters.
"""

from __future__ import annotations

import time

import pytest

from pagemap import PageMap
from pagemap.cache import (
    CacheStats,
    InvalidationReason,
    PageMapCache,
    normalize_cache_url,
)
from pagemap.dom_change_detector import DomFingerprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page_map(url: str = "https://example.com/page", **overrides) -> PageMap:
    defaults = {
        "url": url,
        "title": "Test Page",
        "page_type": "unknown",
        "interactables": [],
        "pruned_context": "",
        "pruned_tokens": 0,
        "generation_ms": 100.0,
    }
    defaults.update(overrides)
    return PageMap(**defaults)


def _make_fingerprint(**overrides) -> DomFingerprint:
    defaults = {
        "interactive_counts": {"button": 5, "link": 5},
        "total_interactives": 10,
        "has_dialog": False,
        "body_child_count": 5,
        "title": "Test Page",
        "content_hash": 12345,
    }
    defaults.update(overrides)
    return DomFingerprint(**defaults)


# =========================================================================
# URL normalization
# =========================================================================


class TestNormalizeCacheUrl:
    def test_lowercase_scheme_and_netloc(self):
        assert normalize_cache_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_fragment_removed(self):
        assert normalize_cache_url("https://example.com/page#section") == "https://example.com/page"

    def test_query_params_sorted(self):
        result = normalize_cache_url("https://example.com/search?z=1&a=2")
        assert result == "https://example.com/search?a=2&z=1"

    def test_duplicate_query_params_preserved(self):
        result = normalize_cache_url("https://example.com/search?a=1&a=2")
        assert "a=1" in result
        assert "a=2" in result

    def test_path_case_preserved(self):
        result = normalize_cache_url("https://example.com/CamelCase/Path")
        assert "/CamelCase/Path" in result

    def test_trailing_slash_preserved(self):
        assert normalize_cache_url("https://example.com/path/") == "https://example.com/path/"
        assert normalize_cache_url("https://example.com/path") == "https://example.com/path"

    def test_invalid_url_returned_as_is(self):
        assert normalize_cache_url("not a url") == "not a url"


# =========================================================================
# PageMapCache — store/lookup/active
# =========================================================================


class TestPageMapCacheStore:
    def test_store_sets_active(self):
        cache = PageMapCache()
        pm = _make_page_map()
        fp = _make_fingerprint()
        gen_id = cache.store(pm, fp)

        assert cache.active is pm
        assert cache.active_entry is not None
        assert cache.active_entry.generation_id == gen_id

    def test_store_adds_to_lru(self):
        cache = PageMapCache()
        pm = _make_page_map("https://example.com/page1")
        cache.store(pm, _make_fingerprint())

        entry = cache.lookup("https://example.com/page1")
        assert entry is not None
        assert entry.page_map is pm

    def test_store_overwrites_same_url(self):
        cache = PageMapCache()
        pm1 = _make_page_map("https://example.com/page1", title="v1")
        pm2 = _make_page_map("https://example.com/page1", title="v2")

        cache.store(pm1, _make_fingerprint())
        cache.store(pm2, _make_fingerprint())

        entry = cache.lookup("https://example.com/page1")
        assert entry.page_map.title == "v2"
        assert cache.lru_size == 1


# =========================================================================
# Invalidation
# =========================================================================


class TestPageMapCacheInvalidate:
    def test_soft_invalidation_clears_active_preserves_lru(self):
        cache = PageMapCache()
        pm = _make_page_map("https://example.com/page1")
        cache.store(pm, _make_fingerprint())

        cache.invalidate(InvalidationReason.SCROLL)

        assert cache.active is None
        assert cache.lookup("https://example.com/page1") is not None

    def test_hard_invalidation_clears_active_and_lru(self):
        cache = PageMapCache()
        pm = _make_page_map("https://example.com/page1")
        cache.store(pm, _make_fingerprint())

        cache.invalidate(InvalidationReason.NAVIGATION)

        assert cache.active is None
        assert cache.lookup("https://example.com/page1") is None

    def test_invalidate_all_clears_everything(self):
        cache = PageMapCache()
        for i in range(5):
            pm = _make_page_map(f"https://example.com/page{i}")
            cache.store(pm, _make_fingerprint())

        cache.invalidate_all()

        assert cache.active is None
        assert cache.lru_size == 0

    @pytest.mark.parametrize(
        "reason",
        [
            InvalidationReason.NAVIGATION,
            InvalidationReason.NEW_TAB,
            InvalidationReason.SSRF_BLOCKED,
            InvalidationReason.BROWSER_DEAD,
            InvalidationReason.TIMEOUT,
        ],
    )
    def test_hard_reasons(self, reason):
        cache = PageMapCache()
        pm = _make_page_map()
        cache.store(pm, _make_fingerprint())
        cache.invalidate(reason)
        assert cache.active is None
        # Hard reasons remove URL entry
        assert cache.lookup(pm.url) is None

    @pytest.mark.parametrize(
        "reason",
        [
            InvalidationReason.SCROLL,
            InvalidationReason.DOM_MAJOR,
            InvalidationReason.DOM_CONTENT,
            InvalidationReason.WAIT_FOR,
            InvalidationReason.FILL_FORM,
        ],
    )
    def test_soft_reasons(self, reason):
        cache = PageMapCache()
        pm = _make_page_map()
        cache.store(pm, _make_fingerprint())
        cache.invalidate(reason)
        assert cache.active is None
        # Soft reasons preserve URL entry
        assert cache.lookup(pm.url) is not None


# =========================================================================
# LRU eviction
# =========================================================================


class TestPageMapCacheLRU:
    def test_eviction_at_max_capacity(self):
        cache = PageMapCache(max_entries=3)
        urls = [f"https://example.com/page{i}" for i in range(4)]

        for url in urls:
            cache.store(_make_page_map(url), _make_fingerprint())

        # First URL should be evicted
        assert cache.lookup(urls[0]) is None
        # Last 3 should still be present
        for url in urls[1:]:
            assert cache.lookup(url) is not None

        assert cache.lru_size == 3

    def test_access_refreshes_lru_position(self):
        cache = PageMapCache(max_entries=3)
        urls = [f"https://example.com/page{i}" for i in range(3)]

        for url in urls:
            cache.store(_make_page_map(url), _make_fingerprint())

        # Access page0 to refresh its position
        cache.lookup(urls[0])

        # Add page3 — should evict page1 (LRU) instead of page0
        cache.store(_make_page_map("https://example.com/page3"), _make_fingerprint())

        assert cache.lookup(urls[0]) is not None  # refreshed, still present
        assert cache.lookup(urls[1]) is None  # evicted
        assert cache.lookup(urls[2]) is not None


# =========================================================================
# TTL expiry
# =========================================================================


class TestPageMapCacheTTL:
    def test_expired_entry_returns_none(self):
        cache = PageMapCache(default_ttl=0.01)  # 10ms TTL
        pm = _make_page_map()
        cache.store(pm, _make_fingerprint())

        time.sleep(0.02)  # Wait for TTL to expire
        assert cache.lookup(pm.url) is None

    def test_non_expired_entry_returns_entry(self):
        cache = PageMapCache(default_ttl=10.0)
        pm = _make_page_map()
        cache.store(pm, _make_fingerprint())

        entry = cache.lookup(pm.url)
        assert entry is not None


# =========================================================================
# CacheStats
# =========================================================================


class TestCacheStats:
    def test_hit_rate_zero_when_empty(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        stats = CacheStats(hits=3, misses=7)
        assert stats.hit_rate == pytest.approx(0.3)

    def test_record_hit(self):
        cache = PageMapCache()
        cache.record_hit()
        assert cache.stats.hits == 1

    def test_record_miss(self):
        cache = PageMapCache()
        cache.record_miss()
        assert cache.stats.misses == 1

    def test_record_content_refresh(self):
        cache = PageMapCache()
        cache.record_content_refresh()
        assert cache.stats.content_refreshes == 1

    def test_hard_invalidation_counted(self):
        cache = PageMapCache()
        pm = _make_page_map()
        cache.store(pm, _make_fingerprint())
        cache.invalidate(InvalidationReason.NAVIGATION)
        assert cache.stats.hard_invalidations == 1

    def test_soft_invalidation_counted(self):
        cache = PageMapCache()
        pm = _make_page_map()
        cache.store(pm, _make_fingerprint())
        cache.invalidate(InvalidationReason.SCROLL)
        assert cache.stats.soft_invalidations == 1

    def test_eviction_counted(self):
        cache = PageMapCache(max_entries=1)
        cache.store(_make_page_map("https://a.com"), _make_fingerprint())
        cache.store(_make_page_map("https://b.com"), _make_fingerprint())
        assert cache.stats.evictions == 1

    def test_ttl_expiration_counted(self):
        cache = PageMapCache(default_ttl=0.01)
        cache.store(_make_page_map(), _make_fingerprint())
        time.sleep(0.02)
        cache.lookup("https://example.com/page")
        assert cache.stats.ttl_expirations == 1


# =========================================================================
# Edge cases
# =========================================================================


class TestPageMapCacheEdgeCases:
    def test_lookup_nonexistent_url(self):
        cache = PageMapCache()
        assert cache.lookup("https://nonexistent.com") is None

    def test_invalidate_when_empty(self):
        cache = PageMapCache()
        cache.invalidate(InvalidationReason.SCROLL)  # should not raise
        assert cache.active is None

    def test_store_with_none_fingerprint(self):
        cache = PageMapCache()
        pm = _make_page_map()
        gen_id = cache.store(pm, None)
        assert gen_id
        assert cache.active is pm

    def test_generation_id_unique(self):
        cache = PageMapCache()
        ids = set()
        for i in range(10):
            gen_id = cache.store(_make_page_map(f"https://example.com/{i}"), _make_fingerprint())
            ids.add(gen_id)
        assert len(ids) == 10
