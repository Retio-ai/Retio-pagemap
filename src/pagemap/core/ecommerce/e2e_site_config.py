# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E site configurations for ecommerce flow testing.

Each site config defines the expected flow stages and snapshot
page types for E2E validation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SiteFlowConfig:
    """Configuration for a single site's E2E flow test."""

    site_id: str
    barrier_page: str | None  # snapshot page_type that has barrier
    discovery_page: str  # "search_results" or "listing"
    product_page: str  # "product_detail"
    expect_barrier: bool = False
    expect_cards_min: int = 1
    locale: str = "en"
    currency: str = "USD"


# 23 ecommerce site configs for E2E testing
SITE_CONFIGS: tuple[SiteFlowConfig, ...] = (
    # Korean sites
    SiteFlowConfig(
        site_id="coupang",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        locale="ko",
        currency="KRW",
    ),
    SiteFlowConfig(
        site_id="musinsa",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        locale="ko",
        currency="KRW",
    ),
    SiteFlowConfig(
        site_id="29cm",
        barrier_page=None,
        discovery_page="listing",
        product_page="product_detail",
        expect_cards_min=2,
        locale="ko",
        currency="KRW",
    ),
    SiteFlowConfig(
        site_id="ssg",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        locale="ko",
        currency="KRW",
    ),
    # US sites
    SiteFlowConfig(
        site_id="amazon",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=5,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="walmart",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="ebay",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="nordstrom",
        barrier_page=None,
        discovery_page="listing",
        product_page="product_detail",
        expect_cards_min=3,
        currency="USD",
    ),
    # UK sites
    SiteFlowConfig(
        site_id="asos",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        currency="GBP",
    ),
    # European sites
    SiteFlowConfig(
        site_id="zalando",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        locale="de",
        currency="EUR",
    ),
    SiteFlowConfig(
        site_id="farfetch",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        currency="USD",
    ),
    # Japanese sites
    SiteFlowConfig(
        site_id="rakuten",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        locale="ja",
        currency="JPY",
    ),
    # Chinese sites
    SiteFlowConfig(
        site_id="taobao",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        locale="zh",
        currency="CNY",
    ),
    # Sports/Fashion global
    SiteFlowConfig(
        site_id="adidas",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="nike",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="zara",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=2,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="uniqlo",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="hm",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=3,
        currency="USD",
    ),
    SiteFlowConfig(
        site_id="shein",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        currency="USD",
    ),
    # Indian sites
    SiteFlowConfig(
        site_id="flipkart",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        currency="INR",
    ),
    # AliExpress
    SiteFlowConfig(
        site_id="aliexpress",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        currency="USD",
    ),
    # Naver Shopping
    SiteFlowConfig(
        site_id="naver_shopping",
        barrier_page=None,
        discovery_page="search_results",
        product_page="product_detail",
        expect_cards_min=3,
        locale="ko",
        currency="KRW",
    ),
    # COS
    SiteFlowConfig(
        site_id="cos",
        barrier_page="cookie_consent",
        discovery_page="listing",
        product_page="product_detail",
        expect_barrier=True,
        expect_cards_min=2,
        currency="USD",
    ),
)

SITE_CONFIG_MAP: dict[str, SiteFlowConfig] = {c.site_id: c for c in SITE_CONFIGS}


def get_site_config(site_id: str) -> SiteFlowConfig | None:
    """Get site config by ID. Returns None if not found."""
    return SITE_CONFIG_MAP.get(site_id)


def get_all_site_ids() -> tuple[str, ...]:
    """Return all configured site IDs."""
    return tuple(c.site_id for c in SITE_CONFIGS)
