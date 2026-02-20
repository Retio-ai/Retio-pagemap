# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 0 HTML pruning engine.

Core data structures for the AXE-style XPath-based pruning pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ChunkType(StrEnum):
    """Atomic chunk classification."""

    TABLE = "table"
    LIST = "list"
    TEXT_BLOCK = "text_block"
    HEADING = "heading"
    MEDIA = "media"
    FORM = "form"
    META = "meta"  # JSON-LD, OG meta
    RSC_DATA = "rsc_data"  # Next.js RSC payload (Naver)


@dataclass(frozen=True, slots=True)
class HtmlChunk:
    """An atomic HTML chunk extracted from the DOM tree."""

    xpath: str
    html: str
    text: str
    tag: str
    chunk_type: ChunkType
    attrs: dict = field(default_factory=dict, hash=False)
    parent_xpath: str = ""
    depth: int = 0
    in_main: bool = False


class PruningError(Exception):
    """Raised when HTML parsing or pruning fails fatally."""
