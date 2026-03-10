"""Backward-compat shim — import from pagemap.core.diagnostics.auto_remap instead."""

from pagemap.core.diagnostics.auto_remap import _REMAPPABLE, MAX_AUTO_REMAPS, maybe_auto_remap  # noqa: F401

__all__ = ["MAX_AUTO_REMAPS", "maybe_auto_remap"]
