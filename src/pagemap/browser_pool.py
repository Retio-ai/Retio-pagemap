"""Backward-compat shim — import from pagemap.server.browser_pool instead."""

from pagemap.server.browser_pool import (  # noqa: F401
    BrowserPool,
    PoolHealth,
    _PooledContext,
)

__all__ = ["BrowserPool", "PoolHealth", "_PooledContext"]
