# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Pruning quality confidence assessment.

Evaluates how well the pruning pipeline captured page content.
Score: base 0.5, adjusted by removal_rate, chunk_ratio, has_main, missed_regions.
"""

from __future__ import annotations

from typing import Any

from . import PruningConfidence


def assess_pruning_confidence(
    *,
    pruning_result: Any | None,
    page_type: str,
    pruned_regions: set[str],
    interactable_count: int,
) -> PruningConfidence | None:
    """Assess pruning quality. Returns None if no pruning_result available. Never raises."""
    try:
        return _assess_impl(
            pruning_result=pruning_result,
            page_type=page_type,
            pruned_regions=pruned_regions,
            interactable_count=interactable_count,
        )
    except Exception:
        return None


def _assess_impl(
    *,
    pruning_result: Any | None,
    page_type: str,
    pruned_regions: set[str],
    interactable_count: int,
) -> PruningConfidence | None:
    if pruning_result is None:
        return None

    signals: list[str] = []
    score = 0.5  # base

    # Extract metrics from pruning_result
    chunk_count_total = getattr(pruning_result, "chunk_count_total", 0)
    chunk_count_selected = getattr(pruning_result, "chunk_count_selected", 0)
    selected_chunks = getattr(pruning_result, "selected_chunks", [])
    aom_stats = getattr(pruning_result, "aom_filter_stats", None)

    # Removal rate
    removal_rate = 0.0
    if aom_stats is not None:
        total_nodes = getattr(aom_stats, "total_nodes", 0)
        removed_nodes = getattr(aom_stats, "removed_nodes", 0)
        if total_nodes > 0:
            removal_rate = removed_nodes / total_nodes

    # Chunk selection ratio
    chunk_ratio = 0.0
    if chunk_count_total > 0:
        chunk_ratio = chunk_count_selected / chunk_count_total

    # Has main content
    has_main = any(getattr(c, "in_main", False) for c in selected_chunks)

    # Token reduction
    token_reduction_pct = getattr(pruning_result, "token_reduction_pct", 0.0)
    if token_reduction_pct == 0.0:
        raw_tokens = getattr(pruning_result, "raw_token_count", 0)
        pruned_tokens = getattr(pruning_result, "pruned_token_count", 0)
        if raw_tokens > 0:
            token_reduction_pct = (1.0 - pruned_tokens / raw_tokens) * 100.0

    # ── Scoring adjustments (page-type-aware) ──────────────────

    # Page-type-specific removal rate sweet spots
    _removal_optimal: tuple[float, float]
    if page_type == "landing":
        _removal_optimal = (0.1, 0.5)
    else:
        _removal_optimal = (0.3, 0.7)

    if _removal_optimal[0] <= removal_rate <= _removal_optimal[1]:
        score += 0.2
        signals.append(f"removal_rate={removal_rate:.2f} (optimal for {page_type})")
    elif removal_rate > 0.9:
        score -= 0.1
        signals.append(f"removal_rate={removal_rate:.2f} (aggressive)")
    elif removal_rate < 0.1 and chunk_count_total > 3:
        score -= 0.05
        signals.append(f"removal_rate={removal_rate:.2f} (minimal)")

    # Chunk selection ratio contribution
    score += chunk_ratio * 0.2
    signals.append(f"chunk_ratio={chunk_ratio:.2f}")

    # Page-type-specific chunk_ratio bonus
    if page_type in ("article", "product_detail") and chunk_ratio > 0.7:
        score += 0.05
        signals.append(f"chunk_ratio_bonus (high ratio for {page_type})")

    # Has main content boost
    if has_main:
        score += 0.1
        signals.append("has_main_content")
    else:
        signals.append("no_main_content")

    # Missed regions penalty
    missed = tuple(sorted(pruned_regions))
    for _region in missed:
        score -= 0.05
    if missed:
        signals.append(f"missed_regions={len(missed)}")

    # Clamp score
    score = max(0.0, min(1.0, score))

    return PruningConfidence(
        overall_confidence=round(score, 2),
        removal_rate=round(removal_rate, 2),
        chunk_selection_ratio=round(chunk_ratio, 2),
        has_main_content=has_main,
        potentially_missed_regions=missed,
        token_reduction_pct=round(token_reduction_pct, 2),
        signals=tuple(signals),
    )
