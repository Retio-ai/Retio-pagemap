"""Backward-compat shim — import from pagemap.core.protocols instead."""

from pagemap.core.protocols import BrowserSessionProtocol  # noqa: F401

__all__ = ["BrowserSessionProtocol"]
