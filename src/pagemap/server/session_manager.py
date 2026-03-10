# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""SessionManager — maps session_id to per-session state.

Protocol-based design: ``StdioSessionManager`` wraps existing ``ServerState``
for backward compatibility, ``HttpSessionManager`` provides per-session isolation
backed by ``BrowserPool``.

Dependencies: context.py, cache.py, template_cache.py, browser_pool.py.
No server.py import (acyclic).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pagemap.cache import PageMapCache
from pagemap.errors import ResourceExhaustionError
from pagemap.template_cache import InMemoryTemplateCache

from .context import RequestContext

if TYPE_CHECKING:
    from .browser_pool import BrowserPool
    from .browser_session import BrowserSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STDIO_SESSION_ID = "__stdio__"
DEFAULT_SESSION_TTL = 1800.0  # 30 minutes
MAX_NAVIGATIONS = int(os.environ.get("PAGEMAP_MAX_NAVIGATIONS", "100"))
MAX_SESSION_AGE = float(os.environ.get("PAGEMAP_MAX_SESSION_AGE", str(DEFAULT_SESSION_TTL)))
MAX_TABS_PER_SESSION = int(os.environ.get("PAGEMAP_MAX_TABS", "5"))
MAX_SESSIONS_PER_TENANT = int(os.environ.get("PAGEMAP_MAX_SESSIONS_PER_TENANT", "0"))  # 0 = unlimited


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SessionNotFoundError(Exception):
    """Raised when an HTTP session is not found (MCP 404)."""


# ---------------------------------------------------------------------------
# SessionEntry — per-session mutable state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SessionEntry:
    """Mutable state associated with a single session."""

    session_id: str
    cache: PageMapCache
    tool_lock: asyncio.Lock
    session_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    browser_session: BrowserSession | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    navigation_count: int = 0  # D2: incremented per get_session() call
    browser_acquired_at: float = 0.0  # D2: monotonic timestamp when BrowserSession acquired
    tenant_id: str = ""  # P0-1: track tenant for counter decrement
    multi_tab: object | None = None  # Phase 1: MultiTabSession (avoid circular import)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionManagerProtocol(Protocol):
    """Interface for session management — STDIO and HTTP implementations."""

    async def get_context(self, session_id: str) -> RequestContext: ...

    def get_tool_lock(self, session_id: str) -> asyncio.Lock: ...

    async def remove_session(self, session_id: str) -> None: ...

    async def shutdown(self) -> None: ...

    @property
    def active_sessions(self) -> int: ...


# ---------------------------------------------------------------------------
# STDIO implementation
# ---------------------------------------------------------------------------


class StdioSessionManager:
    """Wraps existing ServerState for backward compatibility.

    Single-session manager for STDIO transport — delegates to ``_state``
    attributes (cache, tool_lock, template_cache) unchanged.
    """

    def __init__(self, state) -> None:
        """Initialize with a ServerState instance.

        Args:
            state: The module-level ``ServerState`` from server.py.
        """
        self._state = state

    async def get_context(self, session_id: str = STDIO_SESSION_ID) -> RequestContext:
        """Return RequestContext for the single STDIO session.

        Raises KeyError if session_id is not ``__stdio__``.
        """
        if session_id != STDIO_SESSION_ID:
            raise KeyError(f"StdioSessionManager only supports session '{STDIO_SESSION_ID}', got '{session_id}'")
        # Import lazily to avoid circular dependency
        from pagemap import server as _srv

        return RequestContext(
            request_id=uuid.uuid4().hex[:12],
            session_id=self._state.session_id,
            client_id="",
            cache=self._state.cache,
            template_cache=self._state.template_cache,
            get_session=_srv._get_session,
            multi_tab=self._state.multi_tab,
            get_or_create_multi_tab=self._state.get_or_create_multi_tab,
        )

    def get_tool_lock(self, session_id: str = STDIO_SESSION_ID) -> asyncio.Lock:
        """Return the single tool lock for STDIO."""
        return self._state.tool_lock

    async def remove_session(self, session_id: str = STDIO_SESSION_ID) -> None:
        """No-op for STDIO — the single session cannot be removed."""
        logger.debug("StdioSessionManager.remove_session called (no-op)")

    async def shutdown(self) -> None:
        """No-op — ServerState lifecycle is managed by server.py main()."""

    @property
    def active_sessions(self) -> int:
        return 1


# ---------------------------------------------------------------------------
# HTTP implementation
# ---------------------------------------------------------------------------


class HttpSessionManager:
    """Per-session state backed by BrowserPool.

    Each HTTP session gets its own ``PageMapCache``, ``asyncio.Lock``,
    and ``BrowserSession`` acquired from the shared pool.

    The ``InMemoryTemplateCache`` is shared across all sessions
    (domain structural knowledge is site-level, not user-level).
    """

    def __init__(
        self,
        pool: BrowserPool,
        template_cache: InMemoryTemplateCache | None = None,
        session_ttl: float = DEFAULT_SESSION_TTL,
    ) -> None:
        self._pool = pool
        self._template_cache = template_cache or InMemoryTemplateCache()
        self._session_ttl = session_ttl
        self._sessions: dict[str, SessionEntry] = {}
        self._sessions_lock = asyncio.Lock()
        self._tenant_session_counts: dict[str, int] = {}  # S3: per-tenant session counter

    async def get_context(self, session_id: str, *, tenant_id: str = "") -> RequestContext:
        """Return RequestContext for the given HTTP session.

        Auto-creates a new session entry if one does not exist.
        A ``BrowserSession`` is lazily acquired from the pool on the
        first ``get_session()`` call within the returned context.

        Raises ``ResourceExhaustionError`` if tenant session limit is exceeded.

        P0-2: Entire check + create + counter increment runs atomically
        under ``_sessions_lock`` to prevent TOCTOU races.
        """
        entry = await self._get_or_create_entry_with_tenant(session_id, tenant_id)

        async def _get_session() -> BrowserSession:
            return await self._get_session_for_entry(entry)

        async def _get_or_create_multi_tab():
            return await self._get_or_create_multi_tab_for_entry(entry)

        return RequestContext(
            request_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            client_id=session_id,
            cache=entry.cache,
            template_cache=self._template_cache,
            get_session=_get_session,
            tenant_id=tenant_id,
            multi_tab=entry.multi_tab,
            get_or_create_multi_tab=_get_or_create_multi_tab,
        )

    def get_tool_lock(self, session_id: str) -> asyncio.Lock:
        """Return per-session tool lock."""
        entry = self._sessions.get(session_id)
        if entry is None:
            raise SessionNotFoundError(f"Session '{session_id}' not found")
        return entry.tool_lock

    async def remove_session(self, session_id: str) -> None:
        """Remove a session — releases pool context and clears cache.

        D1: BrowserContext.close() (via pool.release -> session.stop)
        destroys all cookies, localStorage, and sessionStorage.
        P0-1: decrements tenant counter on removal.
        """
        async with self._sessions_lock:
            entry = self._sessions.pop(session_id, None)
        if entry is None:
            return
        self._decrement_tenant_counter(entry)
        await self._cleanup_entry(entry)
        logger.info("HTTP session removed: %s", session_id)

    async def shutdown(self) -> None:
        """Clean up all HTTP sessions."""
        async with self._sessions_lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()
            self._tenant_session_counts.clear()
        for sid, entry in sessions:
            await self._cleanup_entry(entry)
            logger.info("HTTP session cleaned up: %s", sid)

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    # ── Internal ─────────────────────────────────────────────────────

    def _decrement_tenant_counter(self, entry: SessionEntry) -> None:
        """P0-1: Decrement tenant session counter; remove key when <= 0."""
        if not entry.tenant_id:
            return
        count = self._tenant_session_counts.get(entry.tenant_id, 0) - 1
        if count <= 0:
            self._tenant_session_counts.pop(entry.tenant_id, None)
        else:
            self._tenant_session_counts[entry.tenant_id] = count

    async def _cleanup_entry(self, entry: SessionEntry) -> None:
        """Invalidate cache and release browser.

        D1: pool.release -> session.stop -> context.close() destroys all
        cookies, localStorage, and sessionStorage for this browser context.
        """
        if entry.multi_tab is not None:
            with suppress(Exception):
                await entry.multi_tab.close_all()
            entry.multi_tab = None
        entry.cache.invalidate_all()
        if entry.browser_session is not None:
            with suppress(Exception):
                await self._pool.release(entry.session_id)
            entry.browser_session = None

    async def _get_or_create_multi_tab_for_entry(self, entry: SessionEntry):
        """Get or create MultiTabSession for an HTTP session entry.

        Must be called with tool_lock held (serialized by caller).
        """
        if entry.multi_tab is None:
            session = await self._get_session_for_entry(entry)
            from .multi_tab import MultiTabSession

            entry.multi_tab = MultiTabSession(session._browser)
        return entry.multi_tab

    def _is_session_expired(self, entry: SessionEntry) -> bool:
        """D3: Check if SessionEntry has exceeded its TTL."""
        return (time.monotonic() - entry.created_at) > self._session_ttl

    def _check_recycle(self, entry: SessionEntry) -> str | None:
        """D2: Return recycle reason if browser context should be refreshed, None otherwise."""
        if entry.navigation_count >= MAX_NAVIGATIONS:
            return f"nav_count={entry.navigation_count}>={MAX_NAVIGATIONS}"
        if entry.browser_acquired_at > 0:
            age = time.monotonic() - entry.browser_acquired_at
            if age >= MAX_SESSION_AGE:
                return f"age={age:.0f}s>={MAX_SESSION_AGE:.0f}s"
        return None

    async def _get_or_create_entry_with_tenant(self, session_id: str, tenant_id: str = "") -> SessionEntry:
        """Get existing session or create a new one (D3: TTL enforcement).

        P0-2: The entire sequence (is_new check + tenant limit validation +
        session creation + counter increment) runs atomically under
        ``_sessions_lock`` to prevent TOCTOU races.
        """
        # Fast path — existing, non-expired entry (no tenant mutation).
        # Safe without lock: asyncio is single-threaded, so the dict lookup
        # and last_used_at write cannot be preempted.  The slow path below
        # re-checks under _sessions_lock (double-check pattern) for the
        # create/expire/tenant-counter mutations that DO require atomicity.
        entry = self._sessions.get(session_id)
        if entry is not None and not self._is_session_expired(entry):
            entry.last_used_at = time.monotonic()
            return entry

        async with self._sessions_lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                if self._is_session_expired(entry):
                    self._sessions.pop(session_id, None)
                    self._decrement_tenant_counter(entry)
                    await self._cleanup_entry(entry)
                    logger.info("HTTP session TTL expired, removed: %s", session_id)
                else:
                    entry.last_used_at = time.monotonic()
                    return entry

            # Tenant limit check (atomic with creation)
            if tenant_id and MAX_SESSIONS_PER_TENANT > 0:
                current = self._tenant_session_counts.get(tenant_id, 0)
                if current >= MAX_SESSIONS_PER_TENANT:
                    raise ResourceExhaustionError(
                        f"Tenant session limit exceeded ({current}/{MAX_SESSIONS_PER_TENANT}). "
                        "Close existing sessions or wait for TTL expiry."
                    )

            entry = SessionEntry(
                session_id=session_id,
                cache=PageMapCache(),
                tool_lock=asyncio.Lock(),
                tenant_id=tenant_id,
            )
            self._sessions[session_id] = entry

            # Increment tenant counter
            if tenant_id:
                self._tenant_session_counts[tenant_id] = self._tenant_session_counts.get(tenant_id, 0) + 1

            logger.info("HTTP session created: %s", session_id)
            return entry

    async def _get_session_for_entry(self, entry: SessionEntry) -> BrowserSession:
        """Get or create a BrowserSession for a session entry via the pool.

        D2: transparent browser recycling on nav-count / age thresholds.
        D3: hard tab-quota rejection.
        """
        async with entry.session_lock:
            if entry.browser_session is not None:
                # 1. Health check
                if not await entry.browser_session.is_alive():
                    logger.warning("Browser session dead for %s, recovering", entry.session_id)
                    with suppress(Exception):
                        await self._pool.release(entry.session_id)
                    entry.browser_session = None
                    entry.cache.invalidate_all()
                    entry.navigation_count = 0
                else:
                    # 2. Recycle check (D2)
                    reason = self._check_recycle(entry)
                    if reason is not None:
                        logger.info("Recycling browser for %s: %s", entry.session_id, reason)
                        entry.cache.invalidate_all()
                        with suppress(Exception):
                            await self._pool.release(entry.session_id)
                        entry.browser_session = None
                        entry.navigation_count = 0
                        # Telemetry (lazy import preserves acyclic module graph)
                        from pagemap.telemetry import emit, events

                        emit(
                            events.BROWSER_DEAD,
                            events.browser_dead(
                                session_id=entry.session_id,
                                error=f"recycled ({reason})",
                            ),
                        )
                    else:
                        # 3. Tab quota check (D3)
                        if entry.browser_session.tab_count >= MAX_TABS_PER_SESSION:
                            raise ResourceExhaustionError(
                                f"Tab limit exceeded ({entry.browser_session.tab_count}/{MAX_TABS_PER_SESSION}). "
                                "Close unused tabs or start a new session."
                            )
                        entry.navigation_count += 1
                        return entry.browser_session

            # 4. Acquire fresh session (dead, recycled, or first call)
            sess = await self._pool.acquire(entry.session_id)
            try:
                from . import _validate_url

                await sess.install_ssrf_route_guard(_validate_url)
            except Exception:
                with suppress(Exception):
                    await self._pool.release(entry.session_id)
                raise
            entry.browser_session = sess
            entry.browser_acquired_at = time.monotonic()
            entry.navigation_count += 1
            return sess
