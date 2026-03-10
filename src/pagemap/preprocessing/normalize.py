"""Backward-compat shim — import from pagemap.core.preprocessing.normalize instead."""

from pagemap.core.preprocessing.normalize import (  # noqa: F401
    PriceParseResult,
    PriceResult,
    detect_currency_from_text,
    format_price,
    infer_currency,
    normalize_date,
    normalize_numeric,
    normalize_price,
    normalize_str,
)

__all__ = [
    "PriceParseResult",
    "PriceResult",
    "detect_currency_from_text",
    "format_price",
    "infer_currency",
    "normalize_date",
    "normalize_numeric",
    "normalize_price",
    "normalize_str",
]
