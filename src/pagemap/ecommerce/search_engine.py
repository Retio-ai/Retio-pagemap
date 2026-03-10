"""Backward-compat shim — import from pagemap.core.ecommerce.search_engine instead."""

from pagemap.core.ecommerce.search_engine import (  # noqa: F401
    _detect_sponsored,
    _normalize_result_count,
    analyze_search_results,
)

__all__ = ["analyze_search_results"]
