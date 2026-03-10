# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E Flow State Machine for ecommerce pipeline validation.

State transitions:
  INIT → BARRIER_CHECK → BARRIER_RESOLVED → SEARCH/LISTING_ANALYZED
      → PRODUCT_ANALYZED → CART_READY → CART_ADDED
      (failure at any step → FLOW_FAILED)

Used for E2E integration testing — validates that the full ecommerce
pipeline works end-to-end for each supported site.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class FlowState(StrEnum):
    INIT = "init"
    BARRIER_CHECK = "barrier_check"
    BARRIER_RESOLVED = "barrier_resolved"
    DISCOVERY_ANALYZED = "discovery_analyzed"
    PRODUCT_ANALYZED = "product_analyzed"
    CART_READY = "cart_ready"
    CART_ADDED = "cart_added"
    FLOW_FAILED = "flow_failed"


@dataclass(frozen=True, slots=True)
class FlowStepResult:
    """Result of a single state transition step."""

    from_state: str
    to_state: str
    step_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class FlowRunner:
    """Runs an E2E flow for a site, accumulating step results.

    This is a test utility — it validates that the ecommerce engine
    can correctly process each stage of a site's purchase flow.
    """

    site_id: str
    state: FlowState = FlowState.INIT
    steps: list[FlowStepResult] = field(default_factory=list)

    def _transition(
        self,
        step_name: str,
        to_state: FlowState,
        success: bool,
        data: dict[str, Any] | None = None,
        error: str = "",
    ) -> FlowStepResult:
        result = FlowStepResult(
            from_state=self.state.value,
            to_state=to_state.value if success else FlowState.FLOW_FAILED.value,
            step_name=step_name,
            success=success,
            data=data or {},
            error=error,
        )
        self.steps.append(result)
        self.state = to_state if success else FlowState.FLOW_FAILED
        return result

    def check_barrier(self, ecom_result: dict[str, Any] | None) -> FlowStepResult:
        """Step 1: Check for barriers (cookie consent, age gate, etc.)."""
        if ecom_result is None:
            return self._transition("barrier_check", FlowState.BARRIER_CHECK, True, {"barrier": None})

        barrier = ecom_result.get("barrier")
        if not barrier:
            return self._transition("barrier_check", FlowState.BARRIER_RESOLVED, True, {"barrier": None})

        auto_dismissible = barrier.get("auto_dismissible", False)
        if auto_dismissible:
            return self._transition(
                "barrier_check",
                FlowState.BARRIER_RESOLVED,
                True,
                {"barrier_type": barrier.get("barrier_type"), "auto_dismiss": True},
            )

        return self._transition(
            "barrier_check",
            FlowState.FLOW_FAILED,
            False,
            {"barrier_type": barrier.get("barrier_type")},
            error=f"Non-dismissible barrier: {barrier.get('barrier_type')}",
        )

    def analyze_discovery(self, ecom_result: dict[str, Any] | None) -> FlowStepResult:
        """Step 2: Analyze search results or listing page."""
        if self.state == FlowState.FLOW_FAILED:
            return self._transition("discovery", FlowState.FLOW_FAILED, False, error="Previous step failed")

        if ecom_result is None:
            return self._transition("discovery", FlowState.FLOW_FAILED, False, error="No ecom result")

        cards = ecom_result.get("cards", [])
        card_count = len(cards) if isinstance(cards, list) else 0

        if card_count > 0:
            return self._transition(
                "discovery",
                FlowState.DISCOVERY_ANALYZED,
                True,
                {"card_count": card_count},
            )

        return self._transition(
            "discovery",
            FlowState.FLOW_FAILED,
            False,
            {"card_count": 0},
            error="No cards extracted from discovery page",
        )

    def analyze_product(self, ecom_result: dict[str, Any] | None) -> FlowStepResult:
        """Step 3: Analyze product detail page."""
        if self.state == FlowState.FLOW_FAILED:
            return self._transition("product", FlowState.FLOW_FAILED, False, error="Previous step failed")

        if ecom_result is None:
            return self._transition("product", FlowState.FLOW_FAILED, False, error="No ecom result")

        name = ecom_result.get("name")
        price = ecom_result.get("price")

        if name is not None or price is not None:
            return self._transition(
                "product",
                FlowState.PRODUCT_ANALYZED,
                True,
                {"name": name, "price": price, "has_options": bool(ecom_result.get("options"))},
            )

        return self._transition(
            "product",
            FlowState.FLOW_FAILED,
            False,
            error="No product name or price extracted",
        )

    def check_cart(self, ecom_result: dict[str, Any] | None) -> FlowStepResult:
        """Step 4: Check cart action availability."""
        if self.state == FlowState.FLOW_FAILED:
            return self._transition("cart", FlowState.FLOW_FAILED, False, error="Previous step failed")

        if ecom_result is None:
            return self._transition("cart", FlowState.FLOW_FAILED, False, error="No ecom result")

        cart = ecom_result.get("cart", {})
        atc_ref = cart.get("add_to_cart_ref")
        buy_now_ref = cart.get("buy_now_ref")
        flow_state = cart.get("flow_state", "unknown")

        if atc_ref or buy_now_ref:
            return self._transition(
                "cart",
                FlowState.CART_READY,
                True,
                {"atc_ref": atc_ref, "buy_now_ref": buy_now_ref, "flow_state": flow_state},
            )

        # Product analyzed is still a success — cart is bonus
        return self._transition(
            "cart",
            FlowState.PRODUCT_ANALYZED,
            True,
            {"flow_state": flow_state},
        )

    @property
    def reached_product(self) -> bool:
        """Check if flow reached at least PRODUCT_ANALYZED."""
        return self.state in (
            FlowState.PRODUCT_ANALYZED,
            FlowState.CART_READY,
            FlowState.CART_ADDED,
        )

    @property
    def reached_cart(self) -> bool:
        """Check if flow reached CART_READY or CART_ADDED."""
        return self.state in (FlowState.CART_READY, FlowState.CART_ADDED)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.steps if s.success)

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of the flow execution."""
        return {
            "site_id": self.site_id,
            "final_state": self.state.value,
            "steps": self.step_count,
            "successes": self.success_count,
            "reached_product": self.reached_product,
            "reached_cart": self.reached_cart,
            "step_details": [{"step": s.step_name, "success": s.success, "error": s.error} for s in self.steps],
        }
