"""Backward-compat shim — import from pagemap.core.pruning.aom_filter instead."""

from pagemap.core.pruning.aom_filter import (  # noqa: F401
    _ARTICLE_ANCESTOR_TAGS,
    _FONT_SIZE_ZERO_RE,
    _H1_RESCUE_RE,
    _OPACITY_ZERO_RE,
    _RATING_RESCUE_RE,
    _REASON_TO_REGION,
    _REVIEW_COUNT_RESCUE_RE,
    _TEXT_DENSITY_MIN_HTML_SIZE,
    _TEXT_DENSITY_THRESHOLD,
    _TEXT_DENSITY_WEIGHT,
    AomFilterStats,
    _compute_weight,
    _count_content_matches,
    _count_noise_matches,
    _detect_repeating_grids,
    _has_interactive_descendants,
    _is_body_direct_child,
    _is_inside_article_or_main,
    aom_filter,
    derive_pruned_regions,
)

__all__ = ["AomFilterStats", "aom_filter", "derive_pruned_regions"]
