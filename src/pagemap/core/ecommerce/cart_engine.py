# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Cart Action Engine — Layer 1 action mapping for product pages.

Maps interactables to cart actions: add-to-cart, buy-now, wishlist, quantity.
Identifies prerequisites (unselected options that must be chosen first).
Never raises — returns empty CartAction on failure.
"""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Interactable

from ..i18n import ADD_TO_CART_TERMS, BUY_NOW_TERMS, CART_CONFIRMATION_TERMS, QUANTITY_TERMS, WISHLIST_TERMS
from . import CartAction, ProductResult

logger = logging.getLogger(__name__)

# Pre-compute lowered terms
_ATC_TERMS_LOWER = tuple(t.lower() for t in ADD_TO_CART_TERMS)
_BUY_TERMS_LOWER = tuple(t.lower() for t in BUY_NOW_TERMS)
_WISHLIST_TERMS_LOWER = tuple(t.lower() for t in WISHLIST_TERMS)
_QTY_TERMS_LOWER = tuple(t.lower() for t in QUANTITY_TERMS)
_CONFIRM_LOWER = tuple(t.lower() for t in CART_CONFIRMATION_TERMS)

# Cart count: badge/count in class or aria
_CART_COUNT_RE = re.compile(
    r'(?:class|aria-label)=["\'][^"\']*(?:cart[-_]?count|cart[-_]?badge|item[-_]?count|basket[-_]?count)[^"\']*["\'][^>]*>'
    r"\s*(\d+)\s*<",
    re.IGNORECASE,
)


def _match_action_ref(
    interactables: list[Interactable],
    terms: tuple[str, ...],
    affordances: tuple[str, ...] = ("click",),
) -> int | None:
    """Find first interactable matching any of the terms."""
    for item in interactables:
        if item.affordance not in affordances:
            continue
        name_lower = item.name.lower()
        for term in terms:
            if term in name_lower:
                return item.ref
    return None


def _find_quantity_ref(interactables: list[Interactable]) -> int | None:
    """Find quantity input/spinner control."""
    for item in interactables:
        if item.affordance not in ("type", "select"):
            continue
        name_lower = item.name.lower()
        for term in _QTY_TERMS_LOWER:
            if term in name_lower:
                return item.ref
    return None


def _identify_prerequisites(product: ProductResult) -> tuple[str, ...]:
    """Identify options that must be selected before add-to-cart.

    Options without a ``selected`` value are prerequisites.
    """
    prereqs: list[str] = []
    for opt in product.options:
        if opt.values and not opt.selected:
            prereqs.append(f"Select {opt.label}" if opt.label else f"Select {opt.type}")
    return tuple(prereqs)


def _detect_flow_state(
    prerequisites: tuple[str, ...],
    add_to_cart_ref: int | None,
) -> str:
    """Determine cart flow state: select_options | ready_to_add | unknown."""
    if prerequisites:
        return "select_options"
    if add_to_cart_ref is not None:
        return "ready_to_add"
    return "unknown"


def _detect_confirmation(html_lower: str) -> bool:
    """Detect cart confirmation message in HTML."""
    return any(term in html_lower for term in _CONFIRM_LOWER)


def _extract_cart_count(html_lower: str) -> int | None:
    """Extract cart item count from badge/count element."""
    try:
        m = _CART_COUNT_RE.search(html_lower)
        if m:
            with suppress(ValueError):
                return int(m.group(1))
        return None
    except Exception:
        return None


def analyze_cart_actions(
    *,
    interactables: list[Interactable],
    html_lower: str,
    product: ProductResult,
    raw_html: str = "",
) -> CartAction:
    """Map interactables to cart action refs. Never raises.

    When raw_html is provided, option availability analysis is also performed.
    """
    try:
        add_to_cart_ref = _match_action_ref(interactables, _ATC_TERMS_LOWER)
        buy_now_ref = _match_action_ref(interactables, _BUY_TERMS_LOWER)
        wishlist_ref = _match_action_ref(interactables, _WISHLIST_TERMS_LOWER, affordances=("click", "toggle"))
        quantity_ref = _find_quantity_ref(interactables)
        prerequisites = _identify_prerequisites(product)
        flow_state = _detect_flow_state(prerequisites, add_to_cart_ref)
        confirmation_visible = _detect_confirmation(html_lower)
        cart_count = _extract_cart_count(html_lower)

        # Option availability analysis (when raw_html is available)
        blocked_reason = None
        available_count = None
        unavailable_count = None

        if raw_html and product.options:
            from .option_analyzer import (
                analyze_option_availability,
                compute_blocked_reason,
                get_availability_counts,
            )

            rich_options = analyze_option_availability(
                product.options,
                raw_html,
                html_lower,
                interactables,
            )
            blocked_reason = compute_blocked_reason(
                rich_options,
                add_to_cart_ref,
                product.availability,
            )
            available_count, unavailable_count = get_availability_counts(rich_options)

            # Update flow_state based on availability
            if blocked_reason in ("out_of_stock", "all_sold_out"):
                flow_state = "sold_out"

        return CartAction(
            add_to_cart_ref=add_to_cart_ref,
            buy_now_ref=buy_now_ref,
            wishlist_ref=wishlist_ref,
            quantity_ref=quantity_ref,
            option_refs=product.options,
            prerequisites=prerequisites,
            flow_state=flow_state,
            cart_count=cart_count,
            confirmation_visible=confirmation_visible,
            blocked_reason=blocked_reason,
            available_option_count=available_count,
            unavailable_option_count=unavailable_count,
        )

    except Exception as e:
        logger.debug("Cart engine error: %s", e)
        return CartAction()
