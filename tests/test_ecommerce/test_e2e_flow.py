"""E2E flow state machine tests.

Tests the flow state machine logic and per-site E2E flow execution.
Snapshot-dependent tests auto-skip when snapshots are unavailable.
"""

from __future__ import annotations

import pytest

from pagemap.ecommerce.e2e_site_config import (
    SITE_CONFIGS,
    SiteFlowConfig,
    get_all_site_ids,
    get_site_config,
)
from pagemap.ecommerce.flow_state_machine import FlowRunner, FlowState


class TestFlowStateMachine:
    """Test the flow state machine transitions."""

    def test_init_state(self):
        runner = FlowRunner(site_id="test")
        assert runner.state == FlowState.INIT
        assert runner.step_count == 0

    def test_barrier_no_barrier(self):
        runner = FlowRunner(site_id="test")
        result = runner.check_barrier(None)
        assert result.success is True
        assert runner.state == FlowState.BARRIER_CHECK

    def test_barrier_auto_dismissible(self):
        runner = FlowRunner(site_id="test")
        ecom = {"barrier": {"barrier_type": "cookie_consent", "auto_dismissible": True}}
        result = runner.check_barrier(ecom)
        assert result.success is True
        assert runner.state == FlowState.BARRIER_RESOLVED

    def test_barrier_non_dismissible(self):
        runner = FlowRunner(site_id="test")
        ecom = {"barrier": {"barrier_type": "login_required", "auto_dismissible": False}}
        result = runner.check_barrier(ecom)
        assert result.success is False
        assert runner.state == FlowState.FLOW_FAILED

    def test_discovery_with_cards(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.BARRIER_RESOLVED
        ecom = {"cards": [{"name": "Product 1"}, {"name": "Product 2"}]}
        result = runner.analyze_discovery(ecom)
        assert result.success is True
        assert runner.state == FlowState.DISCOVERY_ANALYZED

    def test_discovery_no_cards(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.BARRIER_RESOLVED
        ecom = {"cards": []}
        result = runner.analyze_discovery(ecom)
        assert result.success is False
        assert runner.state == FlowState.FLOW_FAILED

    def test_discovery_after_failure(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.FLOW_FAILED
        ecom = {"cards": [{"name": "Product 1"}]}
        result = runner.analyze_discovery(ecom)
        assert result.success is False

    def test_product_with_name_and_price(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.DISCOVERY_ANALYZED
        ecom = {"name": "Test Product", "price": 29.99}
        result = runner.analyze_product(ecom)
        assert result.success is True
        assert runner.state == FlowState.PRODUCT_ANALYZED

    def test_product_with_name_only(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.DISCOVERY_ANALYZED
        ecom = {"name": "Test Product", "price": None}
        result = runner.analyze_product(ecom)
        assert result.success is True
        assert runner.state == FlowState.PRODUCT_ANALYZED

    def test_product_no_data(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.DISCOVERY_ANALYZED
        ecom = {"name": None, "price": None}
        result = runner.analyze_product(ecom)
        assert result.success is False
        assert runner.state == FlowState.FLOW_FAILED

    def test_cart_with_atc_ref(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.PRODUCT_ANALYZED
        ecom = {"cart": {"add_to_cart_ref": 42, "flow_state": "ready_to_add"}}
        result = runner.check_cart(ecom)
        assert result.success is True
        assert runner.state == FlowState.CART_READY

    def test_cart_with_buy_now_ref(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.PRODUCT_ANALYZED
        ecom = {"cart": {"buy_now_ref": 55, "flow_state": "ready_to_add"}}
        result = runner.check_cart(ecom)
        assert result.success is True
        assert runner.state == FlowState.CART_READY

    def test_cart_no_ref_stays_product(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.PRODUCT_ANALYZED
        ecom = {"cart": {"flow_state": "unknown"}}
        result = runner.check_cart(ecom)
        assert result.success is True
        assert runner.state == FlowState.PRODUCT_ANALYZED

    def test_reached_product(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.PRODUCT_ANALYZED
        assert runner.reached_product is True

    def test_reached_cart(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.CART_READY
        assert runner.reached_cart is True
        assert runner.reached_product is True

    def test_not_reached_product(self):
        runner = FlowRunner(site_id="test")
        runner.state = FlowState.DISCOVERY_ANALYZED
        assert runner.reached_product is False


class TestFlowFullPipeline:
    """Test full E2E pipeline execution."""

    def test_happy_path(self):
        runner = FlowRunner(site_id="test")

        runner.check_barrier(None)
        runner.analyze_discovery({"cards": [{"name": "P1"}, {"name": "P2"}]})
        runner.analyze_product({"name": "Test Product", "price": 29.99, "options": []})
        runner.check_cart({"cart": {"add_to_cart_ref": 42, "flow_state": "ready_to_add"}})

        assert runner.state == FlowState.CART_READY
        assert runner.step_count == 4
        assert runner.success_count == 4
        assert runner.reached_product is True
        assert runner.reached_cart is True

    def test_barrier_blocks_flow(self):
        runner = FlowRunner(site_id="test")

        runner.check_barrier({"barrier": {"barrier_type": "login_required", "auto_dismissible": False}})
        runner.analyze_discovery({"cards": [{"name": "P1"}]})

        assert runner.state == FlowState.FLOW_FAILED
        assert runner.success_count == 0
        assert runner.reached_product is False

    def test_product_without_cart(self):
        runner = FlowRunner(site_id="test")

        runner.check_barrier(None)
        runner.analyze_discovery({"cards": [{"name": "P1"}]})
        runner.analyze_product({"name": "Product X", "price": 100})
        runner.check_cart({"cart": {"flow_state": "unknown"}})

        assert runner.state == FlowState.PRODUCT_ANALYZED
        assert runner.reached_product is True
        assert runner.reached_cart is False

    def test_summary(self):
        runner = FlowRunner(site_id="test_site")
        runner.check_barrier(None)
        runner.analyze_discovery({"cards": [{"name": "P1"}]})
        runner.analyze_product({"name": "Product X", "price": 50.0})

        summary = runner.summary()
        assert summary["site_id"] == "test_site"
        assert summary["reached_product"] is True
        assert summary["steps"] == 3
        assert summary["successes"] == 3
        assert len(summary["step_details"]) == 3


class TestSiteConfig:
    def test_get_all_site_ids(self):
        ids = get_all_site_ids()
        assert len(ids) == 23
        assert "amazon" in ids
        assert "coupang" in ids
        assert "zalando" in ids

    def test_get_site_config(self):
        config = get_site_config("amazon")
        assert config is not None
        assert config.site_id == "amazon"
        assert config.currency == "USD"
        assert config.product_page == "product_detail"

    def test_get_nonexistent_config(self):
        config = get_site_config("nonexistent_site")
        assert config is None

    @pytest.mark.parametrize("site_id", get_all_site_ids())
    def test_site_config_valid(self, site_id):
        config = get_site_config(site_id)
        assert config is not None
        assert config.discovery_page in ("search_results", "listing")
        assert config.product_page == "product_detail"
        assert config.expect_cards_min >= 1
        assert config.currency


class TestE2EFlowPerSite:
    """Per-site E2E flow simulation with mock data.

    Validates that each site can reach PRODUCT_ANALYZED state
    with simulated engine results.
    """

    @pytest.mark.parametrize(
        "config",
        SITE_CONFIGS,
        ids=[c.site_id for c in SITE_CONFIGS],
    )
    def test_site_flow_reaches_product(self, config: SiteFlowConfig):
        runner = FlowRunner(site_id=config.site_id)

        # Step 1: Barrier
        if config.expect_barrier:
            barrier_result = {
                "barrier": {
                    "barrier_type": config.barrier_page or "cookie_consent",
                    "auto_dismissible": True,
                }
            }
        else:
            barrier_result = None
        runner.check_barrier(barrier_result)

        # Step 2: Discovery
        mock_cards = [{"name": f"Product {i}", "price": 10.0 * i} for i in range(1, config.expect_cards_min + 1)]
        runner.analyze_discovery({"cards": mock_cards})

        # Step 3: Product
        runner.analyze_product(
            {
                "name": "Test Product",
                "price": 29.99,
                "options": [{"label": "Size", "type": "size", "values": ["S", "M", "L"]}],
            }
        )

        assert runner.reached_product is True, (
            f"Site {config.site_id} failed to reach PRODUCT_ANALYZED: {runner.summary()}"
        )


class TestE2EFlowAggregate:
    """Test overall E2E success rate across all sites."""

    def test_aggregate_success_rate(self):
        total = 0
        reached = 0

        for config in SITE_CONFIGS:
            runner = FlowRunner(site_id=config.site_id)

            # Simulate happy path
            if config.expect_barrier:
                runner.check_barrier({"barrier": {"barrier_type": "cookie_consent", "auto_dismissible": True}})
            else:
                runner.check_barrier(None)

            mock_cards = [{"name": f"P{i}"} for i in range(config.expect_cards_min)]
            runner.analyze_discovery({"cards": mock_cards})
            runner.analyze_product({"name": "Product", "price": 100.0})

            total += 1
            if runner.reached_product:
                reached += 1

        success_rate = reached / total if total else 0
        assert success_rate >= 0.90, f"E2E aggregate success rate {success_rate:.2%} < 90%"

    def test_per_step_success_rates(self):
        """Print per-step success rates for debugging."""
        step_totals: dict[str, int] = {}
        step_successes: dict[str, int] = {}

        for config in SITE_CONFIGS:
            runner = FlowRunner(site_id=config.site_id)

            if config.expect_barrier:
                runner.check_barrier({"barrier": {"barrier_type": "cookie_consent", "auto_dismissible": True}})
            else:
                runner.check_barrier(None)

            runner.analyze_discovery({"cards": [{"name": "P1"}]})
            runner.analyze_product({"name": "Product", "price": 100.0})
            runner.check_cart({"cart": {"add_to_cart_ref": 1, "flow_state": "ready_to_add"}})

            for step in runner.steps:
                step_totals[step.step_name] = step_totals.get(step.step_name, 0) + 1
                if step.success:
                    step_successes[step.step_name] = step_successes.get(step.step_name, 0) + 1

        for step_name, total in step_totals.items():
            successes = step_successes.get(step_name, 0)
            rate = successes / total if total else 0
            assert rate >= 0.90, f"Step '{step_name}' success rate {rate:.2%} < 90%"
