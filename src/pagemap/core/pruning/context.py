# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A2: Context-aware pruning intensity.

PruningContext captures page characteristics (complexity, density, budget pressure).
StageAlphas encodes per-stage pruning aggressiveness derived from context.
"""

from __future__ import annotations

import logging
import math
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import lxml.html

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PruningContext:
    """Page-level context signals for adaptive pruning."""

    page_complexity: float = 0.0  # DOM nodes x avg_depth (normalised, 0-1)
    content_density: float = 0.0  # text_bytes / total_bytes (0-1)
    budget_pressure: float = 1.0  # token_budget / raw_tokens (0-1, 1.0 = no pressure)

    def __post_init__(self) -> None:
        for name in ("page_complexity", "content_density", "budget_pressure"):
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                default = 1.0 if name == "budget_pressure" else 0.0
                object.__setattr__(self, name, default)


@dataclass(frozen=True, slots=True)
class StageAlphas:
    """Per-stage pruning intensity multipliers (1.0 = unchanged)."""

    aom: float = 1.0
    grouping: float = 1.0
    rule: float = 1.0
    budget: float = 1.0
    compress: float = 1.0

    def __post_init__(self) -> None:
        for name in ("aom", "grouping", "rule", "budget", "compress"):
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0.0:
                object.__setattr__(self, name, 1.0)


@dataclass(frozen=True, slots=True)
class PruningCorrections:
    """Direction vector corrections injected by CQP via ContextVar.

    task_vector_offset: A1 additive (text_density, link_density, interactive_ratio, semantic_weight).
    alpha_scaling: A2 multiplicative (aom, grouping, rule, budget, compress).
    """

    task_vector_offset: tuple[float, float, float, float] | None = None
    alpha_scaling: tuple[float, float, float, float, float] | None = None


_pruning_corrections: ContextVar[PruningCorrections | None] = ContextVar("pagemap_pruning_corrections", default=None)


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def build_pruning_context(
    doc: lxml.html.HtmlElement,
    raw_html: str,
    raw_token_count: int,
    max_tokens: int | None,
) -> PruningContext:
    """Build PruningContext from DOM doc and raw metrics.

    Uses lxml C-level ``doc.iter()`` — no recursive Python walk.
    """
    depth_cache: dict[int, int] = {}
    total_nodes = 0
    total_depth = 0
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        total_nodes += 1
        el_id = id(el)
        parent = el.getparent()
        if parent is None:
            d = 0
        else:
            pid = id(parent)
            d = depth_cache.get(pid, 0) + 1
        depth_cache[el_id] = d
        total_depth += d

    avg_depth = total_depth / max(total_nodes, 1)
    page_complexity = min(total_nodes * (avg_depth / 20.0) / 5000.0, 1.0)

    text_len = len((doc.text_content() or "").strip())
    content_density = min(text_len / max(len(raw_html), 1), 1.0)

    if max_tokens is not None and max_tokens > 0:
        budget_pressure = min(max_tokens / max(raw_token_count, 1), 1.0)
    else:
        budget_pressure = 1.0

    return PruningContext(page_complexity, content_density, budget_pressure)


def compute_stage_alphas(ctx: PruningContext) -> StageAlphas:
    """Derive per-stage alphas from PruningContext. Pure function."""
    if ctx.budget_pressure >= 1.0:
        return StageAlphas()  # all 1.0 — backward compat

    pressure_factor = 1.0 + (1.0 - ctx.budget_pressure) * 2.0  # [1.0, 3.0]
    density_factor = 1.0 - ctx.content_density * 0.5  # [0.5, 1.0]
    raw_alpha = pressure_factor * density_factor  # [0.5, 3.0]

    return StageAlphas(
        aom=_clamp(raw_alpha**0.3, 0.8, 1.15),
        grouping=_clamp(1.0 / raw_alpha, 0.4, 1.0),
        rule=_clamp(raw_alpha**0.3, 0.8, 1.5),
        budget=_clamp(raw_alpha, 1.0, 3.0),
        compress=_clamp(raw_alpha, 1.0, 2.0),
    )
