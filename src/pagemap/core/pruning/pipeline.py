# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Pruning pipeline orchestration.

Flow:
  raw.html
    → HTMLRAG Pass 1 (preprocessor)
    → Special script extraction (JSON-LD, RSC)
    → lxml DOM parsing
    → AOM filter (semantic + role/aria node removal)
    → Atomic chunk decomposition
    → Rule-based pruning (schema heuristics)
    → Re-merge (selected chunks → single HTML)
    → HTMLRAG Pass 2 (lossless compression)
    → Token measurement
    → PruningResult
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config_registry import PruningConfig

from ..preprocessing.preprocess import count_tokens, count_tokens_approx
from . import HtmlChunk, PruningError
from .aom_filter import AomFilterStats, _detect_repeating_grids, aom_filter
from .compressor import compress_html, remerge_chunks
from .context import StageAlphas, _clamp, build_pruning_context, compute_stage_alphas
from .preprocessor import _decompose_element, preprocess
from .pruner import PruneDecision, apply_budget_selection, boost_adjacent_chunks, prune_chunks

logger = logging.getLogger(__name__)


def _default_cfg() -> PruningConfig:
    from ..config_registry import DEFAULT_PRUNING_CONFIG

    return DEFAULT_PRUNING_CONFIG


@dataclass
class PruningResult:
    """Result of pruning a single page."""

    site_id: str
    page_id: str
    raw_token_count: int = 0
    pruned_token_count: int = 0
    token_reduction_pct: float = 0.0
    chunk_count_total: int = 0
    chunk_count_selected: int = 0
    pruned_html: str = ""
    pruner_recall: float | None = None
    per_field_recall: dict[str, bool] | None = None
    elapsed_ms: float = 0.0
    aom_filter_stats: AomFilterStats = field(default_factory=AomFilterStats)
    errors: list[str] = field(default_factory=list)
    meta_chunks: list[HtmlChunk] = field(default_factory=list)
    heading_chunks: list[HtmlChunk] = field(default_factory=list)
    selected_chunks: list[HtmlChunk] = field(default_factory=list)
    doc: Any = None  # lxml.html.HtmlElement — transient, not serialized
    selected_decisions: dict[str, PruneDecision] | None = None
    boost_flip_count: int = 0
    stage_alphas: StageAlphas | None = None  # A2: per-page alpha diagnostic
    tier_counts: dict[str, int] | None = None  # A5: {"A": n, "B": n, "C": n}
    task_hint: str | None = None  # A1: task hint used
    interactive_chunk_total: int = 0  # A4: chunks with >=1 interactive element
    interactive_chunk_selected: int = 0  # A4: kept chunks with >=1 interactive element


def prune_page(
    raw_html: str,
    site_id: str,
    page_id: str,
    schema_name: str,
    *,
    config: PruningConfig | None = None,
    max_tokens: int | None = None,
    task_hint: str | None = None,
) -> PruningResult:
    """Run the full pruning pipeline on a single page.

    Args:
        raw_html: Original HTML content
        site_id: Site identifier
        page_id: Page identifier
        schema_name: Schema name for heuristic matching (e.g. "Product")
        config: Optional PruningConfig for CQP-driven threshold overrides.
        max_tokens: Optional token budget. When set, activates A2
            context-aware pruning (budget_pressure < 1.0 → elevated
            alphas) and budget_selection.

    Returns:
        PruningResult with pruned HTML and metrics
    """
    result = PruningResult(site_id=site_id, page_id=page_id)
    start = time.monotonic()

    try:
        # Measure raw tokens (approx for large HTML — metrics only, not budget-critical)
        if len(raw_html) > 50_000:
            result.raw_token_count = count_tokens_approx(raw_html)
        else:
            result.raw_token_count = count_tokens(raw_html)

        # Step 1-3: Preprocess (no chunk decomposition yet)
        meta_chunks, doc = preprocess(raw_html)

        cfg = config or _default_cfg()

        # A2: Build pruning context and compute per-stage alphas
        pruning_ctx = build_pruning_context(doc, raw_html, result.raw_token_count, max_tokens)
        alphas = compute_stage_alphas(pruning_ctx)

        # A4: apply direction vector alpha scaling from ContextVar
        try:
            from .context import _pruning_corrections

            _corr = _pruning_corrections.get()
            if _corr is not None and _corr.alpha_scaling is not None:
                alphas = StageAlphas(
                    aom=_clamp(alphas.aom * _corr.alpha_scaling[0], 0.8, 1.15),
                    grouping=_clamp(alphas.grouping * _corr.alpha_scaling[1], 0.4, 1.0),
                    rule=_clamp(alphas.rule * _corr.alpha_scaling[2], 0.8, 1.5),
                    budget=_clamp(alphas.budget * _corr.alpha_scaling[3], 1.0, 3.0),
                    compress=_clamp(alphas.compress * _corr.alpha_scaling[4], 1.0, 2.0),
                )
        except Exception:  # nosec B110
            pass

        result.stage_alphas = alphas

        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import STAGE_ALPHAS_COMPUTED

            emit(
                STAGE_ALPHAS_COMPUTED,
                {
                    "bp": pruning_ctx.budget_pressure,
                    "cd": pruning_ctx.content_density,
                    "alpha_aom": alphas.aom,
                    "alpha_rule": alphas.rule,
                    "alpha_budget": alphas.budget,
                },
            )
        except Exception:  # nosec B110
            pass

        # A2: Context built on pre-AOM DOM (intentional — AOM threshold depends on alphas,
        # so context must be computed first. Density/complexity are slightly overestimated
        # but budget_pressure — the dominant signal — is unaffected.)

        # Step 3.5: Detect repeating grids for AOM whitelist
        grid_whitelist = _detect_repeating_grids(doc)
        if grid_whitelist:
            logger.info("Grid whitelist: %d containers", len(grid_whitelist))

        # Step 4: AOM filter (in-place on DOM, with grid whitelist)
        result.aom_filter_stats = aom_filter(
            doc,
            schema_name=schema_name,
            threshold=0.5 * alphas.aom,
            grid_whitelist=grid_whitelist,
            enable_text_density=cfg.enable_text_density_signal,
        )

        # Preserve post-AOM doc for downstream DOM card detection
        result.doc = doc

        # Step 5: Chunk decomposition — single pass after AOM filter
        body = doc.body if doc.body is not None else doc
        tree = doc.getroottree()
        dom_chunks = _decompose_element(
            body,
            tree,
            enable_sibling_grouping=cfg.enable_sibling_grouping,
            grouping_alpha=alphas.grouping,
        )
        all_chunks = meta_chunks + dom_chunks

        result.chunk_count_total = len(all_chunks)
        result.meta_chunks = meta_chunks
        result.heading_chunks = [c for c in all_chunks if c.tag == "h1" or c.attrs.get("itemprop")]

        if not all_chunks:
            result.errors.append("No chunks after preprocessing + AOM filter")
            result.pruned_html = raw_html
            result.pruned_token_count = result.raw_token_count
            result.elapsed_ms = (time.monotonic() - start) * 1000
            return result

        # Detect if page has <main>
        has_main = any(c.in_main for c in all_chunks)

        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import CHUNK_DECOMPOSE

            emit(CHUNK_DECOMPOSE, {"chunk_count": len(all_chunks), "has_main": has_main})
        except Exception:  # nosec B110
            pass

        # Step 5: Rule-based pruning
        decisions = prune_chunks(all_chunks, schema_name, has_main=has_main, config=config, stage_alpha=alphas.rule)

        # Step 5b: Adjacent chunk boosting
        if cfg.enable_adjacent_boost:
            flip_count = boost_adjacent_chunks(decisions)
            result.boost_flip_count = flip_count

        # Step 5c: A1 fitness scoring (task_hint present only)
        validated_hint = None
        if task_hint is not None:
            from .task_vector import compute_fitness_scores, validate_task_hint

            validated_hint = validate_task_hint(task_hint)
            if validated_hint is not None:
                # A4: read direction vector offset from ContextVar
                _a4_offset = None
                try:
                    from .context import _pruning_corrections

                    _corr = _pruning_corrections.get()
                    if _corr is not None:
                        _a4_offset = _corr.task_vector_offset
                except Exception:  # nosec B110
                    pass
                compute_fitness_scores(decisions, validated_hint, task_vector_offset=_a4_offset)
                result.task_hint = validated_hint

        # Step 5d: Budget-aware selection (greedy deletion of lowest-score chunks)
        if max_tokens is not None and cfg.enable_scoring:
            apply_budget_selection(decisions, max_tokens, score_bias=alphas.budget)

        # Step 5e: A5 tier processing (task_hint present only)
        if validated_hint is not None:
            from .tier_processor import apply_tier_processing, generate_dropped_references

            tier_processed = apply_tier_processing(decisions, pruning_ctx.budget_pressure)
            dropped_refs = generate_dropped_references(tier_processed)
            selected = [c for c, d in tier_processed if d.keep] + dropped_refs

            # Compute tier counts
            tier_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0}
            for _c, _d in tier_processed:
                if _d.keep and _d.tier:
                    tier_counts[_d.tier] = tier_counts.get(_d.tier, 0) + 1
            result.tier_counts = tier_counts

            with contextlib.suppress(Exception):
                from pagemap.telemetry import emit

                emit(
                    "task_tier_processed",
                    {
                        "task_hint": validated_hint,
                        "tier_a": tier_counts.get("A", 0),
                        "tier_b": tier_counts.get("B", 0),
                        "tier_c": tier_counts.get("C", 0),
                    },
                )

            # Update decisions reference for downstream
            decisions = tier_processed
        else:
            selected = [chunk for chunk, decision in decisions if decision.keep]

        result.chunk_count_selected = len(selected)
        result.selected_chunks = selected
        result.selected_decisions = {c.xpath: d for c, d in decisions if d.keep}

        # A4: compute interactive chunk counts for PruningOutputProfile
        try:
            from .task_vector import _INTERACTIVE_RE

            _int_total = 0
            _int_selected = 0
            _kept_xpaths = result.selected_decisions or {}
            for chunk, _dec in decisions:
                if _INTERACTIVE_RE.search(chunk.html):
                    _int_total += 1
                    if chunk.xpath in _kept_xpaths:
                        _int_selected += 1
            result.interactive_chunk_total = _int_total
            result.interactive_chunk_selected = _int_selected
        except Exception:  # nosec B110
            pass

        if not selected:
            result.errors.append("0 chunks selected — returning original HTML")
            result.pruned_html = raw_html
            result.pruned_token_count = result.raw_token_count
            result.elapsed_ms = (time.monotonic() - start) * 1000
            return result

        # Step 6: Re-merge (with block-tree parent preservation)
        merged = remerge_chunks(selected, enable_block_tree=cfg.enable_block_tree_remerge)

        # Step 7: HTMLRAG Pass 2 compression
        compressed = compress_html(merged, extra_passes=alphas.compress)
        result.pruned_html = compressed

        # Token measurement
        result.pruned_token_count = count_tokens(compressed)
        if result.raw_token_count > 0:
            result.token_reduction_pct = (1.0 - result.pruned_token_count / result.raw_token_count) * 100

    except PruningError as e:
        result.errors.append(str(e))
        result.pruned_html = raw_html
        result.pruned_token_count = count_tokens(raw_html) if raw_html else 0
        logger.error("PruningError for %s/%s: %s", site_id, page_id, e)
    except Exception as e:
        result.errors.append(f"Unexpected error: {e}")
        result.pruned_html = raw_html
        result.pruned_token_count = count_tokens(raw_html) if raw_html else 0
        logger.error("Unexpected error for %s/%s: %s", site_id, page_id, e, exc_info=True)

    result.elapsed_ms = (time.monotonic() - start) * 1000
    return result


def measure_pruner_recall(
    pruned_html: str,
    ground_truth: dict,
    schema_name: str,
) -> tuple[float, dict[str, bool]]:
    """Measure whether GT field values survive pruning.

    For each field in ground truth, check if the value is present in pruned_html.

    Returns:
        (overall_recall, per_field_dict) where per_field_dict maps field_name → found
    """
    from rapidfuzz.fuzz import partial_ratio

    from ..preprocessing.normalize import normalize_date, normalize_numeric, normalize_str
    from ..preprocessing.schemas import FIELD_TYPES

    field_types = FIELD_TYPES.get(schema_name, {})
    gt_data = ground_truth.get("data", {})
    pruned_lower = pruned_html.lower()
    pruned_normalized = normalize_str(pruned_html)

    per_field: dict[str, bool] = {}
    total = 0
    found = 0

    for field_name, field_type in field_types.items():
        gt_val = gt_data.get(field_name)
        if gt_val is None:
            continue

        total += 1
        field_found = False

        if field_type == "numeric":
            num = normalize_numeric(gt_val)
            if num is not None:
                # Check int, float, and comma-separated formats
                num_str = str(int(num)) if num == int(num) else str(num)
                num_with_commas = f"{int(num):,}" if num == int(num) else num_str
                raw_str = str(gt_val).strip()
                field_found = num_str in pruned_html or num_with_commas in pruned_html or raw_str in pruned_html
                # Check abbreviated formats: 66000 → "66k", 1200000 → "1.2m"
                if not field_found and num >= 1000:
                    int_num = int(num)
                    if int_num >= 1_000_000:
                        m_val = int_num / 1_000_000
                        abbrevs = [f"{m_val:.1f}m", f"{int(m_val)}m"] if m_val == int(m_val) else [f"{m_val:.1f}m"]
                        field_found = any(a in pruned_lower for a in abbrevs)
                    elif int_num >= 1000:
                        k_val = int_num / 1000
                        abbrevs = [f"{k_val:.1f}k", f"{int(k_val)}k"] if k_val == int(k_val) else [f"{k_val:.1f}k"]
                        field_found = any(a in pruned_lower for a in abbrevs)

        elif field_type in ("text", "long_text"):
            gt_str = normalize_str(str(gt_val))
            if gt_str:
                # Use partial_ratio for fuzzy containment
                score = partial_ratio(gt_str[:200], pruned_normalized)
                field_found = score >= 80
                # Fallback: check if key words are present (for fragmented text)
                if not field_found:
                    words = [w for w in gt_str.split() if len(w) > 2]
                    if words:
                        words_found = sum(1 for w in words if w in pruned_normalized)
                        field_found = words_found >= len(words) * 0.4

        elif field_type == "url":
            url = str(gt_val).strip()
            # Check exact URL or URL path component
            field_found = url in pruned_html
            if not field_found:
                # Try just the path portion (URL may be truncated/rewritten)
                from urllib.parse import urlparse

                path = urlparse(url).path
                if path and len(path) > 10:
                    field_found = path in pruned_html

        elif field_type == "date":
            normalized_date = normalize_date(str(gt_val))
            if normalized_date:
                # Check all common date format variants
                # normalized is YYYY-MM-DD
                parts = normalized_date.split("-")
                if len(parts) == 3:
                    y, m, d = parts
                    variants = [
                        normalized_date,  # 2024-10-22
                        f"{y}.{m}.{d}",  # 2024.10.22
                        f"{y}.{int(m)}.{int(d)}",  # 2024.10.22 (no leading zero)
                        f"{y}/{m}/{d}",  # 2024/10/22
                        f"{y}/{int(m)}/{int(d)}",  # 2024/10/22
                        f"{y}년 {int(m)}월 {int(d)}일",  # 2024년 10월 22일
                        f"{y}년{int(m)}월{int(d)}일",  # 2024년10월22일
                        str(gt_val).strip(),  # Original GT value
                    ]
                    field_found = any(v in pruned_html for v in variants)
                else:
                    field_found = normalized_date in pruned_html

        elif field_type == "list":
            # Check if at least some items are present
            if isinstance(gt_val, list):
                items_found = sum(1 for item in gt_val if normalize_str(str(item)) in pruned_normalized)
                field_found = items_found >= len(gt_val) * 0.5 if gt_val else True

        elif field_type == "exact":
            gt_exact = normalize_str(str(gt_val))
            field_found = gt_exact in pruned_normalized
            # Fallback: check if the core part exists (e.g., "MIT license" → "mit")
            if not field_found and gt_exact:
                words = gt_exact.split()
                field_found = any(w in pruned_normalized for w in words if len(w) > 2)

        if field_found:
            found += 1
        per_field[field_name] = field_found

    recall = found / total if total > 0 else 1.0
    return recall, per_field


def print_summary(
    results: list[PruningResult],
    ground_truths: dict[str, dict] | None = None,
    site_schema_map: dict[str, tuple[str, str]] | None = None,
    verbose: bool = False,
) -> None:
    """Print a summary table of pruning results."""
    from tabulate import tabulate

    rows = []
    total_raw = 0
    total_pruned = 0
    recalls = []

    for r in results:
        total_raw += r.raw_token_count
        total_pruned += r.pruned_token_count

        recall_str = "-"
        if r.pruner_recall is not None:
            recall_str = f"{r.pruner_recall:.2f}"
            recalls.append(r.pruner_recall)

        error_str = r.errors[0][:30] if r.errors else ""

        rows.append(
            [
                r.site_id,
                r.page_id,
                f"{r.raw_token_count:,}",
                f"{r.pruned_token_count:,}",
                f"{r.token_reduction_pct:.1f}%",
                r.chunk_count_total,
                r.chunk_count_selected,
                recall_str,
                f"{r.elapsed_ms:.0f}ms",
                error_str,
            ]
        )

        if verbose and r.per_field_recall:
            for field_name, found in r.per_field_recall.items():
                status = "OK" if found else "MISS"
                rows.append(["", "", "", "", "", "", "", f"  {field_name}: {status}", "", ""])

    # Average row
    avg_reduction = (1.0 - total_pruned / total_raw) * 100 if total_raw > 0 else 0
    avg_recall = sum(recalls) / len(recalls) if recalls else 0
    rows.append(
        [
            "AVERAGE",
            "",
            f"{total_raw:,}",
            f"{total_pruned:,}",
            f"{avg_reduction:.1f}%",
            "",
            "",
            f"{avg_recall:.2f}" if recalls else "-",
            "",
            "",
        ]
    )

    headers = ["Site", "Page", "Raw Tok", "Pruned", "Reduction", "Chunks", "Selected", "Recall", "Time", "Errors"]
    print(tabulate(rows, headers=headers, tablefmt="simple"))

    # Pass criteria check
    print("\n--- Pass Criteria ---")
    print(f"Token reduction (avg): {avg_reduction:.1f}% (target: >= 80%)", "PASS" if avg_reduction >= 80 else "FAIL")
    print(f"Pruner recall (avg):   {avg_recall:.2f} (target: >= 0.95)", "PASS" if avg_recall >= 0.95 else "FAIL")
    crashes = sum(1 for r in results if r.errors)
    print(
        f"No crashes:            {len(results) - crashes}/{len(results)}",
        "PASS" if crashes == 0 else f"FAIL ({crashes} errors)",
    )
