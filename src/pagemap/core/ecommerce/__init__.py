# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Ecommerce 3-Layer Engine: Barrier → Core Task → Site Hints.

Layer 0 (Barrier): detect cookie consent, login walls, age verification
Layer 1 (Core Task): search/listing/product/cart analysis
Layer 2 (Site Hints): per-site fallback rules
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .. import Interactable

logger = logging.getLogger(__name__)

# ── Feature flag (cached at module load) ───────────────────────────
ECOMMERCE_ENABLED: bool = os.environ.get("ENABLE_ECOMMERCE", "1").lower() in (
    "1",
    "true",
    "yes",
)

# ── Layer 0: Barrier dataclasses ───────────────────────────────────


class BarrierType(StrEnum):
    COOKIE_CONSENT = "cookie_consent"
    LOGIN_REQUIRED = "login_required"
    AGE_VERIFICATION = "age_verification"
    REGION_RESTRICTED = "region_restricted"
    POPUP_OVERLAY = "popup_overlay"


@dataclass(frozen=True, slots=True)
class BarrierResult:
    """Result of barrier detection (Layer 0).

    Immutable — use ``with_matched_ref()`` to produce a copy with
    an updated ``accept_ref`` after budget filtering renumbers refs.
    """

    barrier_type: BarrierType
    provider: str  # "cookiebot", "onetrust", "generic", etc.
    auto_dismissible: bool  # cookie=True, login=False
    accept_ref: int | None  # interactable ref for dismiss button
    confidence: float  # 0.0–1.0
    signals: tuple[str, ...] = ()
    form_fields: tuple[dict[str, Any], ...] = ()  # LOGIN_REQUIRED: sanitized form info
    oauth_providers: tuple[str, ...] = ()  # "google", "kakao", etc.
    accept_terms: tuple[str, ...] = ()  # terms used to match accept button
    gate_type: str = ""  # "click_through" | "date_entry" (age gate only)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for metadata storage."""
        d = dataclasses.asdict(self)
        d["barrier_type"] = self.barrier_type.value
        return d

    def warning_message(self) -> str:
        """Human-readable warning for agent prompt."""
        if self.barrier_type == BarrierType.COOKIE_CONSENT:
            if self.accept_ref is not None:
                return f"Cookie consent ({self.provider}) — dismiss with [{self.accept_ref}]"
            return f"Cookie consent ({self.provider}) detected"
        if self.barrier_type == BarrierType.LOGIN_REQUIRED:
            return "Login required — page content may be restricted"
        if self.barrier_type == BarrierType.AGE_VERIFICATION:
            return "Age verification required"
        if self.barrier_type == BarrierType.REGION_RESTRICTED:
            return "Region-restricted content"
        return f"Popup overlay detected ({self.provider})"

    def with_matched_ref(self, interactables: list[Interactable]) -> BarrierResult:
        """Return a copy with accept_ref matched against final interactables.

        After budget filtering renumbers refs, the original accept_ref
        may be stale.  This re-scans final interactables for the accept
        button using ``accept_terms``.
        """
        if not self.accept_terms or not interactables:
            return self

        for item in interactables:
            name_lower = item.name.lower()
            for term in self.accept_terms:
                if term in name_lower:
                    return dataclasses.replace(self, accept_ref=item.ref)

        # No match in final interactables — clear the ref
        return dataclasses.replace(self, accept_ref=None)


# ── Layer 1: Engine output dataclasses ─────────────────────────────


@dataclass(frozen=True, slots=True)
class ProductCard:
    """A product card from search results or listing pages."""

    name: str = ""
    price: float | None = None
    currency: str | None = None
    price_text: str | None = None
    original_price: float | None = None
    brand: str | None = None
    image_url: str | None = None
    url: str | None = None
    position: int | None = None
    ref: int | None = None
    is_sponsored: bool = False


@dataclass(frozen=True, slots=True)
class OptionGroup:
    """A product option group (size, color, etc.)."""

    label: str = ""
    type: str = "other"  # "size" | "color" | "other"
    values: tuple[str, ...] = ()
    ref: int | None = None
    selected: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Layer 1 output for search_results pages."""

    cards: tuple[ProductCard, ...] = ()
    query: str | None = None
    total_results: str | None = None
    sort_ref: int | None = None
    sort_options: tuple[str, ...] = ()
    filter_refs: tuple[int, ...] = ()
    next_ref: int | None = None
    prev_ref: int | None = None
    load_more_ref: int | None = None
    current_page: int | None = None
    total_pages: int | None = None


@dataclass(frozen=True, slots=True)
class ListingResult:
    """Layer 1 output for listing pages."""

    cards: tuple[ProductCard, ...] = ()
    category: str | None = None
    breadcrumbs: tuple[str, ...] = ()
    total_products: str | None = None
    filter_refs: tuple[int, ...] = ()
    next_ref: int | None = None
    prev_ref: int | None = None
    load_more_ref: int | None = None


@dataclass(frozen=True, slots=True)
class ProductResult:
    """Layer 1 output for product_detail pages."""

    name: str | None = None
    price: float | None = None
    currency: str | None = None
    original_price: float | None = None
    discount_pct: int | None = None
    brand: str | None = None
    rating: float | None = None
    review_count: int | None = None
    availability: str | None = None  # "in_stock" | "out_of_stock" | "limited" | "pre_order"
    shipping: str | None = None
    options: tuple[OptionGroup, ...] = ()
    image_count: int = 0
    gallery_images: tuple[str, ...] = ()
    selected_variant: dict[str, str] | None = None
    review_snippets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CartAction:
    """Layer 1 output for cart action mapping on product pages."""

    add_to_cart_ref: int | None = None
    buy_now_ref: int | None = None
    wishlist_ref: int | None = None
    quantity_ref: int | None = None
    option_refs: tuple[OptionGroup, ...] = ()
    prerequisites: tuple[str, ...] = ()
    flow_state: str = "unknown"  # "select_options"|"ready_to_add"|"sold_out"|"unknown"
    cart_count: int | None = None
    confirmation_visible: bool = False
    blocked_reason: str | None = None  # "size_required"|"out_of_stock"|"options_required"
    available_option_count: int | None = None
    unavailable_option_count: int | None = None


# ── Router ─────────────────────────────────────────────────────────


def run_ecommerce_engine(
    *,
    page_type: str,
    raw_html: str,
    html_lower: str,
    interactables: list[Interactable],
    metadata: dict[str, Any],
    page_url: str,
    navigation_hints: dict[str, Any],
) -> dict[str, Any] | None:
    """Route to the appropriate Layer 1 engine based on page_type.

    Never raises — returns None on failure.

    Returns:
        dict serialization of engine result, or None.
    """
    try:
        result: Any = None

        if page_type == "search_results":
            from .search_engine import analyze_search_results

            result = analyze_search_results(
                raw_html=raw_html,
                html_lower=html_lower,
                interactables=interactables,
                metadata=metadata,
                page_url=page_url,
                navigation_hints=navigation_hints,
            )

        elif page_type == "listing":
            from .listing_engine import analyze_listing

            result = analyze_listing(
                raw_html=raw_html,
                html_lower=html_lower,
                interactables=interactables,
                metadata=metadata,
                page_url=page_url,
                navigation_hints=navigation_hints,
            )

        elif page_type == "product_detail":
            from .cart_engine import analyze_cart_actions
            from .product_engine import analyze_product

            product = analyze_product(
                raw_html=raw_html,
                html_lower=html_lower,
                interactables=interactables,
                metadata=metadata,
                page_url=page_url,
            )
            cart = analyze_cart_actions(
                interactables=interactables,
                html_lower=html_lower,
                product=product,
                raw_html=raw_html,
            )
            combined = dataclasses.asdict(product)
            combined["cart"] = dataclasses.asdict(cart)
            result = combined

        else:
            return None

        # Apply site hints (Layer 2)
        if result is not None:
            from .site_hints import apply_site_hints

            result, _applied = apply_site_hints(
                url=page_url,
                ecom_data=result if isinstance(result, dict) else dataclasses.asdict(result),
                raw_html=raw_html,
                html_lower=html_lower,
                page_type=page_type,
            )

        if result is None:
            return None

        # Emit telemetry
        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import ECOMMERCE_ENGINE_RUN

            emit(
                ECOMMERCE_ENGINE_RUN,
                {
                    "page_type": page_type,
                    "url": page_url,
                    "has_cards": bool(result.get("cards")),
                    "has_product": bool(result.get("name")),
                },
            )
        except Exception:  # nosec B110
            pass

        return result

    except Exception as e:
        logger.warning("Ecommerce engine failed for %s: %s", page_type, e)
        return None
