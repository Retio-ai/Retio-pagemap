"""Backward-compat shim — import from pagemap.core.pruning.pipeline instead."""

from pagemap.core.pruning.pipeline import PruningResult, prune_page  # noqa: F401

__all__ = ["PruningResult", "prune_page"]
