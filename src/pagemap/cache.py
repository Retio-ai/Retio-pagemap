"""Backward-compat shim — import from pagemap.core.cache instead."""

from pagemap.core.cache import (  # noqa: F401
    CacheEntry,
    CacheStats,
    InvalidationReason,
    PageMapCache,
    normalize_cache_url,
)

__all__ = ["CacheEntry", "CacheStats", "InvalidationReason", "PageMapCache", "normalize_cache_url"]
