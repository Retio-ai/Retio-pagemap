"""Backward-compat shim — import from pagemap.core.ecommerce.cart_engine instead."""

from pagemap.core.ecommerce.cart_engine import (  # noqa: F401
    _detect_confirmation,
    _detect_flow_state,
    _extract_cart_count,
    analyze_cart_actions,
)

__all__ = ["analyze_cart_actions"]
