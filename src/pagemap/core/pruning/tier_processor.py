# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""A5: 3-tier chunk processing based on task fitness scores.

Classifies kept chunks into Tier A (preserve), Tier B (compress),
Tier C (metadata reference) to resolve information black holes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from enum import StrEnum

from . import ChunkType, HtmlChunk, PruneReason
from .pruner import PruneDecision


class ChunkTier(StrEnum):
    A = "A"  # Original preserved
    B = "B"  # Compressed
    C = "C"  # Metadata reference


# ── Sentence boundary regex (CJK aware) ──────────────────────────────

_SENTENCE_END_RE = re.compile(
    r"(?<=[.!?])\s+"
    r"|(?<=\u3002)\s*"  # 。
    r"|(?<=\uff01)\s*"  # ！
    r"|(?<=\uff1f)\s*"  # ？
    r"|(?<=\ub2e4\.)\s+"  # 다.
    r"|(?<=\uc694\.)\s+"  # 요.
)

_TABLE_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_HEADER_RE = re.compile(r"<thead\b[^>]*>.*?</thead>", re.IGNORECASE | re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<li\b[^>]*>.*?</li>", re.IGNORECASE | re.DOTALL)

# ── Threshold computation ────────────────────────────────────────────


def compute_tier_thresholds(budget_pressure: float) -> tuple[float, float]:
    """Return (tier_a_min, tier_b_min). Lower pressure -> lower thresholds."""
    bp = max(0.0, min(1.0, budget_pressure))
    tier_a = 0.8 - 0.15 * bp  # 0.80 -> 0.65
    tier_b = 0.4 - 0.15 * bp  # 0.40 -> 0.25
    return tier_a, tier_b


# ── Tier classification ──────────────────────────────────────────────


def classify_tier(decision: PruneDecision, tier_a_min: float, tier_b_min: float) -> ChunkTier:
    """Classify a kept decision into a tier based on fitness."""
    match decision.reason:
        case PruneReason.META_ALWAYS:
            return ChunkTier.A
        case _:
            fitness = decision.fitness if decision.fitness is not None else 0.0

            # SCHEMA_MATCH gets lowered threshold (already validated)
            if decision.reason == PruneReason.SCHEMA_MATCH:
                if fitness >= tier_a_min * 0.75:
                    return ChunkTier.A

            if fitness >= tier_a_min:
                return ChunkTier.A

            # HEADING: minimum Tier B (never demoted to C)
            if decision.reason in (PruneReason.IN_MAIN_HEADING, PruneReason.KEEP_HEADING_NO_MAIN):
                return ChunkTier.B

            if fitness >= tier_b_min:
                return ChunkTier.B

            return ChunkTier.C


# ── Tier B: compression ──────────────────────────────────────────────

_MAX_SENTENCES = 2
_MAX_TABLE_ROWS = 3
_MAX_LIST_ITEMS = 3
_FALLBACK_CHAR_LIMIT = 200


def compress_chunk_tier_b(chunk: HtmlChunk) -> HtmlChunk:
    """Return a compressed copy of the chunk via dataclasses.replace()."""
    match chunk.chunk_type:
        case ChunkType.TEXT_BLOCK:
            sentences = _SENTENCE_END_RE.split(chunk.text)
            if len(sentences) <= _MAX_SENTENCES:
                return chunk
            truncated = " ".join(sentences[:_MAX_SENTENCES]).strip()
            if not truncated:
                truncated = chunk.text[:_FALLBACK_CHAR_LIMIT]
            return replace(chunk, text=truncated + "...", html=truncated + "...")

        case ChunkType.TABLE:
            rows = _TABLE_ROW_RE.findall(chunk.html)
            if len(rows) <= _MAX_TABLE_ROWS + 1:  # header + rows
                return chunk
            header_match = _TABLE_HEADER_RE.search(chunk.html)
            header = header_match.group(0) if header_match else ""
            kept_rows = rows[:_MAX_TABLE_ROWS]
            extra = len(rows) - _MAX_TABLE_ROWS
            new_html = f"<table>{header}{''.join(kept_rows)}<tr><td>[+{extra} rows]</td></tr></table>"
            new_text = re.sub(r"<[^>]+>", " ", new_html).strip()
            return replace(chunk, html=new_html, text=new_text)

        case ChunkType.LIST:
            items = _LIST_ITEM_RE.findall(chunk.html)
            if len(items) <= _MAX_LIST_ITEMS:
                return chunk
            extra = len(items) - _MAX_LIST_ITEMS
            new_html = "<ul>" + "".join(items[:_MAX_LIST_ITEMS]) + f"<li>[+{extra} items]</li></ul>"
            new_text = re.sub(r"<[^>]+>", " ", new_html).strip()
            return replace(chunk, html=new_html, text=new_text)

        case ChunkType.HEADING | ChunkType.MEDIA:
            return chunk

        case _:
            # FORM and others: truncate text if long
            if len(chunk.text) > _FALLBACK_CHAR_LIMIT:
                return replace(
                    chunk,
                    text=chunk.text[:_FALLBACK_CHAR_LIMIT] + "...",
                    html=chunk.html[:_FALLBACK_CHAR_LIMIT] + "...",
                )
            return chunk


# ── Tier C: metadata reference ────────────────────────────────────────


def _derive_label(chunk: HtmlChunk, decisions: list[tuple[HtmlChunk, PruneDecision]]) -> str:
    """Derive a section label for a Tier C reference."""
    # 1. Nearest HEADING in same parent
    for c, _d in decisions:
        if c.parent_xpath == chunk.parent_xpath and c.chunk_type == ChunkType.HEADING and c.text:
            return c.text[:50]

    # 2. aria-label
    label = chunk.attrs.get("aria-label")
    if label:
        return label[:50]

    # 3. chunk_type display name
    type_names = {
        ChunkType.TEXT_BLOCK: "Text Block",
        ChunkType.TABLE: "Table",
        ChunkType.LIST: "List",
        ChunkType.FORM: "Form",
        ChunkType.MEDIA: "Media",
    }
    return type_names.get(chunk.chunk_type, chunk.chunk_type.value)


def summarize_chunk_tier_c(chunk: HtmlChunk, section_label: str) -> HtmlChunk:
    """Create a metadata reference for a Tier C chunk."""
    ref_text = f"[Section: {section_label}]"
    return replace(chunk, text=ref_text, html=ref_text)


# ── Pipeline integration ─────────────────────────────────────────────


def apply_tier_processing(
    decisions: list[tuple[HtmlChunk, PruneDecision]],
    budget_pressure: float = 1.0,
) -> list[tuple[HtmlChunk, PruneDecision]]:
    """Classify and process kept chunks by tier. Returns new (chunk, decision) list."""
    tier_a_min, tier_b_min = compute_tier_thresholds(budget_pressure)

    result: list[tuple[HtmlChunk, PruneDecision]] = []
    tier_a_count = 0
    tier_b_count = 0

    for chunk, decision in decisions:
        if not decision.keep:
            result.append((chunk, decision))
            continue

        tier = classify_tier(decision, tier_a_min, tier_b_min)
        decision.tier = tier.value

        match tier:
            case ChunkTier.A:
                tier_a_count += 1
                result.append((chunk, decision))
            case ChunkTier.B:
                tier_b_count += 1
                compressed = compress_chunk_tier_b(chunk)
                result.append((compressed, decision))
            case ChunkTier.C:
                label = _derive_label(chunk, decisions)
                summarized = summarize_chunk_tier_c(chunk, label)
                result.append((summarized, decision))

    # Safety: if no A+B chunks, fall back to all Tier A
    if tier_a_count + tier_b_count == 0:
        fallback: list[tuple[HtmlChunk, PruneDecision]] = []
        for chunk, decision in decisions:
            if decision.keep:
                decision.tier = ChunkTier.A.value
            fallback.append((chunk, decision))
        return fallback

    return result


def generate_dropped_references(
    decisions: list[tuple[HtmlChunk, PruneDecision]],
) -> list[HtmlChunk]:
    """Generate Tier C references for budget-dropped chunks."""
    # Group budget-dropped chunks by parent_xpath
    groups: dict[str, list[tuple[HtmlChunk, PruneDecision]]] = defaultdict(list)
    for chunk, decision in decisions:
        if not decision.keep and "budget-drop" in decision.reason_detail:
            groups[chunk.parent_xpath].append((chunk, decision))

    if not groups:
        return []

    references: list[HtmlChunk] = []
    for _parent_xpath, group_items in groups.items():
        first_chunk = group_items[0][0]
        label = _derive_label(first_chunk, decisions)
        count = len(group_items)
        ref_text = f"[Section: {label} ({count} items)]"
        ref_chunk = replace(first_chunk, text=ref_text, html=ref_text)
        references.append(ref_chunk)

    return references
