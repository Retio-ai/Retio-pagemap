# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for S9 diagnostics tests."""

from __future__ import annotations

import pytest

from pagemap import Interactable


@pytest.fixture
def make_interactable():
    """Factory for creating test Interactable objects."""

    def _make(
        ref: int = 1,
        role: str = "button",
        name: str = "Click me",
        affordance: str = "click",
        region: str = "main",
        tier: int = 1,
        value: str = "",
        options: list[str] | None = None,
    ) -> Interactable:
        return Interactable(
            ref=ref,
            role=role,
            name=name,
            affordance=affordance,
            region=region,
            tier=tier,
            value=value,
            options=options or [],
        )

    return _make


@pytest.fixture
def blocked_html() -> str:
    """HTML content that looks like a bot-blocked page."""
    return """
    <html>
    <head><title>Access Denied</title></head>
    <body>
    <h1>Access Denied</h1>
    <p>Please verify you are a human to continue.</p>
    <div class="cf-turnstile"></div>
    </body>
    </html>
    """


@pytest.fixture
def product_html() -> str:
    """HTML content that looks like a product page."""
    return """
    <html>
    <head><title>Cool Product</title></head>
    <body>
    <h1>Cool Product</h1>
    <span class="price">$99.99</span>
    <p>In Stock</p>
    <button>Add to Cart</button>
    </body>
    </html>
    """


@pytest.fixture
def out_of_stock_html() -> str:
    """HTML content with out-of-stock product."""
    return """
    <html>
    <head><title>Cool Product</title></head>
    <body>
    <h1>Cool Product</h1>
    <span class="price">$99.99</span>
    <p>Sold Out</p>
    </body>
    </html>
    """


@pytest.fixture
def empty_results_html() -> str:
    """HTML content with empty search results."""
    return """
    <html>
    <head><title>Search Results</title></head>
    <body>
    <h1>Search Results</h1>
    <p>No results found for your search.</p>
    </body>
    </html>
    """


@pytest.fixture
def error_page_html() -> str:
    """HTML content of a 404 error page."""
    return """
    <html>
    <head><title>404 Not Found</title></head>
    <body>
    <h1>404 Not Found</h1>
    <p>The page you're looking for doesn't exist.</p>
    </body>
    </html>
    """
