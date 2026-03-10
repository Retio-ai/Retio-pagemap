"""Backward-compat shim — import from pagemap.core.ecommerce.site_hints instead."""

from pagemap.core.ecommerce.site_hints import _HINTS, apply_site_hints  # noqa: F401

__all__ = ["apply_site_hints"]
