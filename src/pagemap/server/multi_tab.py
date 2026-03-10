# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Multi-tab session management for PageMap MCP server.

Ported from Retio MVP ``multi-tab-session.ts``.
Each tab has its own BrowserContext (isolated cookies/storage)
and PageMapCache instance.

Key differences from Retio TS:
- Retio uses native WebView bridges; pagemap uses Playwright ``browser.new_context()``.
- Per-tab cache isolation via ``PageMapCache`` instances (vs WebView JS context isolation).
- SSRF validation reuses pagemap's existing 4-layer ``_validate_url_with_dns``.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum

from playwright.async_api import Browser, Page

from pagemap.cache import PageMapCache

from .browser_session import BrowserConfig, BrowserSession

logger = logging.getLogger("pagemap.server.multi_tab")

# ── Constants ─────────────────────────────────────────────────────────

MAX_TABS = 5
TAB_TTL_SECONDS = 1800  # 30 minutes
TAB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]{1,30}$")


# ── Data models ───────────────────────────────────────────────────────


class TabOpStatus(StrEnum):
    """Status codes for tab operations."""

    OK = "ok"
    ERROR = "error"
    TAB_EXISTS = "tab_exists"
    TAB_NOT_FOUND = "tab_not_found"
    MAX_TABS_REACHED = "max_tabs_reached"
    INVALID_NAME = "invalid_name"
    INVALID_URL = "invalid_url"


@dataclass(slots=True)
class TabInstance:
    """A single tab with its own browser context and cache."""

    name: str
    session: BrowserSession
    cache: PageMapCache
    created_at: float = field(default_factory=time.monotonic)


# ── Cookie validation ─────────────────────────────────────────────────

_COOKIE_ALLOWED_KEYS = frozenset(
    {
        "name",
        "value",
        "domain",
        "path",
        "url",
        "expires",
        "httpOnly",
        "secure",
        "sameSite",
    }
)


def _validate_cookies(cookies: list[dict]) -> str | None:
    """Validate cookie dicts before injecting into a browser context.

    Returns an error message string, or ``None`` if valid.
    """
    for i, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            return f"cookie[{i}] is not a dict"
        unknown = set(cookie.keys()) - _COOKIE_ALLOWED_KEYS
        if unknown:
            return f"cookie[{i}] has unknown keys: {sorted(unknown)}"
        if "name" not in cookie or "value" not in cookie:
            return f"cookie[{i}] missing required 'name' and/or 'value'"
        if "domain" not in cookie and "url" not in cookie:
            return f"cookie[{i}] requires either 'domain' or 'url'"
    return None


# ── MultiTabSession ──────────────────────────────────────────────────


class MultiTabSession:
    """Manages multiple isolated browser tabs sharing a single Browser.

    Each tab gets its own ``BrowserContext`` (via ``BrowserSession.start_from_pool``)
    for cookie/storage isolation, and its own ``PageMapCache``.

    Backward-compatible: when no tabs are open (``is_multi_tab == False``),
    the server falls back to the original single-session mode.
    """

    def __init__(self, browser: Browser) -> None:
        self._browser = browser
        self._tabs: dict[str, TabInstance] = {}
        self._active_tab: str | None = None

    @property
    def is_multi_tab(self) -> bool:
        """True when at least one tab is open."""
        return len(self._tabs) > 0

    @property
    def active_tab_name(self) -> str | None:
        return self._active_tab

    def get_active_tab(self) -> TabInstance | None:
        """Return the active TabInstance, or None."""
        if self._active_tab is None:
            return None
        return self._tabs.get(self._active_tab)

    def get_active_page(self) -> Page | None:
        """Return the active tab's Page, or None."""
        tab = self.get_active_tab()
        if tab is None:
            return None
        return tab.session.page

    def get_active_cache(self) -> PageMapCache | None:
        """Return the active tab's PageMapCache, or None."""
        tab = self.get_active_tab()
        if tab is None:
            return None
        return tab.cache

    def get_active_session(self) -> BrowserSession | None:
        """Return the active tab's BrowserSession, or None."""
        tab = self.get_active_tab()
        if tab is None:
            return None
        return tab.session

    # ── Tab operations ────────────────────────────────────────────

    async def open_tab(
        self,
        name: str,
        url: str,
        *,
        cookies: list[dict] | None = None,
        url_validator=None,
        ssrf_validator=None,
        user_agent: str | None = None,
    ) -> dict:
        """Open a new browser tab with an independent session.

        Args:
            name: Unique tab identifier (alphanumeric + underscore, max 30 chars).
            url: URL to navigate to (http/https only).
            cookies: Pre-authenticated session cookies to inject before navigation.
            url_validator: Sync URL validator for route guard (``_validate_url``).
            ssrf_validator: Async URL validator (``_validate_url_with_dns``).
            user_agent: Custom user-agent string (e.g. BOT_USER_AGENT for --bot-ua).

        Returns:
            dict with ``status``, ``tab_name``, ``url``, and ``tab_count``.
        """
        # Auto-prune expired tabs before opening new ones
        await self.prune_expired_tabs()

        # Validate name
        if not TAB_NAME_PATTERN.match(name):
            return {
                "status": TabOpStatus.INVALID_NAME,
                "error": f"Tab name must match {TAB_NAME_PATTERN.pattern} (alphanumeric + underscore, 1-30 chars).",
            }

        # Check duplicate
        if name in self._tabs:
            return {
                "status": TabOpStatus.TAB_EXISTS,
                "error": f"Tab '{name}' already exists. Use switch_tab to activate it.",
            }

        # Check limit
        if len(self._tabs) >= MAX_TABS:
            return {
                "status": TabOpStatus.MAX_TABS_REACHED,
                "error": f"Maximum {MAX_TABS} tabs reached. Close a tab first.",
            }

        # Cookie validation (before resource allocation)
        if cookies:
            err = _validate_cookies(cookies)
            if err:
                return {"status": TabOpStatus.ERROR, "error": f"Invalid cookies: {err}"}

        # SSRF validation
        if ssrf_validator is not None:
            ssrf_error = await ssrf_validator(url)
            if ssrf_error:
                return {
                    "status": TabOpStatus.INVALID_URL,
                    "error": f"URL blocked: {ssrf_error}",
                }

        # Create isolated browser session (new BrowserContext)
        config = BrowserConfig(headless=True, user_agent=user_agent) if user_agent else BrowserConfig(headless=True)
        tab_session = BrowserSession(config)
        await tab_session.start_from_pool(self._browser)

        try:
            # Install SSRF route guard on the new context
            if url_validator is not None:
                await tab_session.install_ssrf_route_guard(url_validator)

            # Inject cookies before navigation
            if cookies:
                await tab_session.context.add_cookies(cookies)

            # Navigate
            await tab_session.navigate(url)
        except Exception:
            with suppress(Exception):
                await tab_session.stop()
            raise

        # Create tab
        tab = TabInstance(
            name=name,
            session=tab_session,
            cache=PageMapCache(),
        )
        self._tabs[name] = tab
        self._active_tab = name

        logger.info("Tab opened: name=%s url=%s total=%d", name, url, len(self._tabs))

        return {
            "status": TabOpStatus.OK,
            "tab_name": name,
            "url": url,
            "tab_count": len(self._tabs),
        }

    async def switch_tab(self, name: str) -> dict:
        """Switch the active tab.

        Returns:
            dict with ``status``, ``tab_name``, and ``url``.
        """
        # Auto-prune expired tabs
        await self.prune_expired_tabs()

        if name not in self._tabs:
            return {
                "status": TabOpStatus.TAB_NOT_FOUND,
                "error": f"Tab '{name}' not found. Available: {list(self._tabs.keys())}",
            }

        self._active_tab = name
        tab = self._tabs[name]
        url = await tab.session.get_page_url()

        logger.info("Tab switched: name=%s", name)

        return {
            "status": TabOpStatus.OK,
            "tab_name": name,
            "url": url,
        }

    def list_tabs(self) -> dict:
        """List all open tabs with their current state.

        Returns:
            dict with ``status``, ``active_tab``, ``tab_count``, and ``tabs`` list.
        """
        tabs_info = []
        now = time.monotonic()
        for name, tab in self._tabs.items():
            tabs_info.append(
                {
                    "name": name,
                    "is_active": name == self._active_tab,
                    "age_seconds": round(now - tab.created_at),
                    "has_page_map": tab.cache.active is not None,
                }
            )

        return {
            "status": TabOpStatus.OK,
            "active_tab": self._active_tab,
            "tab_count": len(self._tabs),
            "tabs": tabs_info,
        }

    async def close_tab(self, name: str) -> dict:
        """Close a tab and release its browser context.

        If the closed tab is the active tab, auto-switches to the next available tab.

        Returns:
            dict with ``status``, ``closed_tab``, ``tab_count``, and ``active_tab``.
        """
        if name not in self._tabs:
            return {
                "status": TabOpStatus.TAB_NOT_FOUND,
                "error": f"Tab '{name}' not found. Available: {list(self._tabs.keys())}",
            }

        tab = self._tabs.pop(name)
        tab.cache.invalidate_all()

        # Close the browser context (BrowserSession.stop closes context only in pool mode)
        try:
            await tab.session.stop()
        except Exception:
            logger.debug("Error closing tab session for '%s'", name, exc_info=True)

        # Auto-switch if active tab was closed
        if self._active_tab == name:
            if self._tabs:
                self._active_tab = next(iter(self._tabs))
            else:
                self._active_tab = None

        logger.info(
            "Tab closed: name=%s remaining=%d active=%s",
            name,
            len(self._tabs),
            self._active_tab,
        )

        return {
            "status": TabOpStatus.OK,
            "closed_tab": name,
            "tab_count": len(self._tabs),
            "active_tab": self._active_tab,
        }

    async def prune_expired_tabs(self) -> list[str]:
        """Close tabs that have exceeded TTL. Returns list of pruned tab names."""
        now = time.monotonic()
        expired = [name for name, tab in self._tabs.items() if now - tab.created_at > TAB_TTL_SECONDS]
        for name in expired:
            await self.close_tab(name)
            logger.info("Tab pruned (TTL expired): name=%s", name)
        return expired

    async def close_all(self) -> None:
        """Close all tabs. Used during shutdown / dispose."""
        names = list(self._tabs.keys())
        for name in names:
            await self.close_tab(name)
        logger.info("All tabs closed")
