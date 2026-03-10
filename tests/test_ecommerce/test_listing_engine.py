# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for listing engine (Layer 1)."""

from __future__ import annotations

from pagemap.ecommerce.listing_engine import analyze_listing

from .conftest import BREADCRUMB_JSONLD, ITEMLIST_JSONLD


class TestListingEngine:
    def test_basic_listing(self, sample_interactables):
        html = f"<html><body>{BREADCRUMB_JSONLD}{ITEMLIST_JSONLD}</body></html>"
        result = analyze_listing(
            raw_html=html,
            html_lower=html.lower(),
            interactables=sample_interactables,
            metadata={},
            page_url="https://example.com/women/jackets",
            navigation_hints={},
        )
        assert result.category == "Jackets"
        assert result.breadcrumbs == ("Home", "Women", "Jackets")
        assert len(result.cards) >= 3

    def test_breadcrumb_extraction(self, sample_interactables):
        html = f"<html><body>{BREADCRUMB_JSONLD}</body></html>"
        result = analyze_listing(
            raw_html=html,
            html_lower=html.lower(),
            interactables=[],
            metadata={},
            page_url="https://example.com/women/jackets",
            navigation_hints={},
        )
        assert result.breadcrumbs == ("Home", "Women", "Jackets")
        assert result.category == "Jackets"

    def test_filter_refs(self, sample_interactables):
        result = analyze_listing(
            raw_html="<html><body>content</body></html>",
            html_lower="<html><body>content</body></html>",
            interactables=sample_interactables,
            metadata={},
            page_url="https://example.com/category",
            navigation_hints={},
        )
        assert 8 in result.filter_refs

    def test_empty_listing(self):
        result = analyze_listing(
            raw_html="<html><body>empty page</body></html>",
            html_lower="<html><body>empty page</body></html>",
            interactables=[],
            metadata={},
            page_url="https://example.com/empty",
            navigation_hints={},
        )
        assert len(result.cards) == 0
        assert result.category is None

    def test_never_raises(self):
        result = analyze_listing(
            raw_html="",
            html_lower="",
            interactables=[],
            metadata={},
            page_url="",
            navigation_hints={},
        )
        assert result is not None
