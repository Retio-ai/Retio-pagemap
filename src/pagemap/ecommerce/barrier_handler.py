"""Backward-compat shim — import from pagemap.core.ecommerce.barrier_handler instead."""

from pagemap.core.ecommerce.barrier_handler import detect_barriers  # noqa: F401

__all__ = ["detect_barriers"]
