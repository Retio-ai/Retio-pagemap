# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""BrowserPool — shared Playwright Browser with per-session BrowserContext isolation.

A single Chromium process hosts up to ``max_contexts`` isolated BrowserContexts.
Capacity is gated by ``asyncio.Semaphore`` (CPython FIFO-guaranteed).

Lifecycle follows the ``AsyncContextManager`` pattern::

    async with BrowserPool(config=BrowserConfig()) as pool:
        async with pool.session("sess-1") as session:
            await session.navigate("https://example.com")

Dependencies: browser_session.py only — no server.py imports.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from types import TracebackType

from playwright.async_api import Browser, Playwright, async_playwright

from .browser_session import (
    BrowserConfig,
    BrowserSession,
    _auto_install_chromium,
    chromium_launch_args,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolHealth:
    """Immutable snapshot of pool state for monitoring."""

    active: int
    max_contexts: int
    waiting: int
    browser_connected: bool


# ---------------------------------------------------------------------------
# Internal: pooled context entry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _PooledContext:
    """Tracks a single session's BrowserSession within the pool."""

    session_id: str
    session: BrowserSession
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    holds_semaphore: bool = False  # True when acquired via acquire(), not session() CM


# ---------------------------------------------------------------------------
# BrowserPool
# ---------------------------------------------------------------------------

# Defaults
_DEFAULT_MAX_CONTEXTS = 5
_ACQUIRE_TIMEOUT = 30.0
_DEFAULT_IDLE_TIMEOUT = 1800.0  # 30 minutes
_REAPER_INTERVAL = 60.0


class BrowserPool:
    """Shared browser with per-session BrowserContext isolation.

    Use as an async context manager::

        async with BrowserPool() as pool:
            async with pool.session("sess-1") as sess:
                ...
    """

    def __init__(
        self,
        *,
        max_contexts: int = _DEFAULT_MAX_CONTEXTS,
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
        config: BrowserConfig | None = None,
    ) -> None:
        self._max_contexts = max_contexts
        self._idle_timeout = idle_timeout
        self._config = config or BrowserConfig()

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._contexts: dict[str, _PooledContext] = {}
        self._reaper_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

    # ── AsyncContextManager ──────────────────────────────────────────

    @staticmethod
    def _chromium_launch_args(config: BrowserConfig) -> list[str]:
        """Return hardened Chromium launch arguments."""
        return chromium_launch_args(config)

    async def __aenter__(self) -> BrowserPool:
        self._playwright = await async_playwright().start()
        args = self._chromium_launch_args(self._config)
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self._config.headless,
                args=args,
            )
        except Exception as exc:
            if "executable doesn't exist" in str(exc).lower():
                if await _auto_install_chromium():
                    self._browser = await self._playwright.chromium.launch(
                        headless=self._config.headless,
                        args=args,
                    )
                else:
                    await self._playwright.stop()
                    self._playwright = None
                    raise RuntimeError(
                        "Chromium is not installed and auto-install failed. Please run: playwright install chromium"
                    ) from exc
            else:
                await self._playwright.stop()
                self._playwright = None
                raise

        self._semaphore = asyncio.Semaphore(self._max_contexts)
        self._shutdown_event.clear()
        self._start_reaper()
        logger.info(
            "BrowserPool started (max_contexts=%d, idle_timeout=%.0fs)",
            self._max_contexts,
            self._idle_timeout,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.shutdown()

    # ── Resource management ──────────────────────────────────────────

    @asynccontextmanager
    async def session(self, session_id: str):
        """Acquire a BrowserSession for *session_id*, releasing on exit.

        Blocks if the pool is at capacity (up to ACQUIRE_TIMEOUT seconds).

        Usage::

            async with pool.session("sess-1") as sess:
                await sess.navigate(url)
        """
        async with asyncio.timeout(_ACQUIRE_TIMEOUT):
            await self._semaphore.acquire()
        try:
            sess = await self._create_or_get(session_id)
            yield sess
        finally:
            # Touch last_used_at for idle tracking
            entry = self._contexts.get(session_id)
            if entry is not None:
                entry.last_used_at = time.monotonic()
            self._semaphore.release()

    async def acquire(self, session_id: str) -> BrowserSession:
        """Low-level acquire: get or create a session.

        Caller is responsible for calling :meth:`release` when done.
        Prefer :meth:`session` context manager instead.
        """
        async with asyncio.timeout(_ACQUIRE_TIMEOUT):
            await self._semaphore.acquire()
        try:
            sess = await self._create_or_get(session_id)
        except Exception:
            self._semaphore.release()
            raise
        # Mark that this entry permanently holds a semaphore slot
        entry = self._contexts.get(session_id)
        if entry is not None:
            entry.holds_semaphore = True
        return sess

    async def release(self, session_id: str) -> None:
        """Release and destroy a session's BrowserContext."""
        entry = self._contexts.pop(session_id, None)
        if entry is None:
            logger.warning("Pool release: session '%s' not found (no-op)", session_id)
            return
        with suppress(Exception):
            await entry.session.stop()
        if entry.holds_semaphore:
            self._semaphore.release()
        logger.info("Pool released session: %s", session_id)

    # ── Monitoring ───────────────────────────────────────────────────

    def health(self) -> PoolHealth:
        """Return a snapshot of pool health."""
        browser_ok = self._browser is not None and self._browser.is_connected()
        # Semaphore._value gives the number of *available* slots
        sem_value = self._semaphore._value if self._semaphore else self._max_contexts
        waiting = max(0, len(self._contexts) - (self._max_contexts - sem_value))
        return PoolHealth(
            active=len(self._contexts),
            max_contexts=self._max_contexts,
            waiting=waiting,
            browser_connected=browser_ok,
        )

    @property
    def active_count(self) -> int:
        return len(self._contexts)

    @property
    def capacity(self) -> int:
        return self._max_contexts

    # ── Internal ─────────────────────────────────────────────────────

    async def _create_or_get(self, session_id: str) -> BrowserSession:
        """Return existing session or create a new one."""
        entry = self._contexts.get(session_id)
        if entry is not None:
            entry.last_used_at = time.monotonic()
            return entry.session

        sess = BrowserSession(self._config)
        await sess.start_from_pool(self._browser)
        entry = _PooledContext(session_id=session_id, session=sess)
        self._contexts[session_id] = entry
        logger.info("Pool created session: %s (active=%d)", session_id, len(self._contexts))
        return sess

    # ── Reaper ───────────────────────────────────────────────────────

    def _start_reaper(self) -> None:
        """Start the idle context reaper task."""
        self._reaper_task = asyncio.get_running_loop().create_task(self._reaper_loop(), name="pagemap-pool-reaper")
        self._reaper_task.add_done_callback(self._handle_reaper_crash)

    def _handle_reaper_crash(self, task: asyncio.Task) -> None:
        """Restart reaper if it crashed unexpectedly (not cancelled)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and not self._shutdown_event.is_set():
            logger.error("Pool reaper crashed, restarting: %s", exc, exc_info=exc)
            self._start_reaper()

    async def _reaper_loop(self) -> None:
        """Periodically evict idle sessions."""
        while not self._shutdown_event.is_set():
            try:
                async with asyncio.timeout(_REAPER_INTERVAL):
                    await self._shutdown_event.wait()
                    return  # shutdown requested
            except TimeoutError:
                pass  # normal wakeup — run reap cycle

            now = time.monotonic()
            to_reap = [sid for sid, entry in self._contexts.items() if (now - entry.last_used_at) > self._idle_timeout]
            for sid in to_reap:
                entry = self._contexts.pop(sid, None)
                if entry is not None:
                    with suppress(Exception):
                        await entry.session.stop()
                    if entry.holds_semaphore:
                        self._semaphore.release()
                    logger.info("Reaper evicted idle session: %s", sid)

    # ── Shutdown ─────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Shut down all sessions, browser, and playwright."""
        self._shutdown_event.set()

        # Cancel reaper
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None

        # Close all sessions
        for _sid, entry in list(self._contexts.items()):
            with suppress(Exception):
                await entry.session.stop()
        self._contexts.clear()

        # Close browser + playwright
        if self._browser:
            with suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright:
            with suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

        logger.info("BrowserPool shut down")
