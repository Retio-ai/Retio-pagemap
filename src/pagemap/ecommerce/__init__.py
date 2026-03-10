"""Backward-compat shim — import from pagemap.core.ecommerce instead."""

from pagemap.core.ecommerce import (  # noqa: F401
    BarrierResult,
    BarrierType,
    CartAction,
    ListingResult,
    OptionGroup,
    ProductCard,
    ProductResult,
    SearchResult,
    run_ecommerce_engine,
)

__all__ = [
    "BarrierResult",
    "BarrierType",
    "CartAction",
    "ListingResult",
    "OptionGroup",
    "ProductCard",
    "ProductResult",
    "SearchResult",
    "run_ecommerce_engine",
]
