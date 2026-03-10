"""Backward-compat shim — import from pagemap.core.ecommerce.product_engine instead."""

from pagemap.core.ecommerce.product_engine import (  # noqa: F401
    _build_selected_variant,
    _extract_gallery_images,
    _extract_review_snippets,
    analyze_product,
)

__all__ = ["analyze_product"]
