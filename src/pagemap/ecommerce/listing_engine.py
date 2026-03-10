"""Backward-compat shim — import from pagemap.core.ecommerce.listing_engine instead."""

from pagemap.core.ecommerce.listing_engine import analyze_listing  # noqa: F401

__all__ = ["analyze_listing"]
