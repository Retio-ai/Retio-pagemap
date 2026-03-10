"""Backward-compat shim — import from pagemap.core.ecommerce._card_extractor instead."""

from pagemap.core.ecommerce._card_extractor import (  # noqa: F401
    PaginationRefs,
    extract_cards,
    extract_cards_from_jsonld,
    extract_cards_from_regex,
    find_filter_refs,
    find_pagination_refs,
)

__all__ = [
    "PaginationRefs",
    "extract_cards",
    "extract_cards_from_jsonld",
    "extract_cards_from_regex",
    "find_filter_refs",
    "find_pagination_refs",
]
