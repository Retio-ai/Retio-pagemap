"""Backward-compat shim — import from pagemap.server.context instead."""

from pagemap.server.context import RequestContext  # noqa: F401

__all__ = ["RequestContext"]
