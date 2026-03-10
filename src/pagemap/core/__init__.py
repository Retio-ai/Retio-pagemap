# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Core engine package: data models, detection, pruning, and classification.

This package contains the pure-logic core of PageMap — no server, cloud,
or telemetry code.  Everything here is a candidate for the future
TypeScript port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pagemap.security.browser_report import BrowserSecurityReport

    from .diagnostics import DiagnosticResult
    from .ecommerce import BarrierResult


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
    name_source: str = ""  # "aria-label", "contents", "title", "placeholder", "alt", "labelledby"

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
    barrier: BarrierResult | None = None  # v0.8.0: Layer 0 barrier detection
    diagnostics: DiagnosticResult | None = None  # v0.8.0 S9: self-healing diagnostics
    browser_security: BrowserSecurityReport | None = None  # v0.9.0: browser-side security scan

    @property
    def total_interactables(self) -> int:
        return len(self.interactables)

    @property
    def tier_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in self.interactables:
            counts[item.tier] = counts.get(item.tier, 0) + 1
        return counts
