"""Backward-compat shim — import from pagemap.core.pruning instead."""

from pagemap.core.pruning import (  # noqa: F401
    ChunkType,
    HtmlChunk,
    PageType,
    PruneReason,
    PruningContext,
    PruningError,
    SchemaName,
    StageAlphas,
)

__all__ = [
    "ChunkType",
    "HtmlChunk",
    "PageType",
    "PruneReason",
    "PruningContext",
    "PruningError",
    "SchemaName",
    "StageAlphas",
]
