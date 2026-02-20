# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""PageMap caching with URL LRU and fingerprint-based invalidation.

Pure Python module — no browser dependencies.

Two layers:
- Active cache: the current page's PageMap (used for execute_action ref validation)
- URL LRU: recently visited pages for fast revisit (navigate_back, same-URL reload)

NOTE: STDIO transport guarantees serial execution.  This class is NOT thread-safe.
For HTTP transport (v0.7.0+), use per-session instances.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlparse, urlunparse

from . import PageMap
from .dom_change_detector import DomFingerprint

logger = logging.getLogger("pagemap.cache")


# ---------------------------------------------------------------------------
# Invalidation reasons
# ---------------------------------------------------------------------------


class InvalidationReason(StrEnum):
    """Why the cache was invalidated — hard/soft auto-determined."""

    NAVIGATION = "navigation"
    NEW_TAB = "new_tab"
    SSRF_BLOCKED = "ssrf_blocked"
    BROWSER_DEAD = "browser_dead"
    TIMEOUT = "timeout"
    SCROLL = "scroll"
    DOM_MAJOR = "dom_major"
    DOM_CONTENT = "dom_content"
    WAIT_FOR = "wait_for"
    FILL_FORM = "fill_form"


# Hard invalidation: active cache AND URL LRU entry removed
_HARD_REASONS = frozenset(
    {
        InvalidationReason.NAVIGATION,
        InvalidationReason.NEW_TAB,
        InvalidationReason.SSRF_BLOCKED,
        InvalidationReason.BROWSER_DEAD,
        InvalidationReason.TIMEOUT,
    }
)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


def normalize_cache_url(url: str) -> str:
    """Normalize URL for cache key: lowercase scheme/netloc, strip fragment, sort query.

    Preserves path case and trailing slash.  Preserves duplicate query params.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path  # preserve case + trailing slash
    # Sort query params (preserves duplicates via parse_qsl)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    sorted_query = "&".join(f"{k}={v}" for k, v in sorted(params))
    # Strip fragment
    return urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    """A cached PageMap with its fingerprint and metadata."""

    page_map: PageMap
    fingerprint: DomFingerprint | None
    created_at: float  # time.monotonic()
    generation_id: str = ""  # uuid hex[:8]
    scroll_y: int = 0

    def is_expired(self, ttl: float) -> bool:
        return (time.monotonic() - self.created_at) > ttl


# ---------------------------------------------------------------------------
# Cache stats (observability)
# ---------------------------------------------------------------------------


@dataclass
class CacheStats:
    """Counters for cache behaviour — used for logging and meta output."""

    hits: int = 0
    misses: int = 0
    content_refreshes: int = 0
    fingerprint_mismatches: int = 0
    ttl_expirations: int = 0
    hard_invalidations: int = 0
    soft_invalidations: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# PageMapCache
# ---------------------------------------------------------------------------


class PageMapCache:
    """Two-layer cache: active (current page) + URL LRU (visited pages).

    TTL is a safety net (90s default); actual freshness is verified by
    fingerprint + content_hash comparison.
    """

    def __init__(self, max_entries: int = 20, default_ttl: float = 90.0) -> None:
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._active: CacheEntry | None = None
        self._url_lru: OrderedDict[str, CacheEntry] = OrderedDict()
        self._stats = CacheStats()

    # -- Layer 1: Active cache --

    @property
    def active(self) -> PageMap | None:
        """The current page's cached PageMap, or None."""
        if self._active is None:
            return None
        return self._active.page_map

    @property
    def active_entry(self) -> CacheEntry | None:
        """The current page's full cache entry."""
        return self._active

    # -- Store --

    def store(
        self,
        page_map: PageMap,
        fingerprint: DomFingerprint | None,
        scroll_y: int = 0,
    ) -> str:
        """Store a PageMap as the active entry and insert into URL LRU.

        Returns the generation_id assigned to this entry.
        """
        gen_id = uuid.uuid4().hex[:8]
        entry = CacheEntry(
            page_map=page_map,
            fingerprint=fingerprint,
            created_at=time.monotonic(),
            generation_id=gen_id,
            scroll_y=scroll_y,
        )
        self._active = entry

        # Insert into URL LRU
        key = normalize_cache_url(page_map.url)
        self._url_lru[key] = entry
        self._url_lru.move_to_end(key)

        # Evict oldest if over capacity
        while len(self._url_lru) > self._max_entries:
            evicted_key, _ = self._url_lru.popitem(last=False)
            self._stats.evictions += 1
            logger.debug("Cache eviction: %s", evicted_key)

        logger.debug(
            "Cache store: url=%s gen=%s lru_size=%d",
            page_map.url,
            gen_id,
            len(self._url_lru),
        )
        return gen_id

    # -- Invalidation --

    def invalidate(self, reason: InvalidationReason) -> None:
        """Invalidate the active cache entry.

        Hard reasons (navigation, new_tab, etc.) also remove the URL LRU entry.
        Soft reasons (scroll, dom_content, etc.) only clear the active cache.
        """
        hard = reason in _HARD_REASONS

        if hard:
            self._stats.hard_invalidations += 1
            # Remove from URL LRU if active entry exists
            if self._active is not None:
                key = normalize_cache_url(self._active.page_map.url)
                self._url_lru.pop(key, None)
        else:
            self._stats.soft_invalidations += 1

        self._active = None
        logger.debug("Cache invalidated: reason=%s hard=%s", reason.value, hard)

    def invalidate_all(self) -> None:
        """Clear everything (browser crash, session reset)."""
        self._active = None
        self._url_lru.clear()
        self._stats.hard_invalidations += 1
        logger.debug("Cache invalidate_all")

    # -- URL LRU lookup --

    def lookup(self, url: str) -> CacheEntry | None:
        """Look up a URL in the LRU cache.  Returns None if missing or TTL-expired."""
        key = normalize_cache_url(url)
        entry = self._url_lru.get(key)
        if entry is None:
            return None
        if entry.is_expired(self._default_ttl):
            self._url_lru.pop(key, None)
            self._stats.ttl_expirations += 1
            logger.debug("Cache TTL expired: %s", key)
            return None
        # Move to end (most recently used)
        self._url_lru.move_to_end(key)
        return entry

    # -- Stats --

    @property
    def stats(self) -> CacheStats:
        return self._stats

    def record_hit(self) -> None:
        self._stats.hits += 1

    def record_miss(self) -> None:
        self._stats.misses += 1

    def record_content_refresh(self) -> None:
        self._stats.content_refreshes += 1

    def record_fingerprint_mismatch(self) -> None:
        self._stats.fingerprint_mismatches += 1

    # -- Introspection --

    @property
    def lru_size(self) -> int:
        return len(self._url_lru)
