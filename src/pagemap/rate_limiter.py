# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Token-bucket rate limiter with per-client and global tiers.

Standalone leaf module with zero dependency on server.py.
Uses stdlib only (time, asyncio, logging, dataclasses, math).

Design choices:

- **Token Bucket** — burst-tolerant, O(1), matches AI agent bursty traffic.
- **Two-tier** — per-client + global buckets (prevents aggregate overload).
- **Per-tool costs** — dict-based cost table for accurate resource modeling.
- **STDIO bypass** — ``enabled=False`` by default; ``client_id=""`` always passes.
- **Reaper** — ``done_callback`` crash-restart pattern (NOT TaskGroup).
- **Clock** — ``time.monotonic()`` (consistent with browser_pool.py, cache.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from dataclasses import dataclass, field
from types import TracebackType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-tool cost table
# ---------------------------------------------------------------------------

TOOL_COSTS: dict[str, int] = {
    "get_page_state": 1,
    "scroll_page": 1,
    "navigate_back": 2,
    "take_screenshot": 2,
    "wait_for": 2,
    "execute_action": 3,
    "get_page_map": 5,
    "fill_form": 5,
    "batch_get_page_map": 10,
}
DEFAULT_TOOL_COST = 3


def tool_cost(tool_name: str) -> int:
    """Look up the token cost for *tool_name*."""
    return TOOL_COSTS.get(tool_name, DEFAULT_TOOL_COST)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Immutable configuration for the rate limiter."""

    enabled: bool = False  # off by default (STDIO); HTTP transport sets True
    capacity: int = 30  # max burst tokens
    refill_rate: float = 2.0  # tokens/sec sustained rate
    global_capacity: int = 100  # aggregate across all clients
    global_refill_rate: float = 10.0
    stale_timeout: float = 1800.0  # 30min idle eviction
    reaper_interval: float = 60.0

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {self.capacity}")
        if self.refill_rate <= 0:
            raise ValueError(f"refill_rate must be > 0, got {self.refill_rate}")
        if self.global_capacity <= 0:
            raise ValueError(f"global_capacity must be > 0, got {self.global_capacity}")
        if self.global_refill_rate <= 0:
            raise ValueError(f"global_refill_rate must be > 0, got {self.global_refill_rate}")
        if self.stale_timeout < 0:
            raise ValueError(f"stale_timeout must be >= 0, got {self.stale_timeout}")
        if self.reaper_interval <= 0:
            raise ValueError(f"reaper_interval must be > 0, got {self.reaper_interval}")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset: float  # seconds until full (IETF RateLimit-Reset)
    retry_after: float  # seconds to wait if rejected (0.0 if allowed)
    scope: str  # "client" | "global" | "bypass"

    def to_headers(self) -> dict[str, str]:
        """Return IETF draft-10 compliant rate limit headers."""
        headers: dict[str, str] = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(math.ceil(self.reset)),
        }
        if not self.allowed:
            headers["Retry-After"] = str(math.ceil(self.retry_after))
        return headers


# Sentinel for bypass results
_ALWAYS_ALLOWED = RateLimitResult(
    allowed=True,
    limit=0,
    remaining=0,
    reset=0.0,
    retry_after=0.0,
    scope="bypass",
)


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimitHealth:
    """Immutable snapshot of rate limiter state for monitoring."""

    enabled: bool
    active_clients: int
    total_requests: int
    total_rejected: int
    global_tokens_remaining: int
    global_capacity: int
    rejection_rate: float  # total_rejected / total_requests (0.0 if no requests)


# ---------------------------------------------------------------------------
# Internal: token bucket
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Simple token bucket with lazy refill."""

    __slots__ = ("_capacity", "_refill_rate", "_tokens", "_last_refill", "_last_used")

    def __init__(self, capacity: int, refill_rate: float) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._tokens = float(capacity)
        now = time.monotonic()
        self._last_refill = now
        self._last_used = now

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now

    def try_consume(self, cost: int) -> bool:
        """Attempt to consume *cost* tokens. Returns True if allowed."""
        self._refill()
        if self._tokens >= cost:
            self._tokens -= cost
            self._last_used = time.monotonic()
            return True
        return False

    def refund(self, cost: int) -> None:
        """Return tokens (used when global passes but client rejects)."""
        self._tokens = min(self._capacity, self._tokens + cost)

    @property
    def remaining(self) -> int:
        """Current available tokens (after refill)."""
        self._refill()
        return int(self._tokens)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def seconds_until_full(self) -> float:
        """Seconds until bucket is fully refilled."""
        self._refill()
        deficit = self._capacity - self._tokens
        if deficit <= 0:
            return 0.0
        if self._refill_rate == 0:
            return float("inf")
        return deficit / self._refill_rate

    @property
    def last_used(self) -> float:
        return self._last_used


# ---------------------------------------------------------------------------
# Internal: per-client bucket
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ClientBucket:
    """Wraps a token bucket with per-client metadata."""

    client_id: str
    bucket: _TokenBucket
    created_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Two-tier (per-client + global) async token-bucket rate limiter.

    Usage::

        async with RateLimiter(config=RateLimitConfig(enabled=True)) as rl:
            result = await rl.acquire("client-1", "get_page_map")
            if not result.allowed:
                raise RateLimitError(...)
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._clients: dict[str, _ClientBucket] = {}
        self._global_bucket = _TokenBucket(self._config.global_capacity, self._config.global_refill_rate)
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None
        self._total_requests = 0
        self._total_rejected = 0

    # -- Async context manager --

    async def __aenter__(self) -> RateLimiter:
        if self._config.enabled:
            self._start_reaper()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.shutdown()

    # -- Public API --

    async def acquire(self, client_id: str, tool_name: str) -> RateLimitResult:
        """Check rate limit for *client_id* calling *tool_name*.

        STDIO bypass: if ``enabled=False`` or ``client_id`` is empty,
        returns an always-allowed sentinel.
        """
        if not self._config.enabled or not client_id:
            return _ALWAYS_ALLOWED

        cost = tool_cost(tool_name)

        async with self._lock:
            self._total_requests += 1

            # Tier 1: global bucket
            if not self._global_bucket.try_consume(cost):
                self._total_rejected += 1
                return RateLimitResult(
                    allowed=False,
                    limit=self._config.global_capacity,
                    remaining=self._global_bucket.remaining,
                    reset=self._global_bucket.seconds_until_full,
                    retry_after=cost / self._config.global_refill_rate,
                    scope="global",
                )

            # Tier 2: per-client bucket
            cb = self._clients.get(client_id)
            if cb is None:
                cb = _ClientBucket(
                    client_id=client_id,
                    bucket=_TokenBucket(self._config.capacity, self._config.refill_rate),
                )
                self._clients[client_id] = cb

            if not cb.bucket.try_consume(cost):
                # Refund global tokens
                self._global_bucket.refund(cost)
                self._total_rejected += 1
                return RateLimitResult(
                    allowed=False,
                    limit=self._config.capacity,
                    remaining=cb.bucket.remaining,
                    reset=cb.bucket.seconds_until_full,
                    retry_after=cost / self._config.refill_rate,
                    scope="client",
                )

            return RateLimitResult(
                allowed=True,
                limit=self._config.capacity,
                remaining=cb.bucket.remaining,
                reset=cb.bucket.seconds_until_full,
                retry_after=0.0,
                scope="client",
            )

    def health(self) -> RateLimitHealth:
        """Return an immutable snapshot of rate limiter state."""
        return RateLimitHealth(
            enabled=self._config.enabled,
            active_clients=len(self._clients),
            total_requests=self._total_requests,
            total_rejected=self._total_rejected,
            global_tokens_remaining=self._global_bucket.remaining,
            global_capacity=self._config.global_capacity,
            rejection_rate=(self._total_rejected / self._total_requests if self._total_requests > 0 else 0.0),
        )

    async def shutdown(self) -> None:
        """Cancel the reaper and clean up."""
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        self._clients.clear()

    # -- Internal: reaper --

    def _start_reaper(self) -> None:
        """Launch (or re-launch) the idle-client reaper loop."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        self._reaper_task = asyncio.get_running_loop().create_task(self._reaper_loop())
        self._reaper_task.add_done_callback(self._reaper_done)

    def _reaper_done(self, task: asyncio.Task) -> None:
        """Restart reaper if it crashed (not cancelled)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Rate limiter reaper crashed, restarting: %s", exc)
            with contextlib.suppress(RuntimeError):
                self._start_reaper()

    async def _reaper_loop(self) -> None:
        """Periodically evict idle client buckets."""
        while True:
            await asyncio.sleep(self._config.reaper_interval)
            now = time.monotonic()
            async with self._lock:
                stale = [
                    cid for cid, cb in self._clients.items() if (now - cb.bucket.last_used) > self._config.stale_timeout
                ]
                for cid in stale:
                    del self._clients[cid]
                if stale:
                    logger.debug("Reaped %d stale rate-limit client(s)", len(stale))
