# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""PageMap serialization: JSON and agent prompt formats.

Three output formats:
- JSON: structured data for programmatic consumption
- Agent prompt: minimal-token format for LLM agent consumption
- Diff prompt: section-level diff for cache-refreshed PageMaps
"""

from __future__ import annotations

import json
from typing import Any

from pagemap.preprocessing.preprocess import count_tokens
from pagemap.sanitizer import add_content_boundary, sanitize_content_block, sanitize_text

from . import Interactable, PageMap


def _render_interactable_line(item: Interactable, pruned_regions: set[str] | None = None) -> str:
    """Render a single interactable as an agent prompt line."""
    name = sanitize_text(item.name)
    line = f"[{item.ref}] {item.role}: {name} ({item.affordance})"
    if item.value:
        line += f' value="{sanitize_text(item.value)}"'
    if item.options:
        opts = ",".join(sanitize_text(o, max_len=100) for o in item.options[:8])
        if len(item.options) > 8:
            opts += f"...+{len(item.options) - 8}"
        line += f" options=[{opts}]"
    if item.tier >= 3:
        line += " [CDP-detected]"
    if pruned_regions and item.region in pruned_regions:
        line += " [context pruned]"
    return line


def to_json(page_map: PageMap, indent: int = 2) -> str:
    """Serialize PageMap to JSON string.

    Args:
        page_map: PageMap to serialize
        indent: JSON indentation level

    Returns:
        JSON string
    """
    data = {
        "url": page_map.url,
        "title": page_map.title,
        "page_type": page_map.page_type,
        "interactables": [
            {
                "ref": i.ref,
                "role": i.role,
                "name": i.name,
                "affordance": i.affordance,
                "region": i.region,
                "tier": i.tier,
                **({"value": i.value} if i.value else {}),
                **({"options": i.options} if i.options else {}),
            }
            for i in page_map.interactables
        ],
        "pruned_context": page_map.pruned_context,
        "images": page_map.images,
        **({"metadata": page_map.metadata} if page_map.metadata else {}),
        **({"warnings": page_map.warnings} if page_map.warnings else {}),
        **({"navigation_hints": page_map.navigation_hints} if page_map.navigation_hints else {}),
        "meta": {
            "pruned_tokens": page_map.pruned_tokens,
            "interactable_count": page_map.total_interactables,
            "generation_ms": round(page_map.generation_ms, 1),
            "tier_counts": page_map.tier_counts,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=indent)


def to_dict(page_map: PageMap) -> dict[str, Any]:
    """Serialize PageMap to a dictionary."""
    return json.loads(to_json(page_map))


def to_agent_prompt(
    page_map: PageMap,
    include_meta: bool = False,
    cache_meta: str = "",
) -> str:
    """Serialize PageMap to minimal-token agent prompt format.

    Format:
        URL: coupang.com/vp/products/123
        Type: product_detail

        ## Actions
        [1] searchbox: 쿠팡 검색 (type)
        [2] button: 장바구니 담기 (click)
        [3] select: 사이즈 선택 (select) options=[S,M,L,XL]

        ## Info
        제목: 오버핏 레더 자켓
        가격: 189,000원 (원가 259,000원)
        평점: 4.6 (847개 리뷰)

    Args:
        page_map: PageMap to serialize
        include_meta: include token counts and generation time
        cache_meta: optional cache status string for Meta section

    Returns:
        Formatted string for LLM consumption
    """
    lines: list[str] = []

    # Header
    lines.append(f"URL: {page_map.url}")
    if page_map.title:
        lines.append(f"Title: {sanitize_text(page_map.title)}")
    lines.append(f"Type: {page_map.page_type}")
    lines.append("")

    # Warnings section (degraded mode notices)
    if page_map.warnings:
        lines.append("## Warnings")
        for w in page_map.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Actions section
    if page_map.interactables:
        lines.append("## Actions")
        for item in page_map.interactables:
            lines.append(_render_interactable_line(item, page_map.pruned_regions))
        lines.append("")

    # Navigation section (pagination + filter hints)
    if page_map.navigation_hints:
        lines.append("## Navigation")
        pag = page_map.navigation_hints.get("pagination", {})
        if pag:
            parts: list[str] = []
            cp = pag.get("current_page")
            tp = pag.get("total_pages")
            if cp and tp:
                parts.append(f"Page {cp}/{tp}")
            elif tp:
                parts.append(f"~{tp} pages")
            ti = pag.get("total_items")
            if ti:
                parts.append(str(ti))
            nr = pag.get("next_ref")
            if nr:
                parts.append(f"Next: [{nr}]")
            pr = pag.get("prev_ref")
            if pr:
                parts.append(f"Prev: [{pr}]")
            lmr = pag.get("load_more_ref")
            if lmr:
                parts.append(f"Load more: [{lmr}]")
            if parts:
                lines.append(" | ".join(parts))
        flt = page_map.navigation_hints.get("filters", {})
        fr = flt.get("filter_refs", [])
        if fr:
            lines.append("Filters: " + ", ".join(f"[{r}]" for r in fr))
        lines.append("")

    # Info section — wrapped with content boundary
    if page_map.pruned_context:
        lines.append("## Info")
        sanitized_context = sanitize_content_block(page_map.pruned_context)
        lines.append(add_content_boundary(sanitized_context, page_map.url))
        lines.append("")

    # Images section
    if page_map.images:
        lines.append("## Images")
        for i, url in enumerate(page_map.images[:5], 1):
            lines.append(f"  [{i}] {url}")
        lines.append("")

    # Optional meta
    if include_meta:
        prompt_text = "\n".join(lines)
        total_tokens = count_tokens(prompt_text)
        lines.append("## Meta")
        lines.append(f"Tokens: ~{total_tokens}")
        lines.append(f"Interactables: {page_map.total_interactables}")
        lines.append(f"Generation: {page_map.generation_ms:.0f}ms")
        if cache_meta:
            lines.append(f"Cache: {cache_meta}")

    return "\n".join(lines)


def estimate_prompt_tokens(page_map: PageMap) -> int:
    """Estimate total token count of the agent prompt format."""
    prompt = to_agent_prompt(page_map)
    return count_tokens(prompt)


# ---------------------------------------------------------------------------
# Section comparison helpers (for diff output)
# ---------------------------------------------------------------------------


def _interactables_equal(old: list[Interactable], new: list[Interactable]) -> bool:
    """Compare interactable lists by semantic content (role, name, affordance, value, options)."""
    if len(old) != len(new):
        return False
    for a, b in zip(old, new, strict=True):
        if (a.role, a.name, a.affordance, a.value, tuple(a.options)) != (
            b.role,
            b.name,
            b.affordance,
            b.value,
            tuple(b.options),
        ):
            return False
    return True


def _pruned_context_equal(old: str, new: str) -> bool:
    return old == new


def _images_equal(old: list[str], new: list[str]) -> bool:
    return old == new


def _navigation_equal(old: dict, new: dict) -> bool:
    return old == new


# ---------------------------------------------------------------------------
# Change summary for diff header
# ---------------------------------------------------------------------------


def _generate_change_summary(
    old: PageMap,
    new: PageMap,
    *,
    actions_changed: bool,
    info_changed: bool,
    images_changed: bool,
    navigation_changed: bool,
) -> list[str]:
    """Generate bullet list of what changed between two PageMaps."""
    changes: list[str] = []
    if actions_changed:
        old_count = len(old.interactables)
        new_count = len(new.interactables)
        if old_count != new_count:
            diff = new_count - old_count
            direction = "new" if diff > 0 else "removed"
            changes.append(f"Actions: {abs(diff)} {direction} items ({new_count} total)")
        else:
            changes.append(f"Actions: content updated ({new_count} items)")
    if info_changed:
        changes.append("Info: content updated")
    if navigation_changed:
        changes.append("Navigation: updated")
    if images_changed:
        changes.append("Images: updated")
    return changes


# ---------------------------------------------------------------------------
# Diff output format
# ---------------------------------------------------------------------------


def to_agent_prompt_diff(
    old: PageMap,
    new: PageMap,
    cache_age_s: float = 0.0,
    include_meta: bool = False,
    savings_threshold: float = 0.20,
) -> str | None:
    """Compare two PageMaps and return a diff string.

    Changed sections are fully re-sent.  Unchanged sections get a compact
    "— unchanged (N items, refs [1]-[N])" marker.

    Returns None if savings are below threshold (caller should fall back to full prompt).
    """
    # Compare each section
    actions_same = _interactables_equal(old.interactables, new.interactables)
    info_same = _pruned_context_equal(old.pruned_context, new.pruned_context)
    images_same = _images_equal(old.images, new.images)
    nav_same = _navigation_equal(old.navigation_hints, new.navigation_hints)

    # All sections identical → "unchanged" response
    if actions_same and info_same and images_same and nav_same:
        lines = ["PageMap Update", "Status: unchanged"]
        if new.interactables:
            lines.append(f"Refs: 1-{len(new.interactables)} still valid")
        if include_meta:
            full_tokens = count_tokens(to_agent_prompt(new))
            lines.append("")
            lines.append("## Meta")
            lines.append(f"Tokens: ~{count_tokens(chr(10).join(lines))} (full: ~{full_tokens})")
            lines.append(f"Cache: hit | age={cache_age_s:.0f}s")
        return "\n".join(lines)

    # Build diff output
    change_summary = _generate_change_summary(
        old,
        new,
        actions_changed=not actions_same,
        info_changed=not info_same,
        images_changed=not images_same,
        navigation_changed=not nav_same,
    )

    lines: list[str] = []
    lines.append("PageMap Update")
    lines.append("Status: updated | refs expired" if not actions_same else "Status: updated")
    if change_summary:
        lines.append("Changes:")
        for c in change_summary:
            lines.append(f"- {c}")
    lines.append("")

    # Header (always include)
    lines.append(f"URL: {new.url}")
    if new.title:
        lines.append(f"Title: {sanitize_text(new.title)}")
    lines.append(f"Type: {new.page_type}")
    lines.append("")

    # Actions section
    if not actions_same:
        if new.interactables:
            lines.append(f"## Actions ({len(new.interactables)} total)")
            for item in new.interactables:
                lines.append(_render_interactable_line(item, new.pruned_regions))
            lines.append("")
    else:
        if new.interactables:
            lines.append(
                f"## Actions — unchanged ({len(new.interactables)} items, refs [1]-[{len(new.interactables)}])"
            )
            lines.append("")

    # Navigation section
    if not nav_same:
        if new.navigation_hints:
            lines.append("## Navigation (updated)")
            pag = new.navigation_hints.get("pagination", {})
            if pag:
                parts: list[str] = []
                cp = pag.get("current_page")
                tp = pag.get("total_pages")
                if cp and tp:
                    parts.append(f"Page {cp}/{tp}")
                elif tp:
                    parts.append(f"~{tp} pages")
                ti = pag.get("total_items")
                if ti:
                    parts.append(str(ti))
                nr = pag.get("next_ref")
                if nr:
                    parts.append(f"Next: [{nr}]")
                pr = pag.get("prev_ref")
                if pr:
                    parts.append(f"Prev: [{pr}]")
                lmr = pag.get("load_more_ref")
                if lmr:
                    parts.append(f"Load more: [{lmr}]")
                if parts:
                    lines.append(" | ".join(parts))
            flt = new.navigation_hints.get("filters", {})
            fr = flt.get("filter_refs", [])
            if fr:
                lines.append("Filters: " + ", ".join(f"[{r}]" for r in fr))
            lines.append("")
    else:
        if new.navigation_hints:
            lines.append("## Navigation — unchanged")
            lines.append("")

    # Info section
    if not info_same:
        if new.pruned_context:
            lines.append("## Info (updated)")
            sanitized_context = sanitize_content_block(new.pruned_context)
            lines.append(add_content_boundary(sanitized_context, new.url))
            lines.append("")
    else:
        if new.pruned_context:
            lines.append("## Info — unchanged")
            lines.append("")

    # Images section
    if not images_same:
        if new.images:
            lines.append("## Images (updated)")
            for i, url in enumerate(new.images[:5], 1):
                lines.append(f"  [{i}] {url}")
            lines.append("")
    else:
        if new.images:
            lines.append("## Images — unchanged")
            lines.append("")

    diff_text = "\n".join(lines)

    # Check savings threshold
    full_text = to_agent_prompt(new)
    full_tokens = count_tokens(full_text)
    diff_tokens = count_tokens(diff_text)
    savings = (full_tokens - diff_tokens) / max(full_tokens, 1)
    if savings < savings_threshold:
        return None  # Not enough savings — caller should use full prompt

    # Meta section
    if include_meta:
        saved = full_tokens - diff_tokens
        lines.append("## Meta")
        lines.append(f"Tokens: ~{diff_tokens} (full: ~{full_tokens}, saved: ~{saved})")
        lines.append(f"Cache: partial | age={cache_age_s:.0f}s")

    return "\n".join(lines)
