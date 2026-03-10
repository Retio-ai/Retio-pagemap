"""Backward-compat shim — import from pagemap.core.pruning.pruner instead."""

from pagemap.core.pruning.pruner import (  # noqa: F401
    _BRAND_RE,
    _CONTACT_RE,
    _DEPARTMENT_RE,
    _FEATURE_RE,
    _PRICE_RE,
    _PRICING_RE,
    _RATING_RE,
    _REASON_SCORES,
    _REPORTER_RE,
    _REVIEW_COUNT_RE,
    PruneDecision,
    _is_high_value_short_text,
    _match_product,
    _xpath_common_depth,
    apply_budget_selection,
    boost_adjacent_chunks,
    prune_chunks,
)

__all__ = ["PruneDecision", "prune_chunks"]
