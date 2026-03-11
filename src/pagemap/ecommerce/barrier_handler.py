"""Backward-compat shim — import from pagemap.core.ecommerce.barrier_handler instead."""

from pagemap.core.ecommerce.barrier_handler import _find_dismiss_ref, detect_barriers  # noqa: F401

__all__ = ["detect_barriers", "_find_dismiss_ref"]
