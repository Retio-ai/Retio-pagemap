# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A1: Task preference vector for task-aware chunk scoring.

Computes a 4-dimensional feature vector for each chunk and measures
cosine similarity against a task-specific preference vector.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from . import ChunkType, HtmlChunk, PruneReason
from .pruner import PruneDecision


class TaskHint(StrEnum):
    """Task type hint for MCP tool parameter."""

    SEARCH = "search"
    DETAIL = "detail"
    CART = "cart"
    FORM = "form"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class ChunkVector:
    """4-dimensional chunk feature vector."""

    text_density: float  # len(text) / max(len(html), 1), [0,1]
    link_density: float  # <a> tag count / max(word_count, 1), [0,1]
    interactive_ratio: float  # interactive tag count normalized, [0,1]
    semantic_weight: float  # structural semantic weight, [0,1]

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                object.__setattr__(self, name, 0.0)

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.text_density, self.link_density, self.interactive_ratio, self.semantic_weight)


# ── Task preference vectors ──────────────────────────────────────────

TASK_VECTORS: Final[Mapping[str, ChunkVector]] = MappingProxyType(
    {
        "search": ChunkVector(0.3, 0.8, 0.6, 0.7),
        "detail": ChunkVector(0.9, 0.2, 0.3, 0.8),
        "cart": ChunkVector(0.2, 0.3, 0.9, 0.5),
        "form": ChunkVector(0.4, 0.1, 1.0, 0.6),
        "general": ChunkVector(0.5, 0.5, 0.5, 0.5),
    }
)


def _normalize(v: tuple[float, ...]) -> tuple[float, ...]:
    mag = math.sqrt(sum(x * x for x in v))
    return tuple(x / mag for x in v) if mag > 0 else v


_TASK_VECTORS_NORMALIZED: Final[Mapping[str, tuple[float, ...]]] = MappingProxyType(
    {k: _normalize(v.to_tuple()) for k, v in TASK_VECTORS.items()}
)

# ── Regex (module-level pre-compiled) ─────────────────────────────────

_LINK_RE = re.compile(r"<a\b", re.IGNORECASE)
_INTERACTIVE_RE = re.compile(r"<(?:input|button|select|textarea)\b", re.IGNORECASE)

_ZERO_VECTOR = ChunkVector(0.0, 0.0, 0.0, 0.0)

_VALID_HINTS: frozenset[str] = frozenset(h.value for h in TaskHint)


# ── Core functions ────────────────────────────────────────────────────


def compute_chunk_vector(chunk: HtmlChunk) -> ChunkVector:
    """Derive 4D feature vector from HtmlChunk fields only. No extra DOM traversal."""
    if not chunk.html and not chunk.text:
        return _ZERO_VECTOR

    html = chunk.html
    text = chunk.text

    # text_density
    text_density = min(len(text) / max(len(html), 1), 1.0)

    # link_density
    link_count = len(_LINK_RE.findall(html))
    word_count = len(text.split())
    link_density = min(link_count / max(word_count, 1), 1.0)

    # interactive_ratio
    interactive_count = len(_INTERACTIVE_RE.findall(html))
    base = 0.5 if chunk.chunk_type == ChunkType.FORM else 0.0
    interactive_ratio = min(base + interactive_count * 0.2, 1.0)

    # semantic_weight
    sw = 0.0
    if chunk.attrs.get("itemprop"):
        sw += 0.4
    if chunk.attrs.get("role"):
        sw += 0.2
    if chunk.in_main:
        sw += 0.2
    if chunk.chunk_type == ChunkType.HEADING:
        sw += 0.1
    if chunk.attrs.get("aria-label"):
        sw += 0.1
    semantic_weight = min(sw, 1.0)

    return ChunkVector(text_density, link_density, interactive_ratio, semantic_weight)


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity. Zero vector -> 0.0."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def compute_fitness_scores(
    decisions: list[tuple[HtmlChunk, PruneDecision]],
    task_hint: str,
    *,
    task_vector_offset: tuple[float, float, float, float] | None = None,
) -> None:
    """Set decision.fitness in-place using pre-normalized task vectors."""
    task_vec = _TASK_VECTORS_NORMALIZED.get(task_hint)
    if task_vec is None:
        task_vec = _TASK_VECTORS_NORMALIZED["general"]

    if task_vector_offset is not None:
        base = TASK_VECTORS.get(task_hint) or TASK_VECTORS["general"]
        adjusted = tuple(max(0.0, min(1.0, b + o)) for b, o in zip(base.to_tuple(), task_vector_offset, strict=True))
        task_vec = _normalize(adjusted)

    for chunk, decision in decisions:
        if decision.reason == PruneReason.META_ALWAYS:
            decision.fitness = 1.0
            continue

        chunk_vec = compute_chunk_vector(chunk)
        normalized_chunk = _normalize(chunk_vec.to_tuple())
        sim = cosine_similarity(task_vec, normalized_chunk)

        if decision.reason == PruneReason.SCHEMA_MATCH:
            decision.fitness = max(sim, 0.5)
        else:
            decision.fitness = sim


def validate_task_hint(hint: str | None) -> str | None:
    """Validate and normalize task hint. None -> None, unknown -> 'general'."""
    if hint is None:
        return None
    hint_lower = hint.lower().strip()
    if hint_lower in _VALID_HINTS:
        return hint_lower
    return "general"
