"""Backward-compat shim — import from pagemap.core.preprocessing.schemas instead."""

from pagemap.core.preprocessing.schemas import (  # noqa: F401
    FIELD_TYPES,
    SCHEMA_MAP,
    EventExtraction,
    FAQPageExtraction,
    GovernmentPageExtraction,
    LocalBusinessExtraction,
    NewsArticleExtraction,
    ProductExtraction,
    SaaSPageExtraction,
    WikiArticleExtraction,
)

__all__ = [
    "EventExtraction",
    "FAQPageExtraction",
    "FIELD_TYPES",
    "GovernmentPageExtraction",
    "LocalBusinessExtraction",
    "NewsArticleExtraction",
    "ProductExtraction",
    "SCHEMA_MAP",
    "SaaSPageExtraction",
    "WikiArticleExtraction",
]
