# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic extraction schemas for Phase -1 baseline.

Each domain has a flat Pydantic model with Optional fields.
All fields are Optional so we can measure what the LLM can/cannot extract.
Missing fields (None) count as FN in F1 scoring.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# E-commerce
# ---------------------------------------------------------------------------


class ProductExtraction(BaseModel):
    """Product data from e-commerce pages (Schema.org Product)."""

    name: str | None = Field(None, description="Product name/title")
    price: float | None = Field(None, description="Current selling price (numeric, no currency symbol)")
    currency: str | None = Field(None, description="ISO 4217 currency code (e.g., KRW, USD)")
    original_price: float | None = Field(None, description="Original price before discount")
    image_url: str | None = Field(None, description="Main product image URL (absolute)")
    rating: float | None = Field(None, description="Average rating (e.g., 4.5)")
    review_count: int | None = Field(None, description="Number of reviews")
    brand: str | None = Field(None, description="Brand or manufacturer name")


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


class NewsArticleExtraction(BaseModel):
    """Article data from news pages (Schema.org NewsArticle)."""

    headline: str | None = Field(None, description="Article headline/title")
    author: str | None = Field(None, description="Author name")
    date_published: str | None = Field(None, description="Publication date (ISO 8601: YYYY-MM-DD)")
    article_body: str | None = Field(None, description="First 200 chars of article body text")
    publisher: str | None = Field(None, description="Publisher/media organization name")


# ---------------------------------------------------------------------------
# Wiki
# ---------------------------------------------------------------------------


class WikiArticleExtraction(BaseModel):
    """Structured data from wiki pages (Schema.org Article)."""

    title: str | None = Field(None, description="Article title")
    summary: str | None = Field(None, description="First paragraph / lead section (max 200 chars)")
    categories: list[str] | None = Field(None, description="Article categories")
    last_edited: str | None = Field(None, description="Last edit date (ISO 8601)")


# ---------------------------------------------------------------------------
# SaaS (GitHub repos, Notion pages, etc.)
# ---------------------------------------------------------------------------


class SaaSPageExtraction(BaseModel):
    """Structured data from SaaS/tool pages."""

    name: str | None = Field(None, description="Project or page title")
    description: str | None = Field(None, description="Project description or summary")
    primary_language: str | None = Field(None, description="Primary programming language (if applicable)")
    stars: int | None = Field(None, description="Stars, likes, or popularity count")
    license: str | None = Field(None, description="License type (e.g., MIT, Apache-2.0)")


# ---------------------------------------------------------------------------
# Government
# ---------------------------------------------------------------------------


class GovernmentPageExtraction(BaseModel):
    """Structured data from government service pages."""

    title: str | None = Field(None, description="Service or page title")
    department: str | None = Field(None, description="Government department or agency")
    description: str | None = Field(None, description="Service description (max 200 chars)")
    date: str | None = Field(None, description="Publication or last update date (ISO 8601)")
    contact_info: str | None = Field(None, description="Contact information if present")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "Product": ProductExtraction,
    "NewsArticle": NewsArticleExtraction,
    "WikiArticle": WikiArticleExtraction,
    "SaaSPage": SaaSPageExtraction,
    "GovernmentPage": GovernmentPageExtraction,
}

# Field type classification for evaluation matching logic.
# "numeric": relative tolerance matching
# "text": fuzzy string matching (rapidfuzz)
# "long_text": relaxed fuzzy matching
# "url": normalized exact match
# "date": ISO 8601 date-only comparison
# "list": set-based Jaccard overlap
# "exact": exact string match after strip/lower
FIELD_TYPES: dict[str, dict[str, str]] = {
    "Product": {
        "name": "text",
        "price": "numeric",
        "currency": "exact",
        "original_price": "numeric",
        "image_url": "url",
        "rating": "numeric",
        "review_count": "numeric",
        "brand": "text",
    },
    "NewsArticle": {
        "headline": "text",
        "author": "text",
        "date_published": "date",
        "article_body": "long_text",
        "publisher": "text",
    },
    "WikiArticle": {
        "title": "text",
        "summary": "long_text",
        "categories": "list",
        "last_edited": "date",
    },
    "SaaSPage": {
        "name": "text",
        "description": "long_text",
        "primary_language": "exact",
        "stars": "numeric",
        "license": "exact",
    },
    "GovernmentPage": {
        "title": "text",
        "department": "text",
        "description": "long_text",
        "date": "date",
        "contact_info": "text",
    },
}
