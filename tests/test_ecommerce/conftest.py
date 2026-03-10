# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for ecommerce engine tests."""

from __future__ import annotations

import pytest

from pagemap import Interactable


@pytest.fixture
def make_interactable():
    """Factory for creating test Interactable instances."""

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
def sample_interactables(make_interactable):
    """A representative set of ecommerce interactables."""
    return [
        make_interactable(ref=1, role="searchbox", name="Search products", affordance="type"),
        make_interactable(ref=2, role="button", name="Add to cart", affordance="click"),
        make_interactable(ref=3, role="button", name="Buy now", affordance="click"),
        make_interactable(ref=4, role="button", name="위시리스트", affordance="click"),
        make_interactable(
            ref=5, role="combobox", name="사이즈 선택", affordance="select", options=["S", "M", "L", "XL"]
        ),
        make_interactable(
            ref=6, role="combobox", name="Sort by", affordance="select", options=["Price low", "Price high", "Newest"]
        ),
        make_interactable(ref=7, role="button", name="Accept all cookies", affordance="click"),
        make_interactable(ref=8, role="link", name="필터", affordance="click"),
    ]


PRODUCT_JSONLD = """
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "오버핏 레더 자켓",
  "brand": {"@type": "Brand", "name": "TestBrand"},
  "offers": {
    "@type": "Offer",
    "price": "189000",
    "priceCurrency": "KRW",
    "availability": "https://schema.org/InStock"
  },
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "4.6",
    "reviewCount": "847"
  }
}
</script>
"""

ITEMLIST_JSONLD = """
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "ItemList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "item": {
        "@type": "Product",
        "name": "Product A",
        "offers": {"@type": "Offer", "price": "29900", "priceCurrency": "KRW"}
      }
    },
    {
      "@type": "ListItem",
      "position": 2,
      "item": {
        "@type": "Product",
        "name": "Product B",
        "offers": {"@type": "Offer", "price": "39900", "priceCurrency": "KRW"}
      }
    },
    {
      "@type": "ListItem",
      "position": 3,
      "item": {
        "@type": "Product",
        "name": "Product C",
        "offers": {"@type": "Offer", "price": "49900", "priceCurrency": "KRW"}
      }
    }
  ]
}
</script>
"""

BREADCRUMB_JSONLD = """
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Home"},
    {"@type": "ListItem", "position": 2, "name": "Women"},
    {"@type": "ListItem", "position": 3, "name": "Jackets"}
  ]
}
</script>
"""

COOKIEBOT_HTML = """
<div id="CybotCookiebotDialog" class="cookie-consent">
  <p>We use cookies to improve your experience.</p>
  <button id="CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll">Accept all cookies</button>
</div>
"""

ONETRUST_HTML = """
<div id="onetrust-banner-sdk" class="onetrust-pc-dark-filter">
  <p>This website uses cookies.</p>
  <button id="onetrust-accept-btn-handler">Accept All</button>
</div>
"""

LOGIN_FORM_HTML = """
<div class="login-modal" id="login-dialog">
  <form action="/login" method="post" class="login-form">
    <input type="email" name="email" placeholder="이메일" required>
    <input type="password" name="password" placeholder="비밀번호" required>
    <button type="submit">로그인</button>
    <div class="social-login">
      <a href="https://accounts.google.com/o/oauth2/auth">Google로 로그인</a>
      <a href="https://kauth.kakao.com/oauth/authorize">카카오로 로그인</a>
    </div>
  </form>
</div>
"""
