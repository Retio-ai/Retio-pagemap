# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S1 — Config-driven parameter dataclasses for pruning and classification.

No module-level singleton — ``DEFAULT_*`` constants are plain frozen dataclasses.
The live mutable registry lives on ``ServerState`` (server/__init__.py).
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field

# ── Pruning Config ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PruningConfig:
    """All tunable pruning thresholds — matches pruner.py module constants."""

    # In-main thresholds
    in_main_text_min: int = 50  # pruner.py:42
    in_main_media_min: int = 10  # pruner.py:43

    # No-main fallback thresholds
    no_main_text_min: int = 30  # pruner.py:47
    no_main_form_min: int = 20  # pruner.py:48
    no_main_media_min: int = 20  # pruner.py:49

    # Schema-specific body text thresholds
    news_body_min: int = 50  # pruner.py:52
    wiki_summary_min: int = 100  # pruner.py:53
    wiki_section_min: int = 30  # pruner.py:54
    saas_desc_min: int = 50  # pruner.py:55
    gov_body_min: int = 30  # pruner.py:56

    # Coupang recommendation filter
    coupang_price_count_limit: int = 10  # pruner.py:59

    # Additional schema matchers
    faq_body_min: int = 30  # pruner.py:313
    event_desc_min: int = 50  # pruner.py:314
    local_biz_desc_min: int = 50  # pruner.py:315

    # Pipeline feature flags (pruning pipeline improvements)
    enable_scoring: bool = True
    enable_adjacent_boost: bool = True
    enable_sibling_grouping: bool = True
    enable_text_density_signal: bool = False
    enable_expanded_rescue: bool = True
    enable_block_tree_remerge: bool = True


# ── Classifier Config ───────────────────────────────────────────


def _default_thresholds() -> dict[str, int]:
    return {
        "product_detail": 20,
        "search_results": 20,
        "article": 20,
        "news": 20,
        "listing": 20,
        "login": 20,
        "checkout": 20,
        "error": 25,
        "help_faq": 20,
        "documentation": 20,
        "form": 20,
        "dashboard": 20,
        "settings": 20,
        "landing": 25,
        "video": 20,
        "blocked": 20,
    }


def _default_type_priority() -> dict[str, int]:
    return {
        "product_detail": 0,
        "checkout": 1,
        "login": 2,
        "settings": 3,
        "search_results": 4,
        "video": 5,
        "news": 6,
        "article": 7,
        "help_faq": 8,
        "form": 9,
        "documentation": 10,
        "dashboard": 11,
        "listing": 12,
        "error": 13,
        "blocked": 14,
        "landing": 15,
    }


def _default_jsonld_weights() -> dict[str, int]:
    return {
        "product_detail": 40,
        "listing": 40,
        "news": 40,
        "article": 40,
        "help_faq": 40,
        "form": 35,
        "checkout": 40,
        "video": 40,
        "landing": 35,
    }


@dataclass(frozen=True, slots=True)
class ClassifierConfig:
    """All tunable classifier parameters — matches page_classifier.py constants."""

    thresholds: dict[str, int] = field(default_factory=_default_thresholds)
    default_threshold: int = 50  # page_classifier.py:180
    dom_cap: int = 40  # page_classifier.py:185
    type_priority: dict[str, int] = field(default_factory=_default_type_priority)
    jsonld_weights: dict[str, int] = field(default_factory=_default_jsonld_weights)

    def __post_init__(self) -> None:
        t = self.thresholds if isinstance(self.thresholds, dict) else dict(self.thresholds)
        object.__setattr__(self, "thresholds", types.MappingProxyType(t))
        p = self.type_priority if isinstance(self.type_priority, dict) else dict(self.type_priority)
        object.__setattr__(self, "type_priority", types.MappingProxyType(p))
        j = self.jsonld_weights if isinstance(self.jsonld_weights, dict) else dict(self.jsonld_weights)
        object.__setattr__(self, "jsonld_weights", types.MappingProxyType(j))


# ── Module-level defaults (no I/O at import time) ──────────────

DEFAULT_PRUNING_CONFIG = PruningConfig()
DEFAULT_CLASSIFIER_CONFIG = ClassifierConfig()
