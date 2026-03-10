"""Backward-compat shim — import from pagemap.core.diagnostics.spa_loader instead."""

from pagemap.core.diagnostics.spa_loader import parse_spa_signals  # noqa: F401

__all__ = ["parse_spa_signals"]
