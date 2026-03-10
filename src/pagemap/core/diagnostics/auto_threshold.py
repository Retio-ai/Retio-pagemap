# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""EMA-based auto threshold controller for pruning budget adjustment.

Tracks pruning confidence via Exponential Moving Average and adjusts the
token budget proportionally.  Fast loosening (when confidence is low) and
slow tightening (when consistently high) to avoid oscillation.

Key design decisions:
- EMA (alpha=0.3): robust against single-sample noise, detects trends in 5-7 samples
- Proportional step: ``max(100, int(current_budget * 0.15))`` — scales with budget
- Hybrid windowing: max(20 samples, 1h) — works for both low and high traffic
- Asymmetric: fast loosen, slow tighten (50 consecutive high-confidence pages)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_EMA_ALPHA = 0.3
_LOOSEN_THRESHOLD = 0.5  # EMA below this → loosen budget
_TIGHTEN_THRESHOLD = 0.8  # EMA above this for extended period → tighten
_TIGHTEN_CONSECUTIVE_PAGES = 50  # pages of high confidence before tightening
_MIN_SAMPLES_FOR_ACTION = 3  # minimum samples before any adjustment
_WINDOW_SAMPLES = 20  # samples per window (or 1h, whichever is more)
_WINDOW_SECONDS = 3600.0  # 1 hour time window
_MAX_BUDGET_MULTIPLIER = 3.0  # never exceed base_budget * this
_MIN_BUDGET = 500  # absolute minimum budget


@dataclass
class ThresholdState:
    """Per-domain+page_type threshold state."""

    domain: str
    page_type: str
    base_budget: int
    budget_adjustment: int = 0
    ema: float = 0.5  # start neutral
    sample_count: int = 0
    consecutive_high: int = 0  # consecutive high-confidence samples
    window_start: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def effective_budget(self) -> int:
        """Current budget = base + adjustment, clamped to valid range."""
        raw = self.base_budget + self.budget_adjustment
        max_budget = int(self.base_budget * _MAX_BUDGET_MULTIPLIER)
        return max(_MIN_BUDGET, min(raw, max_budget))


class AutoThresholdController:
    """EMA-based auto threshold for pruning budget.

    Usage::

        controller = AutoThresholdController()
        # After each page build, record confidence:
        controller.record_confidence("example.com", "product_detail", 0.85, budget=1500)
        # Before building pruned context, get adjusted budget:
        budget = controller.get_adjusted_budget("example.com", "product_detail", base_budget=1500)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, ThresholdState] = {}  # "domain:page_type" → state

    def _key(self, domain: str, page_type: str) -> str:
        return f"{domain}:{page_type}"

    def _get_or_create(self, domain: str, page_type: str, base_budget: int) -> ThresholdState:
        key = self._key(domain, page_type)
        if key not in self._states:
            self._states[key] = ThresholdState(
                domain=domain,
                page_type=page_type,
                base_budget=base_budget,
            )
        return self._states[key]

    def record_confidence(
        self,
        domain: str,
        page_type: str,
        confidence: float,
        *,
        budget: int = 1500,
        now: float | None = None,
    ) -> None:
        """Record a pruning confidence observation and update EMA.

        Args:
            domain: Site domain (e.g. "example.com").
            page_type: Page type (e.g. "product_detail", "search_results").
            confidence: Pruning confidence score [0.0, 1.0].
            budget: Current base budget for this page type.
            now: Current timestamp (injectable for tests).
        """
        if now is None:
            now = time.time()

        with self._lock:
            state = self._get_or_create(domain, page_type, budget)

            # Update EMA
            if state.sample_count == 0:
                state.ema = confidence
            else:
                state.ema = _EMA_ALPHA * confidence + (1 - _EMA_ALPHA) * state.ema

            state.sample_count += 1
            state.updated_at = now

            # Track consecutive high-confidence pages (for tightening)
            if confidence > _TIGHTEN_THRESHOLD:
                state.consecutive_high += 1
            else:
                state.consecutive_high = 0

            # Check if window should reset
            elapsed = now - state.window_start
            if state.sample_count >= _WINDOW_SAMPLES or elapsed >= _WINDOW_SECONDS:
                self._evaluate_adjustment(state)
                state.window_start = now
                state.sample_count = 0

    def _evaluate_adjustment(self, state: ThresholdState) -> None:
        """Evaluate and apply budget adjustment based on EMA."""
        if state.sample_count < _MIN_SAMPLES_FOR_ACTION:
            return

        if state.ema < _LOOSEN_THRESHOLD:
            # Fast loosen — proportional step
            step = max(100, int(state.base_budget * 0.15))
            old_adj = state.budget_adjustment
            state.budget_adjustment += step

            # Cap at max multiplier
            max_adj = int(state.base_budget * (_MAX_BUDGET_MULTIPLIER - 1))
            state.budget_adjustment = min(state.budget_adjustment, max_adj)

            if state.budget_adjustment != old_adj:
                logger.info(
                    "Auto threshold LOOSEN: %s:%s ema=%.2f adj=%+d→%+d",
                    state.domain,
                    state.page_type,
                    state.ema,
                    old_adj,
                    state.budget_adjustment,
                )

        elif (
            state.ema > _TIGHTEN_THRESHOLD
            and state.consecutive_high >= _TIGHTEN_CONSECUTIVE_PAGES
            and state.budget_adjustment > 0
        ):
            # Slow tighten — reduce excess, but cautiously
            reduction = max(50, int((state.budget_adjustment) * 0.1))
            old_adj = state.budget_adjustment
            state.budget_adjustment = max(0, state.budget_adjustment - reduction)
            state.consecutive_high = 0  # Reset counter after tightening

            if state.budget_adjustment != old_adj:
                logger.info(
                    "Auto threshold TIGHTEN: %s:%s ema=%.2f adj=%+d→%+d",
                    state.domain,
                    state.page_type,
                    state.ema,
                    old_adj,
                    state.budget_adjustment,
                )

    def get_adjusted_budget(self, domain: str, page_type: str, *, base_budget: int = 1500) -> int:
        """Return the adjusted budget for a domain+page_type.

        Returns *base_budget* if no observations have been recorded yet.
        """
        with self._lock:
            key = self._key(domain, page_type)
            state = self._states.get(key)
            if state is None:
                return base_budget
            # Update base_budget if it changed
            state.base_budget = base_budget
            return state.effective_budget

    def get_state(self, domain: str, page_type: str) -> ThresholdState | None:
        """Return current threshold state for inspection."""
        with self._lock:
            return self._states.get(self._key(domain, page_type))

    def all_states(self) -> dict[str, ThresholdState]:
        """Return all threshold states (for persistence/debugging)."""
        with self._lock:
            return dict(self._states)

    def health(self) -> dict:
        """Return health summary."""
        return {
            "tracked_keys": len(self._states),
            "states": {
                k: {
                    "ema": round(s.ema, 3),
                    "budget_adjustment": s.budget_adjustment,
                    "effective_budget": s.effective_budget,
                    "sample_count": s.sample_count,
                }
                for k, s in self._states.items()
            },
        }
