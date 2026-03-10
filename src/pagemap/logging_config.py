"""Backward-compat shim — import from pagemap.server.logging_config instead."""

from pagemap.server.logging_config import configure  # noqa: F401

__all__ = ["configure"]
