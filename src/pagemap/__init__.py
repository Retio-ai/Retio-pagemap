# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Page Map: structured intermediate representation for AI agent web tasks.

Converts web pages from ~100K tokens to 2-5K token structured maps containing:
- interactables: actionable UI elements with affordances
- pruned_context: compressed page content (prices, titles, key info)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Interactable:
    """A single interactive element extracted from the page."""

    ref: int  # sequential number for agent reference
    role: str  # button, link, searchbox, combobox, checkbox, etc.
    name: str  # accessibility name
    affordance: str  # click, type, select, toggle
    region: str  # header, main, footer, navigation, complementary, unknown
    tier: int  # 1-4 detection tier
    value: str = ""  # current value (for inputs)
    options: list[str] = field(default_factory=list)  # for selects/comboboxes
    selector: str = ""  # CSS selector for precise DOM targeting (internal)

    def __str__(self) -> str:
        parts = [f"[{self.ref}]", f"{self.role}:", self.name, f"({self.affordance})"]
        if self.value:
            parts.append(f'value="{self.value}"')
        if self.options:
            parts.append(f"options=[{','.join(self.options[:5])}]")
        return " ".join(parts)


@dataclass
class PageMap:
    """Structured representation of a web page for AI agents."""

    url: str
    title: str
    page_type: str  # product_detail, search_results, article, listing, unknown
    interactables: list[Interactable]
    pruned_context: str  # compressed HTML with key information
    pruned_tokens: int
    generation_ms: float
    images: list[str] = field(default_factory=list)  # product image URLs
    metadata: dict = field(default_factory=dict)  # structured extraction result
    warnings: list[str] = field(default_factory=list)  # degraded mode notices
    navigation_hints: dict = field(default_factory=dict)  # pagination/filter metadata
    pruned_regions: set[str] = field(default_factory=set)  # regions removed by AOM filter

    @property
    def total_interactables(self) -> int:
        return len(self.interactables)

    @property
    def tier_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in self.interactables:
            counts[item.tier] = counts.get(item.tier, 0) + 1
        return counts
