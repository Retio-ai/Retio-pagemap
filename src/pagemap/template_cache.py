# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Template cache for domain+page_type structural knowledge.

Caches "structural processing intelligence" learned from the first page build
of a (domain, page_type) pair. Subsequent pages on the same domain+page_type
can skip redundant exploration steps (metadata source cascade, card detection
strategy, pagination parameter scan) by using cached hints.

The template cache is independent of the PageMap URL LRU cache — domain
structural knowledge survives browser crashes and session resets.

Architecture mirrors cache.py: OrderedDict LRU + TTL + Stats.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pagemap.pruning.pipeline import PruningResult

logger = logging.getLogger("pagemap.template_cache")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_TEMPLATES = 50
DEFAULT_TTL_SECONDS = 86_400.0  # 24 hours
MAX_CONSECUTIVE_FAILURES = 3


# ---------------------------------------------------------------------------
# Template key
# ---------------------------------------------------------------------------


def extract_template_domain(url: str) -> str:
    """Extract normalized domain from URL for template cache key.

    Strips ``www.`` prefix, lowercases, and removes port numbers.
    Returns empty string for invalid/missing URLs.
    """
    try:
        hostname = urlparse(url).hostname
    except Exception:
        return ""
    if not hostname:
        return ""
    return hostname.lower().removeprefix("www.")


@dataclass(frozen=True, slots=True)
class TemplateKey:
    """Cache key: (domain, page_type)."""

    domain: str
    page_type: str


# ---------------------------------------------------------------------------
# Template data (immutable structural knowledge)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemplateData:
    """Immutable structural knowledge learned from the first page build."""

    schema_name: str = ""
    has_main: bool = False
    has_json_ld: bool = False
    metadata_source: str = ""  # "json_ld" | "itemprop" | "og" | "h1"
    metadata_fields_found: frozenset[str] = field(default_factory=frozenset)
    card_strategy: str | None = None  # "json_ld_itemlist" | "chunks" | None
    has_pagination: bool = False
    pagination_param: str | None = None  # "page" | "p" | "none" etc.
    aom_removal_ratio: float = 0.0
    chunk_selection_ratio: float = 0.0


# ---------------------------------------------------------------------------
# Page template (mutable cache wrapper)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PageTemplate:
    """Mutable cache wrapper around immutable TemplateData."""

    data: TemplateData
    key: TemplateKey
    hit_count: int = 0
    consecutive_failures: int = 0
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = 0.0
    source_url: str = ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_AOM_RATIO_TOLERANCE = 0.3
_CHUNK_RATIO_TOLERANCE = 0.3


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of template validation against actual page build."""

    passed: bool
    mismatches: tuple[str, ...] = ()


def validate_template(
    template: PageTemplate,
    actual_has_main: bool,
    actual_metadata_source: str,
    actual_aom_removal_ratio: float,
    actual_chunk_selection_ratio: float,
) -> ValidationResult:
    """Validate a cached template against actual page build results.

    Checks structural invariants. Returns mismatches on failure.
    """
    mismatches: list[str] = []
    td = template.data

    if td.has_main != actual_has_main:
        mismatches.append(f"has_main: expected={td.has_main}, actual={actual_has_main}")

    if td.metadata_source and actual_metadata_source and td.metadata_source != actual_metadata_source:
        mismatches.append(f"metadata_source: expected={td.metadata_source}, actual={actual_metadata_source}")

    if abs(td.aom_removal_ratio - actual_aom_removal_ratio) > _AOM_RATIO_TOLERANCE:
        mismatches.append(
            f"aom_removal_ratio: expected={td.aom_removal_ratio:.2f}, actual={actual_aom_removal_ratio:.2f}"
        )

    if abs(td.chunk_selection_ratio - actual_chunk_selection_ratio) > _CHUNK_RATIO_TOLERANCE:
        mismatches.append(
            f"chunk_selection_ratio: expected={td.chunk_selection_ratio:.2f}, actual={actual_chunk_selection_ratio:.2f}"
        )

    return ValidationResult(passed=len(mismatches) == 0, mismatches=tuple(mismatches))


# ---------------------------------------------------------------------------
# Cache stats
# ---------------------------------------------------------------------------


@dataclass
class TemplateCacheStats:
    """Counters for template cache observability."""

    hits: int = 0
    misses: int = 0
    templates_created: int = 0
    validations_passed: int = 0
    validations_failed: int = 0
    invalidations: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# InMemoryTemplateCache
# ---------------------------------------------------------------------------


class InMemoryTemplateCache:
    """LRU cache of PageTemplates keyed by (domain, page_type).

    TTL: 24h default.  Max 50 entries.  Not thread-safe (STDIO transport).
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_TEMPLATES,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_entries = max_entries
        self._ttl = ttl
        self._entries: OrderedDict[TemplateKey, PageTemplate] = OrderedDict()
        self._stats = TemplateCacheStats()

    # -- Lookup --

    def lookup(self, key: TemplateKey) -> PageTemplate | None:
        """Look up a template.  Returns None if missing or TTL-expired."""
        entry = self._entries.get(key)
        if entry is None:
            self._stats.misses += 1
            return None

        # TTL check
        age = time.monotonic() - entry.created_at
        if age > self._ttl:
            del self._entries[key]
            self._stats.misses += 1
            logger.debug("Template TTL expired: %s", key)
            return None

        # LRU refresh + hit tracking
        self._entries.move_to_end(key)
        entry.hit_count += 1
        entry.last_used_at = time.monotonic()
        self._stats.hits += 1
        return entry

    # -- Store --

    def store(self, template: PageTemplate) -> None:
        """Store (or overwrite) a template.  Evicts LRU if over capacity."""
        key = template.key

        # Overwrite existing
        if key in self._entries:
            self._entries[key] = template
            self._entries.move_to_end(key)
        else:
            self._entries[key] = template
            self._stats.templates_created += 1

        # LRU eviction
        while len(self._entries) > self._max_entries:
            evicted_key, _ = self._entries.popitem(last=False)
            self._stats.evictions += 1
            logger.debug("Template evicted: %s", evicted_key)

        logger.debug("Template stored: %s (size=%d)", key, len(self._entries))

    # -- Invalidation --

    def invalidate(self, key: TemplateKey) -> bool:
        """Invalidate a single template.  Returns True if it existed."""
        if key in self._entries:
            del self._entries[key]
            self._stats.invalidations += 1
            logger.debug("Template invalidated: %s", key)
            return True
        return False

    def invalidate_domain(self, domain: str) -> int:
        """Invalidate all templates for a domain.  Returns count removed."""
        keys_to_remove = [k for k in self._entries if k.domain == domain]
        for k in keys_to_remove:
            del self._entries[k]
            self._stats.invalidations += 1
        if keys_to_remove:
            logger.debug("Templates invalidated for domain=%s: %d", domain, len(keys_to_remove))
        return len(keys_to_remove)

    def invalidate_all(self) -> None:
        """Clear all templates."""
        count = len(self._entries)
        self._entries.clear()
        self._stats.invalidations += count

    # -- Validation tracking --

    def record_validation_pass(self, key: TemplateKey) -> None:
        """Record a successful validation — resets consecutive failure count."""
        entry = self._entries.get(key)
        if entry is not None:
            entry.consecutive_failures = 0
            self._stats.validations_passed += 1

    def record_validation_failure(self, key: TemplateKey) -> None:
        """Record a validation failure.  Auto-invalidates after 3 consecutive failures."""
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.consecutive_failures += 1
        self._stats.validations_failed += 1

        if entry.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.invalidate(key)
            logger.warning(
                "Template auto-invalidated after %d consecutive failures: %s",
                MAX_CONSECUTIVE_FAILURES,
                key,
            )

    # -- Stats --

    @property
    def stats(self) -> TemplateCacheStats:
        return self._stats

    def peek(self, key: TemplateKey) -> PageTemplate | None:
        """Return a template without modifying stats or LRU order."""
        return self._entries.get(key)

    @property
    def size(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Learning — post-hoc extraction from pipeline results
# ---------------------------------------------------------------------------


def infer_metadata_source(
    metadata: dict[str, Any],
    meta_chunks: list,
) -> str:
    """Infer which extraction strategy produced the metadata (post-hoc).

    Compares metadata keys with JSON-LD parse results to determine source.
    Falls back to checking for itemprop/og patterns.
    """
    if not metadata:
        return ""

    # Try JSON-LD comparison
    from pagemap.metadata import _parse_json_ld_product

    json_ld = _parse_json_ld_product(meta_chunks)
    if json_ld:
        # If JSON-LD produced most of the same keys, it was likely the source
        overlap = set(json_ld.keys()) & set(metadata.keys())
        if len(overlap) >= 2 or (len(overlap) >= 1 and "name" in overlap):
            return "json_ld"

    # No JSON-LD match — check OG or itemprop
    if metadata.get("name"):
        has_og_chunk = any(chunk.attrs.get("property", "").startswith("og:") for chunk in meta_chunks)
        if has_og_chunk:
            return "og"
        return "itemprop"

    return ""


def _infer_card_strategy(metadata: dict[str, Any] | None) -> str | None:
    """Infer card detection strategy from metadata."""
    if not metadata:
        return None
    items = metadata.get("items")
    if isinstance(items, list) and items:
        # Check if items look like JSON-LD ItemList entries
        first = items[0] if items else {}
        if isinstance(first, dict) and ("url" in first or "position" in first):
            return "json_ld_itemlist"
    return None


def _infer_pagination_param(raw_html: str) -> str | None:
    """Infer the pagination parameter name from raw HTML.

    Checks common patterns: page, p, pg, pn, pageNo, etc.
    Returns the first matching parameter name, or None.
    """
    import re

    # Check common pagination parameter patterns in order of frequency
    params = ["page", "p", "pg", "pn", "pageNo", "pageNum", "currentPage"]
    for param in params:
        pattern = rf'(?:href|action)=["\'][^"\']*[?&]{re.escape(param)}=\d+'
        if re.search(pattern, raw_html, re.IGNORECASE):
            return param
    return None


def learn_template(
    key: TemplateKey,
    schema_name: str,
    pruning_result: PruningResult,
    metadata: dict[str, Any],
    source_url: str,
    raw_html: str = "",
) -> PageTemplate:
    """Learn a template from a completed page build.

    Extracts structural patterns from the PruningResult and metadata
    without modifying any existing pipeline functions.
    """
    # has_main: any selected chunk in <main>
    has_main = any(c.in_main for c in pruning_result.selected_chunks)

    # has_json_ld: meta chunks contain application/ld+json
    has_json_ld = any(c.attrs.get("type") == "application/ld+json" for c in pruning_result.meta_chunks)

    # Metadata source inference
    metadata_source = infer_metadata_source(metadata, pruning_result.meta_chunks)

    # Fields found
    metadata_fields_found = frozenset(metadata.keys()) - {"items", "_pruning_result"}

    # Card strategy
    card_strategy = _infer_card_strategy(metadata)

    # AOM removal ratio
    aom_stats = pruning_result.aom_filter_stats
    aom_removal_ratio = aom_stats.removed_nodes / max(aom_stats.total_nodes, 1)

    # Chunk selection ratio
    chunk_selection_ratio = pruning_result.chunk_count_selected / max(pruning_result.chunk_count_total, 1)

    # Pagination
    has_pagination = False
    pagination_param: str | None = None
    if raw_html and key.page_type in ("listing", "search_results"):
        pagination_param = _infer_pagination_param(raw_html)
        has_pagination = pagination_param is not None

    data = TemplateData(
        schema_name=schema_name,
        has_main=has_main,
        has_json_ld=has_json_ld,
        metadata_source=metadata_source,
        metadata_fields_found=metadata_fields_found,
        card_strategy=card_strategy,
        has_pagination=has_pagination,
        pagination_param=pagination_param,
        aom_removal_ratio=aom_removal_ratio,
        chunk_selection_ratio=chunk_selection_ratio,
    )

    return PageTemplate(
        data=data,
        key=key,
        created_at=time.monotonic(),
        source_url=source_url,
    )
