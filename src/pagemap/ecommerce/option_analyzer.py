"""Backward-compat shim — import from pagemap.core.ecommerce.option_analyzer instead."""

from pagemap.core.ecommerce.option_analyzer import (  # noqa: F401
    OptionValue,
    RichOptionGroup,
    analyze_option_availability,
    compute_blocked_reason,
    get_availability_counts,
    infer_selection_order,
)

__all__ = [
    "OptionValue",
    "RichOptionGroup",
    "analyze_option_availability",
    "compute_blocked_reason",
    "get_availability_counts",
    "infer_selection_order",
]
