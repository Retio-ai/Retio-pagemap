# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for AutoThresholdController — EMA-based pruning budget adjustment."""

from __future__ import annotations

import threading

import pytest

from pagemap.diagnostics.auto_threshold import (
    _EMA_ALPHA,
    _MAX_BUDGET_MULTIPLIER,
    _MIN_BUDGET,
    _MIN_SAMPLES_FOR_ACTION,
    _TIGHTEN_CONSECUTIVE_PAGES,
    _WINDOW_SAMPLES,
    _WINDOW_SECONDS,
    AutoThresholdController,
    ThresholdState,
)


@pytest.fixture
def controller():
    return AutoThresholdController()


class TestThresholdState:
    def test_effective_budget_no_adjustment(self):
        state = ThresholdState(domain="d", page_type="p", base_budget=1500)
        assert state.effective_budget == 1500

    def test_effective_budget_with_positive_adjustment(self):
        state = ThresholdState(domain="d", page_type="p", base_budget=1500, budget_adjustment=300)
        assert state.effective_budget == 1800

    def test_effective_budget_capped_at_max(self):
        state = ThresholdState(domain="d", page_type="p", base_budget=1500, budget_adjustment=99999)
        expected_max = int(1500 * _MAX_BUDGET_MULTIPLIER)
        assert state.effective_budget == expected_max

    def test_effective_budget_floored_at_min(self):
        state = ThresholdState(domain="d", page_type="p", base_budget=100, budget_adjustment=-9999)
        assert state.effective_budget == _MIN_BUDGET


class TestRecordConfidence:
    def test_first_sample_sets_ema(self, controller):
        controller.record_confidence("example.com", "product", 0.8, budget=1500, now=1000.0)
        state = controller.get_state("example.com", "product")
        assert state is not None
        assert state.ema == 0.8
        assert state.sample_count == 1

    def test_ema_updates_with_alpha(self, controller):
        controller.record_confidence("example.com", "product", 1.0, budget=1500, now=1000.0)
        controller.record_confidence("example.com", "product", 0.0, budget=1500, now=1001.0)
        state = controller.get_state("example.com", "product")
        expected = _EMA_ALPHA * 0.0 + (1 - _EMA_ALPHA) * 1.0  # 0.7
        assert state.ema == pytest.approx(expected, abs=0.01)

    def test_sample_count_increments(self, controller):
        for i in range(5):
            controller.record_confidence("d", "p", 0.5, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        assert state.sample_count == 5

    def test_different_domains_tracked_independently(self, controller):
        controller.record_confidence("a.com", "product", 0.8, budget=1500, now=1000.0)
        controller.record_confidence("b.com", "product", 0.3, budget=1500, now=1000.0)
        assert controller.get_state("a.com", "product").ema == 0.8
        assert controller.get_state("b.com", "product").ema == 0.3

    def test_different_page_types_tracked_independently(self, controller):
        controller.record_confidence("d", "product", 0.9, budget=1500, now=1000.0)
        controller.record_confidence("d", "search", 0.2, budget=1500, now=1000.0)
        assert controller.get_state("d", "product").ema == 0.9
        assert controller.get_state("d", "search").ema == 0.2


class TestConsecutiveHighTracking:
    def test_high_confidence_increments_consecutive(self, controller):
        for i in range(10):
            controller.record_confidence("d", "p", 0.9, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        assert state.consecutive_high == 10

    def test_low_confidence_resets_consecutive(self, controller):
        for i in range(5):
            controller.record_confidence("d", "p", 0.9, budget=1500, now=1000.0 + i)
        controller.record_confidence("d", "p", 0.3, budget=1500, now=1010.0)
        state = controller.get_state("d", "p")
        assert state.consecutive_high == 0


class TestBudgetLoosening:
    def test_low_ema_triggers_loosening(self, controller):
        """When EMA drops below threshold after enough samples, budget should increase."""
        for i in range(_WINDOW_SAMPLES):
            controller.record_confidence("d", "p", 0.2, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        assert state.budget_adjustment > 0

    def test_proportional_step_size(self, controller):
        """Step size should be proportional to budget: max(100, budget * 0.15)."""
        for i in range(_WINDOW_SAMPLES):
            controller.record_confidence("d", "p", 0.2, budget=2000, now=1000.0 + i)
        state = controller.get_state("d", "p")
        expected_step = max(100, int(2000 * 0.15))  # 300
        assert state.budget_adjustment == expected_step

    def test_adjustment_capped(self, controller):
        """Budget adjustment should not exceed max multiplier."""
        for i in range(_WINDOW_SAMPLES * 20):
            controller.record_confidence("d", "p", 0.1, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        max_adj = int(1500 * (_MAX_BUDGET_MULTIPLIER - 1))
        assert state.budget_adjustment <= max_adj


class TestBudgetTightening:
    def test_high_ema_with_consecutive_triggers_tightening(self, controller):
        """Slow tightening when EMA is high for many consecutive pages."""
        # Manually create a state with a loosened budget
        state = controller._get_or_create("d", "p", 1500)
        state.budget_adjustment = 500
        state.ema = 0.95
        state.sample_count = 100
        state.consecutive_high = _TIGHTEN_CONSECUTIVE_PAGES + 1

        # Trigger evaluation
        controller._evaluate_adjustment(state)

        # Budget should have decreased
        assert state.budget_adjustment < 500

    def test_no_tighten_without_consecutive(self, controller):
        """No tightening if consecutive high count is below threshold."""
        # Manually create a state with loosened budget but low consecutive count
        state = controller._get_or_create("d", "p", 1500)
        state.budget_adjustment = 500
        state.ema = 0.95
        state.sample_count = 100
        state.consecutive_high = 5  # well below threshold

        controller._evaluate_adjustment(state)

        # Should NOT tighten — not enough consecutive pages
        assert state.budget_adjustment == 500

    def test_no_tighten_when_no_adjustment(self, controller):
        """No tightening when budget_adjustment is already 0."""
        state = controller._get_or_create("d", "p", 1500)
        state.budget_adjustment = 0
        state.ema = 0.95
        state.sample_count = 100
        state.consecutive_high = _TIGHTEN_CONSECUTIVE_PAGES + 1

        controller._evaluate_adjustment(state)
        assert state.budget_adjustment == 0


class TestGetAdjustedBudget:
    def test_no_observations_returns_base(self, controller):
        assert controller.get_adjusted_budget("new.com", "default", base_budget=1500) == 1500

    def test_returns_effective_budget(self, controller):
        for i in range(_WINDOW_SAMPLES):
            controller.record_confidence("d", "p", 0.2, budget=1500, now=1000.0 + i)
        result = controller.get_adjusted_budget("d", "p", base_budget=1500)
        assert result > 1500


class TestMinSamplesGuard:
    def test_no_adjustment_with_few_samples(self, controller):
        """No adjustment until minimum samples reached."""
        for i in range(_MIN_SAMPLES_FOR_ACTION - 1):
            controller.record_confidence("d", "p", 0.1, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        assert state.budget_adjustment == 0


class TestWindowReset:
    def test_window_resets_on_sample_count(self, controller):
        for i in range(_WINDOW_SAMPLES + 5):
            controller.record_confidence("d", "p", 0.5, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        # After window reset, sample_count restarts from 0 + new samples after reset
        assert state.sample_count == 5

    def test_window_resets_on_time(self, controller):
        """Window should reset after 1 hour even with few samples.

        Note: window_start uses time.time() default, so injected now=4700
        doesn't actually trigger a time-based reset. This test verifies
        that samples are still counted correctly regardless.
        """
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1000.0)
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1001.0)
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1002.0)
        # Jump 1+ hour
        controller.record_confidence("d", "p", 0.2, budget=1500, now=4700.0)
        state = controller.get_state("d", "p")
        assert state.sample_count == 4

    def test_sample_count_resets_after_window(self, controller):
        """sample_count must reset to 0 when a window ends, so _MIN_SAMPLES_FOR_ACTION guard works."""
        for i in range(_WINDOW_SAMPLES):
            controller.record_confidence("d", "p", 0.5, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        assert state.sample_count == 0  # reset after window evaluation


class TestHealth:
    def test_health_empty(self, controller):
        h = controller.health()
        assert h["tracked_keys"] == 0

    def test_health_with_state(self, controller):
        controller.record_confidence("d", "p", 0.5, budget=1500, now=1000.0)
        h = controller.health()
        assert h["tracked_keys"] == 1
        assert "d:p" in h["states"]


class TestAllStates:
    def test_returns_copy(self, controller):
        controller.record_confidence("d", "p", 0.5, budget=1500, now=1000.0)
        states = controller.all_states()
        assert len(states) == 1
        assert "d:p" in states


class TestEMACalculation:
    def test_ema_convergence(self, controller):
        """EMA should converge to the constant signal value."""
        for i in range(100):
            controller.record_confidence("d", "p", 0.7, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        assert state.ema == pytest.approx(0.7, abs=0.01)

    def test_ema_responds_to_changes(self, controller):
        """EMA should respond to signal changes."""
        for i in range(20):
            controller.record_confidence("d", "p", 0.9, budget=1500, now=1000.0 + i)
        state = controller.get_state("d", "p")
        high_ema = state.ema

        for i in range(20):
            controller.record_confidence("d", "p", 0.1, budget=1500, now=2000.0 + i)
        state = controller.get_state("d", "p")
        assert state.ema < high_ema


class TestThreadSafety:
    def test_has_threading_lock(self):
        """Controller should have a threading.Lock."""
        controller = AutoThresholdController()
        assert isinstance(controller._lock, type(threading.Lock()))

    def test_concurrent_record_confidence(self):
        """10 threads recording concurrently should not corrupt state."""
        from concurrent.futures import ThreadPoolExecutor

        controller = AutoThresholdController()
        errors = []

        def record_many(thread_id):
            try:
                for i in range(50):
                    controller.record_confidence(
                        "d",
                        "p",
                        0.5 + (thread_id % 5) * 0.1,
                        budget=1500,
                        now=1000.0 + thread_id * 100 + i,
                    )
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(record_many, range(10)))

        assert not errors
        state = controller.get_state("d", "p")
        assert state is not None


class TestTimeWindowReset:
    def test_time_window_reset_actually_triggers(self):
        """Window should reset after _WINDOW_SECONDS elapsed, using injected now."""
        controller = AutoThresholdController()

        # Set initial window_start to a known time by recording first sample
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1000.0)
        state = controller.get_state("d", "p")
        # Manually set window_start to match injected time
        state.window_start = 1000.0

        # Record 2 more samples (under _WINDOW_SAMPLES but within time)
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1001.0)
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1002.0)

        # Jump past _WINDOW_SECONDS — should trigger evaluation and reset
        controller.record_confidence("d", "p", 0.2, budget=1500, now=1000.0 + _WINDOW_SECONDS + 1)
        state = controller.get_state("d", "p")
        # After reset, sample_count should be 0 (reset) or small
        assert state.sample_count == 0
        # Window should have been evaluated → budget loosened for low EMA
        assert state.budget_adjustment > 0
