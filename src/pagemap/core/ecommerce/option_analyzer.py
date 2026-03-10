# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Option Analyzer — availability detection for product options.

Detects:
  - <option disabled> elements
  - Swatch buttons with disabled/sold-out CSS classes
  - aria-disabled="true" attributes
  - Sold-out text in multiple languages (via i18n)
  - Option selection order from DOM structure
  - Blocked reasons for cart actions

Never raises — returns gracefully degraded results on failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Interactable

from ..i18n import OPTION_UNAVAILABLE_TERMS
from . import OptionGroup

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OptionValue:
    """A single option value with availability metadata."""

    value: str
    available: bool = True
    ref: int | None = None
    price_modifier: str | None = None


@dataclass(frozen=True, slots=True)
class RichOptionGroup:
    """An OptionGroup enriched with availability data."""

    label: str = ""
    type: str = "other"  # "size" | "color" | "other"
    values: tuple[str, ...] = ()
    ref: int | None = None
    selected: str | None = None
    rich_values: tuple[OptionValue, ...] = ()
    required: bool = True
    selection_order: int | None = None


# Pre-compiled patterns
_DISABLED_OPTION_RE = re.compile(
    r"<option[^>]+disabled[^>]*>([^<]*)</option>",
    re.IGNORECASE,
)

_DISABLED_SWATCH_RE = re.compile(
    r'<(?:button|div|span|a)[^>]*class=["\'][^"\']*(?:disabled|sold-out|soldout|unavailable|out-of-stock|inactive)[^"\']*["\'][^>]*>([^<]{1,80})',
    re.IGNORECASE,
)

_ARIA_DISABLED_RE = re.compile(
    r'<(?:button|div|span|a)[^>]*aria-disabled=["\']true["\'][^>]*>([^<]{1,80})',
    re.IGNORECASE,
)

_UNAVAILABLE_TERMS_LOWER = tuple(t.lower() for t in OPTION_UNAVAILABLE_TERMS)

# Size-type option labels for ordering inference
_SIZE_TYPE_LABELS = ("size", "사이즈", "サイズ", "taille", "größe", "尺码")
_COLOR_TYPE_LABELS = ("color", "colour", "컬러", "색상", "カラー", "couleur", "farbe", "颜色")


def analyze_option_availability(
    options: tuple[OptionGroup, ...],
    raw_html: str = "",
    html_lower: str = "",
    interactables: list[Interactable] | None = None,
) -> tuple[RichOptionGroup, ...]:
    """Enrich OptionGroups with availability data from HTML analysis.

    Never raises — returns original groups wrapped as RichOptionGroup on failure.
    """
    try:
        # Collect all unavailable values from HTML
        unavailable_values = _collect_unavailable_values(raw_html, html_lower)

        # Build rich groups
        rich_groups: list[RichOptionGroup] = []
        for _i, group in enumerate(options):
            order = infer_selection_order(group)
            rich_values = _build_rich_values(group, unavailable_values, interactables)

            rich_groups.append(
                RichOptionGroup(
                    label=group.label,
                    type=group.type,
                    values=group.values,
                    ref=group.ref,
                    selected=group.selected,
                    rich_values=rich_values,
                    required=True,
                    selection_order=order,
                )
            )

        return tuple(rich_groups)

    except Exception as e:
        logger.debug("Option analyzer error: %s", e)
        return tuple(
            RichOptionGroup(
                label=g.label,
                type=g.type,
                values=g.values,
                ref=g.ref,
                selected=g.selected,
            )
            for g in options
        )


def _collect_unavailable_values(raw_html: str, html_lower: str) -> set[str]:
    """Collect option values marked as unavailable in HTML."""
    unavailable: set[str] = set()

    # 1. <option disabled>
    for m in _DISABLED_OPTION_RE.finditer(raw_html):
        val = m.group(1).strip()
        if val:
            unavailable.add(val.lower())

    # 2. Swatch buttons with disabled/sold-out classes
    for m in _DISABLED_SWATCH_RE.finditer(raw_html):
        val = m.group(1).strip()
        if val:
            unavailable.add(val.lower())

    # 3. aria-disabled="true"
    for m in _ARIA_DISABLED_RE.finditer(raw_html):
        val = m.group(1).strip()
        if val:
            unavailable.add(val.lower())

    # 4. Text-based unavailable detection
    # NOTE: This heuristic searches raw HTML for sold-out terms near option-like
    # text. It cannot detect "M - 품절" inside <option> without disabled attr,
    # as the value "M" does not itself contain the term.
    for term in _UNAVAILABLE_TERMS_LOWER:
        if term in html_lower:
            # Find the surrounding context to identify which option value
            idx = html_lower.find(term)
            if idx >= 0:
                context = html_lower[max(0, idx - 50) : idx + len(term) + 50]
                # Look for option-like text near the unavailable term
                val_match = re.search(r">[^<]{1,30}<", context)
                if val_match:
                    val = val_match.group(0).strip("><").strip()
                    if val and val.lower() != term:
                        unavailable.add(val.lower())

    return unavailable


def _build_rich_values(
    group: OptionGroup,
    unavailable_values: set[str],
    interactables: list[Interactable] | None,
) -> tuple[OptionValue, ...]:
    """Build OptionValue tuples with availability status."""
    rich: list[OptionValue] = []
    for val in group.values:
        available = val.lower() not in unavailable_values
        # Also check if value text contains unavailable terms
        val_lower = val.lower()
        for term in _UNAVAILABLE_TERMS_LOWER:
            if term in val_lower:
                available = False
                break

        ref = None
        if interactables:
            for item in interactables:
                if item.name.lower() == val_lower:
                    ref = item.ref
                    break

        rich.append(OptionValue(value=val, available=available, ref=ref))

    return tuple(rich)


def infer_selection_order(group: OptionGroup) -> int | None:
    """Infer the selection order for an option group.

    Convention: color (1) before size (2), others (3).
    Returns None if type is unknown.
    """
    label_lower = group.label.lower()
    type_lower = group.type.lower()

    if type_lower == "color" or any(kw in label_lower for kw in _COLOR_TYPE_LABELS):
        return 1
    if type_lower == "size" or any(kw in label_lower for kw in _SIZE_TYPE_LABELS):
        return 2
    return 3


def compute_blocked_reason(
    options: tuple[RichOptionGroup, ...],
    atc_ref: int | None,
    availability: str | None = None,
) -> str | None:
    """Determine why the Add-to-Cart action might be blocked.

    Returns:
        One of: "size_required" | "color_required" | "options_required" |
        "out_of_stock" | "all_sold_out" | None (not blocked)
    """
    # Check availability first
    if availability in ("out_of_stock", "sold_out"):
        return "out_of_stock"

    # Check if all option values are sold out
    for group in options:
        if group.rich_values:
            all_sold = all(not v.available for v in group.rich_values)
            if all_sold:
                return "all_sold_out"

    # Check for unselected required options
    unselected: list[str] = []
    for group in options:
        if group.required and not group.selected:
            unselected.append(group.type)

    if not unselected:
        return None

    if len(unselected) == 1:
        if "size" in unselected:
            return "size_required"
        if "color" in unselected:
            return "color_required"

    if unselected:
        return "options_required"

    return None


def get_availability_counts(
    options: tuple[RichOptionGroup, ...],
) -> tuple[int, int]:
    """Count available and unavailable option values across all groups.

    Returns:
        (available_count, unavailable_count)
    """
    available = 0
    unavailable = 0
    for group in options:
        for val in group.rich_values:
            if val.available:
                available += 1
            else:
                unavailable += 1
    return available, unavailable
