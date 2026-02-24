# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.rate_limiter — leaf module, no server.py imports."""

from __future__ import annotations

import asyncio
import math
import time

import pytest

from pagemap.rate_limiter import (
    DEFAULT_TOOL_COST,
    TOOL_COSTS,
    RateLimitConfig,
    RateLimiter,
    RateLimitResult,
    _TokenBucket,
    tool_cost,
)

# ── Config validation ───────────────────────────────────────────


class TestRateLimitConfig:
    def test_defaults(self):
        cfg = RateLimitConfig()
        assert cfg.enabled is False
        assert cfg.capacity == 30
        assert cfg.refill_rate == 2.0

    def test_invalid_capacity(self):
        with pytest.raises(ValueError, match="capacity"):
            RateLimitConfig(capacity=0)

    def test_invalid_refill_rate(self):
        with pytest.raises(ValueError, match="refill_rate"):
            RateLimitConfig(refill_rate=-1)

    def test_invalid_global_capacity(self):
        with pytest.raises(ValueError, match="global_capacity"):
            RateLimitConfig(global_capacity=0)

    def test_invalid_global_refill_rate(self):
        with pytest.raises(ValueError, match="global_refill_rate"):
            RateLimitConfig(global_refill_rate=0)

    def test_invalid_stale_timeout(self):
        with pytest.raises(ValueError, match="stale_timeout"):
            RateLimitConfig(stale_timeout=-1)

    def test_invalid_reaper_interval(self):
        with pytest.raises(ValueError, match="reaper_interval"):
            RateLimitConfig(reaper_interval=0)


# ── Token bucket basics ────────────────────────────────────────


class TestTokenBucket:
    def test_acquire_allowed(self):
        bucket = _TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.try_consume(1) is True

    def test_acquire_rejected_after_exhaustion(self):
        bucket = _TokenBucket(capacity=5, refill_rate=0.0001)
        for _ in range(5):
            assert bucket.try_consume(1) is True
        assert bucket.try_consume(1) is False

    def test_remaining_decreases(self):
        bucket = _TokenBucket(capacity=10, refill_rate=0.0)
        assert bucket.remaining == 10
        bucket.try_consume(3)
        assert bucket.remaining == 7

    def test_refund(self):
        bucket = _TokenBucket(capacity=10, refill_rate=0.0)
        bucket.try_consume(5)
        bucket.refund(3)
        assert bucket.remaining == 8

    def test_refund_capped_at_capacity(self):
        bucket = _TokenBucket(capacity=10, refill_rate=0.0)
        bucket.refund(100)
        assert bucket.remaining == 10

    def test_seconds_until_full(self):
        bucket = _TokenBucket(capacity=10, refill_rate=1.0)
        bucket.try_consume(10)
        suf = bucket.seconds_until_full
        assert suf > 0

    def test_seconds_until_full_when_full(self):
        bucket = _TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.seconds_until_full == 0.0


# ── Refill over time ───────────────────────────────────────────


class TestRefill:
    def test_refill_restores_tokens(self):
        bucket = _TokenBucket(capacity=10, refill_rate=100.0)
        bucket.try_consume(10)
        assert bucket.remaining == 0
        # With refill_rate=100/s, after ~0.1s we get ~10 tokens back
        time.sleep(0.11)
        assert bucket.remaining >= 9  # allow small timing variance


# ── Per-tool costs ──────────────────────────────────────────────


class TestToolCosts:
    def test_known_tool(self):
        assert tool_cost("get_page_map") == 5
        assert tool_cost("get_page_state") == 1
        assert tool_cost("batch_get_page_map") == 10

    def test_unknown_tool_default(self):
        assert tool_cost("nonexistent_tool") == DEFAULT_TOOL_COST

    def test_cost_table_values(self):
        for name, cost in TOOL_COSTS.items():
            assert cost > 0, f"Tool {name} has non-positive cost: {cost}"


# ── STDIO bypass ────────────────────────────────────────────────


class TestBypass:
    @pytest.mark.asyncio
    async def test_disabled_returns_bypass(self):
        rl = RateLimiter(RateLimitConfig(enabled=False))
        result = await rl.acquire("client-1", "get_page_map")
        assert result.allowed is True
        assert result.scope == "bypass"

    @pytest.mark.asyncio
    async def test_empty_client_id_returns_bypass(self):
        rl = RateLimiter(RateLimitConfig(enabled=True))
        result = await rl.acquire("", "get_page_map")
        assert result.allowed is True
        assert result.scope == "bypass"


# ── Two-tier buckets ───────────────────────────────────────────


class TestTwoTier:
    @pytest.mark.asyncio
    async def test_acquire_allowed(self):
        cfg = RateLimitConfig(enabled=True, capacity=30, global_capacity=100)
        rl = RateLimiter(cfg)
        result = await rl.acquire("client-1", "get_page_state")
        assert result.allowed is True
        assert result.scope == "client"

    @pytest.mark.asyncio
    async def test_client_rejection(self):
        cfg = RateLimitConfig(enabled=True, capacity=5, refill_rate=0.0001, global_capacity=100)
        rl = RateLimiter(cfg)
        # Exhaust client bucket (cost=1 each)
        for _ in range(5):
            r = await rl.acquire("client-1", "get_page_state")
            assert r.allowed is True
        r = await rl.acquire("client-1", "get_page_state")
        assert r.allowed is False
        assert r.scope == "client"

    @pytest.mark.asyncio
    async def test_global_rejection_short_circuits(self):
        cfg = RateLimitConfig(enabled=True, capacity=100, global_capacity=3, global_refill_rate=0.0001)
        rl = RateLimiter(cfg)
        for _ in range(3):
            r = await rl.acquire("client-1", "get_page_state")
            assert r.allowed is True
        r = await rl.acquire("client-1", "get_page_state")
        assert r.allowed is False
        assert r.scope == "global"

    @pytest.mark.asyncio
    async def test_client_rejection_refunds_global(self):
        cfg = RateLimitConfig(
            enabled=True,
            capacity=3,
            refill_rate=0.0001,
            global_capacity=100,
            global_refill_rate=0.0001,
        )
        rl = RateLimiter(cfg)
        # Exhaust client bucket (3 tokens)
        for _ in range(3):
            await rl.acquire("client-1", "get_page_state")
        # Global consumed 3 so far → 97 remaining
        global_before_rejected = rl._global_bucket.remaining
        # 4th call: global consumes 1, client rejects, global refunds 1 → net 0
        r = await rl.acquire("client-1", "get_page_state")
        assert r.allowed is False
        global_after_rejected = rl._global_bucket.remaining
        # Net effect on global should be zero (consumed then refunded)
        assert global_after_rejected == global_before_rejected


# ── Per-client isolation ───────────────────────────────────────


class TestClientIsolation:
    @pytest.mark.asyncio
    async def test_separate_buckets(self):
        cfg = RateLimitConfig(enabled=True, capacity=3, refill_rate=0.0001, global_capacity=100)
        rl = RateLimiter(cfg)
        # Exhaust client-1
        for _ in range(3):
            await rl.acquire("client-1", "get_page_state")
        r1 = await rl.acquire("client-1", "get_page_state")
        assert r1.allowed is False
        # client-2 should still work
        r2 = await rl.acquire("client-2", "get_page_state")
        assert r2.allowed is True


# ── Per-tool cost integration ──────────────────────────────────


class TestToolCostIntegration:
    @pytest.mark.asyncio
    async def test_batch_costs_more(self):
        cfg = RateLimitConfig(enabled=True, capacity=10, refill_rate=0.0001, global_capacity=100)
        rl = RateLimiter(cfg)
        # batch_get_page_map costs 10 — exactly exhausts capacity
        r = await rl.acquire("c1", "batch_get_page_map")
        assert r.allowed is True
        r = await rl.acquire("c1", "get_page_state")
        assert r.allowed is False


# ── Reaper ──────────────────────────────────────────────────────


class TestReaper:
    @pytest.mark.asyncio
    async def test_stale_clients_evicted(self):
        cfg = RateLimitConfig(
            enabled=True,
            stale_timeout=0.05,
            reaper_interval=0.05,
        )
        async with RateLimiter(cfg) as rl:
            await rl.acquire("stale-client", "get_page_state")
            assert len(rl._clients) == 1
            # Wait for reaper cycle + stale timeout
            await asyncio.sleep(0.2)
            # Client should be reaped
            assert len(rl._clients) == 0


# ── Health snapshot ─────────────────────────────────────────────


class TestHealth:
    @pytest.mark.asyncio
    async def test_initial_health(self):
        cfg = RateLimitConfig(enabled=True, global_capacity=100)
        rl = RateLimiter(cfg)
        h = rl.health()
        assert h.enabled is True
        assert h.active_clients == 0
        assert h.total_requests == 0
        assert h.total_rejected == 0
        assert h.rejection_rate == 0.0

    @pytest.mark.asyncio
    async def test_counters_after_requests(self):
        cfg = RateLimitConfig(enabled=True, capacity=2, refill_rate=0.0001, global_capacity=100)
        rl = RateLimiter(cfg)
        await rl.acquire("c1", "get_page_state")
        await rl.acquire("c1", "get_page_state")
        await rl.acquire("c1", "get_page_state")  # should reject
        h = rl.health()
        assert h.total_requests == 3
        assert h.total_rejected == 1
        assert h.active_clients == 1
        assert h.rejection_rate == pytest.approx(1 / 3)

    @pytest.mark.asyncio
    async def test_global_capacity_in_health(self):
        cfg = RateLimitConfig(enabled=True, global_capacity=50)
        rl = RateLimiter(cfg)
        h = rl.health()
        assert h.global_capacity == 50


# ── RateLimitResult.to_headers() ───────────────────────────────


class TestHeaders:
    def test_allowed_headers(self):
        result = RateLimitResult(allowed=True, limit=30, remaining=25, reset=5.0, retry_after=0.0, scope="client")
        headers = result.to_headers()
        assert headers["RateLimit-Limit"] == "30"
        assert headers["RateLimit-Remaining"] == "25"
        assert headers["RateLimit-Reset"] == "5"
        assert "Retry-After" not in headers

    def test_rejected_headers(self):
        result = RateLimitResult(allowed=False, limit=30, remaining=0, reset=15.5, retry_after=2.3, scope="client")
        headers = result.to_headers()
        assert headers["RateLimit-Limit"] == "30"
        assert headers["RateLimit-Remaining"] == "0"
        assert headers["RateLimit-Reset"] == str(math.ceil(15.5))
        assert headers["Retry-After"] == str(math.ceil(2.3))

    def test_reset_ceil(self):
        result = RateLimitResult(allowed=True, limit=10, remaining=5, reset=1.1, retry_after=0.0, scope="client")
        assert result.to_headers()["RateLimit-Reset"] == "2"


# ── Async context manager lifecycle ────────────────────────────


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_enter_exit(self):
        cfg = RateLimitConfig(enabled=True)
        async with RateLimiter(cfg) as rl:
            assert rl._reaper_task is not None
            assert not rl._reaper_task.done()
        # After exit, reaper should be cancelled
        assert rl._reaper_task is None or rl._reaper_task.done()

    @pytest.mark.asyncio
    async def test_disabled_no_reaper(self):
        cfg = RateLimitConfig(enabled=False)
        async with RateLimiter(cfg) as rl:
            assert rl._reaper_task is None


# ── Edge cases ──────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_remaining(self):
        cfg = RateLimitConfig(enabled=True, capacity=1, refill_rate=0.0001, global_capacity=100)
        rl = RateLimiter(cfg)
        r = await rl.acquire("c1", "get_page_state")
        assert r.allowed is True
        assert r.remaining == 0

    @pytest.mark.asyncio
    async def test_burst_exhaustion(self):
        cfg = RateLimitConfig(enabled=True, capacity=5, refill_rate=0.0001, global_capacity=100)
        rl = RateLimiter(cfg)
        results = [await rl.acquire("c1", "get_page_state") for _ in range(6)]
        allowed = [r for r in results if r.allowed]
        rejected = [r for r in results if not r.allowed]
        assert len(allowed) == 5
        assert len(rejected) == 1

    @pytest.mark.asyncio
    async def test_concurrent_acquires(self):
        cfg = RateLimitConfig(enabled=True, capacity=10, global_capacity=100)
        rl = RateLimiter(cfg)
        results = await asyncio.gather(*[rl.acquire(f"c{i}", "get_page_state") for i in range(5)])
        assert all(r.allowed for r in results)

    @pytest.mark.asyncio
    async def test_shutdown_clears_clients(self):
        cfg = RateLimitConfig(enabled=True)
        rl = RateLimiter(cfg)
        await rl.acquire("c1", "get_page_state")
        await rl.shutdown()
        assert len(rl._clients) == 0
