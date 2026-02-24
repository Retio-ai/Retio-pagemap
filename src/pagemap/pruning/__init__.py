# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 0 HTML pruning engine.

Core data structures for the AXE-style XPath-based pruning pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pagemap.errors import PageMapError


class ChunkType(StrEnum):
    """Atomic chunk classification."""

    TABLE = "table"
    LIST = "list"
    TEXT_BLOCK = "text_block"
    HEADING = "heading"
    MEDIA = "media"
    FORM = "form"
    META = "meta"  # JSON-LD, OG meta
    RSC_DATA = "rsc_data"  # Next.js RSC payload (Naver)


class SchemaName(StrEnum):
    """Schema classification for pruning heuristics."""

    PRODUCT = "Product"
    NEWS_ARTICLE = "NewsArticle"
    WIKI_ARTICLE = "WikiArticle"
    SAAS_PAGE = "SaaSPage"
    GOVERNMENT_PAGE = "GovernmentPage"
    FAQ_PAGE = "FAQPage"
    EVENT = "Event"
    LOCAL_BUSINESS = "LocalBusiness"
    VIDEO_OBJECT = "VideoObject"
    GENERIC = "Generic"


class PageType(StrEnum):
    """Page type classification for compression strategy."""

    PRODUCT_DETAIL = "product_detail"
    SEARCH_RESULTS = "search_results"
    ARTICLE = "article"
    LISTING = "listing"
    NEWS = "news"
    # P7.1 new types
    LOGIN = "login"
    FORM = "form"
    CHECKOUT = "checkout"
    DASHBOARD = "dashboard"
    HELP_FAQ = "help_faq"
    SETTINGS = "settings"
    ERROR = "error"
    DOCUMENTATION = "documentation"
    LANDING = "landing"
    VIDEO = "video"
    UNKNOWN = "unknown"


class PruneReason(StrEnum):
    """Why a chunk was kept or removed."""

    META_ALWAYS = "meta-always-keep"
    SCHEMA_MATCH = "schema-match"
    COUPANG_REC_FILTER = "coupang-recommendation-filter"
    IN_MAIN_HEADING = "in-main-heading"
    IN_MAIN_TEXT = "in-main-text"
    IN_MAIN_HV_SHORT = "in-main-high-value-short"
    IN_MAIN_STRUCTURED = "in-main-structured"
    IN_MAIN_FORM = "in-main-form"
    IN_MAIN_MEDIA = "in-main-media"
    IN_MAIN_SHORT = "in-main-short"
    KEEP_HEADING_NO_MAIN = "keep-heading-no-main"
    KEEP_TEXT_NO_MAIN = "keep-text-no-main"
    KEEP_FORM_NO_MAIN = "keep-form-no-main"
    KEEP_MEDIA_NO_MAIN = "keep-media-no-main"
    NO_MATCH = "no-match"


@dataclass(frozen=True, slots=True)
class HtmlChunk:
    """An atomic HTML chunk extracted from the DOM tree."""

    xpath: str
    html: str
    text: str
    tag: str
    chunk_type: ChunkType
    attrs: dict = field(default_factory=dict, hash=False)
    parent_xpath: str = ""
    depth: int = 0
    in_main: bool = False


class PruningError(PageMapError):
    """Raised when HTML parsing or pruning fails fatally."""
