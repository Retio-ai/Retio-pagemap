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

import logging
import time
from dataclasses import dataclass, field

from pagemap.preprocessing.preprocess import count_tokens
from pagemap.pruning import HtmlChunk, PruningError
from pagemap.pruning.aom_filter import AomFilterStats, aom_filter
from pagemap.pruning.compressor import compress_html, remerge_chunks
from pagemap.pruning.preprocessor import _decompose_element, preprocess_and_chunk
from pagemap.pruning.pruner import prune_chunks

logger = logging.getLogger(__name__)


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


def prune_page(
    raw_html: str,
    site_id: str,
    page_id: str,
    schema_name: str,
) -> PruningResult:
    """Run the full pruning pipeline on a single page.

    Args:
        raw_html: Original HTML content
        site_id: Site identifier
        page_id: Page identifier
        schema_name: Schema name for heuristic matching (e.g. "Product")

    Returns:
        PruningResult with pruned HTML and metrics
    """
    result = PruningResult(site_id=site_id, page_id=page_id)
    start = time.monotonic()

    try:
        # Measure raw tokens
        result.raw_token_count = count_tokens(raw_html)

        # Step 1-3: Preprocess + chunk
        chunks, doc = preprocess_and_chunk(raw_html)

        # Step 4: AOM filter (in-place on DOM)
        result.aom_filter_stats = aom_filter(doc, schema_name=schema_name)

        # Re-chunk after AOM filtering (DOM was modified in-place)
        body = doc.body if doc.body is not None else doc
        tree = doc.getroottree()
        dom_chunks = _decompose_element(body, tree)

        # Keep meta chunks from original extraction (they're not in DOM)
        from pagemap.pruning import ChunkType

        meta_chunks = [c for c in chunks if c.chunk_type in (ChunkType.META, ChunkType.RSC_DATA)]
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

        # Step 5: Rule-based pruning
        decisions = prune_chunks(all_chunks, schema_name, has_main=has_main)
        selected = [chunk for chunk, decision in decisions if decision.keep]
        result.chunk_count_selected = len(selected)
        result.selected_chunks = selected

        if not selected:
            result.errors.append("0 chunks selected — returning original HTML")
            result.pruned_html = raw_html
            result.pruned_token_count = result.raw_token_count
            result.elapsed_ms = (time.monotonic() - start) * 1000
            return result

        # Step 6: Re-merge
        merged = remerge_chunks(selected)

        # Step 7: HTMLRAG Pass 2 compression
        compressed = compress_html(merged)
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

    from pagemap.preprocessing.normalize import normalize_date, normalize_numeric, normalize_str
    from pagemap.preprocessing.schemas import FIELD_TYPES

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
