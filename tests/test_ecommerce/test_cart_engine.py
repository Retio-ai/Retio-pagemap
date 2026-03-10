# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for cart action engine (Layer 1)."""

from __future__ import annotations

from pagemap.ecommerce import OptionGroup, ProductResult
from pagemap.ecommerce.cart_engine import analyze_cart_actions


class TestCartEngine:
    def test_add_to_cart_detected(self, sample_interactables):
        product = ProductResult()
        result = analyze_cart_actions(
            interactables=sample_interactables,
            html_lower="<body>product page</body>",
            product=product,
        )
        assert result.add_to_cart_ref == 2  # "Add to cart" button

    def test_buy_now_detected(self, sample_interactables):
        product = ProductResult()
        result = analyze_cart_actions(
            interactables=sample_interactables,
            html_lower="<body>product page</body>",
            product=product,
        )
        assert result.buy_now_ref == 3  # "Buy now" button

    def test_wishlist_detected(self, sample_interactables):
        product = ProductResult()
        result = analyze_cart_actions(
            interactables=sample_interactables,
            html_lower="<body>product page</body>",
            product=product,
        )
        assert result.wishlist_ref == 4  # "위시리스트" button

    def test_prerequisites_for_unselected_options(self, make_interactable):
        product = ProductResult(
            options=(
                OptionGroup(label="Size", type="size", values=("S", "M", "L"), selected=None),
                OptionGroup(label="Color", type="color", values=("Red", "Blue"), selected="Red"),
            ),
        )
        result = analyze_cart_actions(
            interactables=[make_interactable(ref=1, role="button", name="Add to cart")],
            html_lower="<body>product</body>",
            product=product,
        )
        assert len(result.prerequisites) == 1
        assert "Size" in result.prerequisites[0]

    def test_no_prerequisites_when_all_selected(self, make_interactable):
        product = ProductResult(
            options=(OptionGroup(label="Size", type="size", values=("S", "M", "L"), selected="M"),),
        )
        result = analyze_cart_actions(
            interactables=[make_interactable(ref=1, role="button", name="Add to cart")],
            html_lower="<body>product</body>",
            product=product,
        )
        assert len(result.prerequisites) == 0

    def test_korean_terms(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="button", name="장바구니 담기"),
            make_interactable(ref=2, role="button", name="바로구매"),
            make_interactable(ref=3, role="button", name="찜하기", affordance="toggle"),
        ]
        product = ProductResult()
        result = analyze_cart_actions(
            interactables=interactables,
            html_lower="<body>상품</body>",
            product=product,
        )
        assert result.add_to_cart_ref == 1
        assert result.buy_now_ref == 2
        assert result.wishlist_ref == 3

    def test_empty_on_no_actions(self):
        result = analyze_cart_actions(
            interactables=[],
            html_lower="<body>empty</body>",
            product=ProductResult(),
        )
        assert result.add_to_cart_ref is None
        assert result.buy_now_ref is None
        assert result.wishlist_ref is None

    def test_never_raises(self):
        result = analyze_cart_actions(
            interactables=[],
            html_lower="",
            product=ProductResult(),
        )
        assert result is not None
