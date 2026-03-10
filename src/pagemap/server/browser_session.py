# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Playwright browser session management for Page Map.

Manages Chromium lifecycle, CDP sessions, and anti-automation flags.
Supports both live browsing and offline HTML loading.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Dialog,
    Page,
    Playwright,
    Route,
    async_playwright,
)

from pagemap.errors import BrowserError
from pagemap.i18n import accept_language_for_url
from pagemap.interactive_detector import _CDP_AX_TREE_TIMEOUT

logger = logging.getLogger(__name__)

# Default browser config
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

# S3: Dangerous URL schemes blocked at context level.
# about:blank is explicitly allowed (used by SSRF reset in server.py).
BLOCKED_URL_SCHEMES = (
    "chrome://",
    "devtools://",
    "chrome-extension://",
    "file://",
    "view-source://",
    "blob:",
    "data:",
)
DEFAULT_LOCALE = "en-US"

_MAX_DIALOG_BUFFER = 10


@dataclass(frozen=True)
class DialogInfo:
    """Record of a JS dialog that was auto-handled."""

    dialog_type: str  # "alert", "confirm", "prompt", "beforeunload"
    message: str
    dismissed: bool  # True=dismiss, False=accept


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

try:
    from importlib.metadata import version as _pkg_version

    _PAGEMAP_VERSION = _pkg_version("retio-pagemap")
except Exception:
    _PAGEMAP_VERSION = "unknown"

BOT_USER_AGENT = f"PageMapBot/{_PAGEMAP_VERSION} (+https://github.com/Retio-ai/pagemap)"


# Re-export from canonical location for backward compatibility.
from pagemap.dom_converters import _cdp_ax_nodes_to_tree as _cdp_ax_nodes_to_tree  # noqa: F401, E402


@dataclass
class BrowserConfig:
    """Browser launch configuration."""

    headless: bool = True
    locale: str = DEFAULT_LOCALE
    viewport_width: int = 1280
    viewport_height: int = 800
    user_agent: str = DEFAULT_USER_AGENT
    timeout_ms: int = 30000
    wait_until: str = "load"
    settle_quiet_ms: int = 200  # DOM mutation quiet period (ms)
    settle_max_ms: int = 3000  # Maximum settle wait (ms)
    wait_strategy: str = "hybrid"  # "hybrid" | "networkidle" | "load"
    networkidle_budget_ms: int = 6000  # hybrid mode: networkidle attempt budget


@dataclass(frozen=True, slots=True)
class NavigationResult:
    """Result of a page navigation with strategy metadata."""

    strategy: str  # "networkidle" | "load+settle" | "load"
    settle_metrics: dict | None  # DOM settle: {waited_ms, mutations, reason}
    http_status: int | None = None  # HTTP response status code


_BROWSER_DEAD_PATTERNS = (
    "target closed",
    "target page",
    "browser has been closed",
    "connection closed",
    "browser disconnected",
)


def _is_browser_dead_error(exc: Exception) -> bool:
    """Detect browser crash/disconnect errors."""
    msg = str(exc).lower()
    return any(p in msg for p in _BROWSER_DEAD_PATTERNS)


# ── Chromium auto-install ─────────────────────────────────────────

_chromium_install_attempted = False
_AUTO_INSTALL_TIMEOUT = 300  # seconds — Chromium ~140MB download


async def _auto_install_chromium() -> bool:
    """Run ``playwright install chromium`` once per process.

    Returns True if install succeeded, False otherwise.
    stdout/stderr are captured to avoid polluting the MCP STDIO stream.
    """
    global _chromium_install_attempted  # noqa: PLW0603
    if _chromium_install_attempted:
        return False
    _chromium_install_attempted = True

    logger.info("Chromium not found — running 'playwright install chromium' …")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_AUTO_INSTALL_TIMEOUT)
        if proc.returncode == 0:
            logger.info("Chromium installed successfully")
            return True
        logger.warning(
            "playwright install chromium failed (rc=%d): %s",
            proc.returncode,
            stderr.decode(errors="replace")[:500],
        )
        return False
    except TimeoutError:
        logger.warning("Chromium install timed out after %ds", _AUTO_INSTALL_TIMEOUT)
        return False
    except Exception:
        logger.warning("Chromium auto-install failed", exc_info=True)
        return False


def chromium_launch_args(config: BrowserConfig) -> list[str]:
    """Return hardened Chromium launch arguments.

    Shared by both ``BrowserSession`` and ``BrowserPool`` to avoid
    duplicating the argument list (DRY).

    When ``PAGEMAP_CONTAINER_MODE=1`` (set by docker-compose / k8s), adds
    ``--no-sandbox`` because gVisor provides the outer sandbox.
    """
    args = [
        "--disable-blink-features=AutomationControlled",
        f"--lang={config.locale}",
        "--disable-extensions",
        "--disable-plugins",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-gpu",
        "--no-first-run",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=ServiceWorker,WebRtcHideLocalIpsWithMdns",
        "--deny-permission-prompts",
        "--disable-breakpad",
        "--no-pings",
        "--disable-domain-reliability",
        "--disable-component-update",
        "--disable-client-side-phishing-detection",
        "--disable-external-intent-requests",
        "--noerrdialogs",
        "--disable-prompt-on-repost",
    ]
    # In container mode (gVisor/k8s), Chromium sandbox is redundant — gVisor
    # provides the outer sandbox.  --no-sandbox is *required* for non-root
    # execution inside containers with seccomp + gVisor.
    if os.environ.get("PAGEMAP_CONTAINER_MODE") == "1":
        args.append("--no-sandbox")
    return args


_JS_BUNDLE_DIR = Path(__file__).resolve().parent.parent / "data" / "js"

# Feature flag: stealth defenses (default ON)
_STEALTH_ENABLED = os.environ.get("PAGEMAP_STEALTH_ENABLED", "1") == "1"


@lru_cache(maxsize=4)
def _load_js_bundle(name: str) -> str | None:
    """Load a compiled JS bundle from data/js/. Returns None if missing."""
    path = _JS_BUNDLE_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("JS bundle not found: %s", path)
        return None


class BrowserSession:
    """Manages a Playwright browser session with CDP access."""

    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp_session: CDPSession | None = None
        self._pending_dialogs: list[DialogInfo] = []
        self._pending_new_page: Page | None = None
        self._batch_pages: set[Page] = set()
        self._owns_browser: bool = True  # False when created via start_from_pool()
        self._scanner_cdp_session: CDPSession | None = None
        self._scanner_script_id: str | None = None
        self._scanner_context_id: int | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser session not started. Use async with or call start().")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser session not started.")
        return self._context

    @property
    def tab_count(self) -> int:
        """Number of open pages (tabs) in the browser context."""
        if self._context is None:
            return 0
        return len(self._context.pages)

    async def is_alive(self, timeout: float = 5.0) -> bool:
        """Check if the browser process is responsive (2-stage health check)."""
        # Stage 1: synchronous connection check (Playwright API)
        if self._browser is None or not self._browser.is_connected():
            return False
        # Stage 2: async page responsiveness check
        if self._page is None:
            return False
        try:
            await asyncio.wait_for(self._page.evaluate("1"), timeout=timeout)
            return True
        except Exception:
            return False

    def _chromium_launch_args(self) -> list[str]:
        """Return hardened Chromium launch arguments."""
        return chromium_launch_args(self.config)

    async def _launch_browser(self) -> None:
        """Launch Chromium, auto-installing on first 'executable not found' error."""
        args = self._chromium_launch_args()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=self.config.headless,
                args=args,
            )
        except Exception as exc:
            if "executable doesn't exist" in str(exc).lower():
                if await _auto_install_chromium():
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.config.headless,
                        args=args,
                    )
                else:
                    raise BrowserError(
                        "Chromium is not installed and auto-install failed. Please run: playwright install chromium"
                    ) from exc
            else:
                raise

    async def _create_context(self, browser: Browser) -> None:
        """Create BrowserContext + Page + event handlers on given browser."""
        # D1: isolation verified — accept_downloads=False, service_workers="block",
        # permissions=[] ensure each context is sandboxed.
        self._context = await browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
            locale=self.config.locale,
            user_agent=self.config.user_agent,
            # S3: Block ServiceWorker registration (prevents SSRF route guard bypass)
            service_workers="block",
            # S3: Deny all permissions (geo, camera, mic, notifications, etc.)
            permissions=[],
            # S3: Prevent file downloads
            accept_downloads=False,
            # Default Accept-Language (overridden per-URL in navigate())
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        # Auto-handle JS dialogs (alert/confirm/prompt/beforeunload)
        self._context.on("dialog", self._on_dialog)
        # Handle popups/new tabs: auto-track for consume_new_page()
        self._context.on("page", self._on_new_page)

        # Stealth: main world, all frames (before page creation)
        await self._install_stealth()

        self._page = await self._context.new_page()

        # Security Scanner: CDP isolated world, main frame only
        await self._install_security_scanner()

        # S3: Block dangerous URL schemes at context level (covers all pages)
        await self._install_scheme_block_route()

    async def start(self) -> None:
        """Launch browser and create initial page."""
        self._playwright = await async_playwright().start()
        await self._launch_browser()
        await self._create_context(self._browser)
        logger.info("Browser session started (headless=%s)", self.config.headless)

    async def start_from_pool(self, browser: Browser) -> None:
        """Start session using a shared browser (pool mode).

        The browser is owned by the pool — stop() will only close the
        context, not the browser or playwright instance.
        """
        self._owns_browser = False
        self._browser = browser
        await self._create_context(browser)
        logger.info("Browser session started from pool (headless=%s)", self.config.headless)

    async def _install_scheme_block_route(self) -> None:
        """Block dangerous URL schemes at context level (covers all pages).

        about:blank is explicitly allowed (used by SSRF reset in server.py).
        """

        async def _handler(route: Route) -> None:
            url = route.request.url
            if url == "about:blank":
                await route.continue_()
                return
            for scheme in BLOCKED_URL_SCHEMES:
                if url.startswith(scheme):
                    logger.debug("Scheme blocked: %s", url)
                    await route.abort("blockedbyclient")
                    return
            if url.startswith("about:"):
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await self._context.route("**/*", _handler)

    async def _install_stealth(self) -> None:
        """Stealth defenses — main world, all frames."""
        if not _STEALTH_ENABLED:
            return
        stealth_js = _load_js_bundle("stealth_bundle.js")
        if not stealth_js:
            return
        seed = secrets.randbits(32)
        await self._context.add_init_script(script=f"window.__pagemap_stealth_seed = {seed};")
        await self._context.add_init_script(script=stealth_js)
        logger.debug("Stealth bundle injected (seed=%d)", seed)

    async def _install_security_scanner(self) -> None:
        """Security scanner — CDP isolated world, main frame only."""
        try:
            from pagemap.security import SECURITY_ADVANCED_ENABLED

            if not SECURITY_ADVANCED_ENABLED:
                return
        except ImportError:
            return

        scanner_js = _load_js_bundle("security_scanner.js")
        if not scanner_js:
            return

        try:
            self._scanner_cdp_session = await self._context.new_cdp_session(self._page)

            # Listen for execution context creation to capture the isolated world context ID
            def _on_context_created(params: dict) -> None:
                ctx = params.get("context", {})
                if ctx.get("name") == "PageMapSecurity":
                    self._scanner_context_id = ctx.get("id")

            self._scanner_cdp_session.on("Runtime.executionContextCreated", _on_context_created)
            await self._scanner_cdp_session.send("Runtime.enable", {})

            result = await self._scanner_cdp_session.send(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": scanner_js, "worldName": "PageMapSecurity"},
            )
            self._scanner_script_id = result.get("identifier")
            logger.debug("Security scanner injected (world=PageMapSecurity)")
        except Exception:
            logger.debug("Security scanner CDP injection failed", exc_info=True)
            self._scanner_cdp_session = None

    async def read_security_report(self) -> dict | None:
        """Read security scanner report from CDP isolated world.

        Returns the raw JS report dict, or None if unavailable.
        Must be called BEFORE DOM Guard removes hidden elements.
        """
        if not self._scanner_cdp_session:
            return None

        try:
            # Resolve context ID for the isolated world if not yet captured
            if self._scanner_context_id is None:
                # Context might not have been created yet (no navigation).
                # Try creating an isolated world explicitly as fallback.
                try:
                    frame_tree = await self._scanner_cdp_session.send("Page.getFrameTree", {})
                    main_frame_id = frame_tree.get("frameTree", {}).get("frame", {}).get("id")
                    if main_frame_id:
                        world = await self._scanner_cdp_session.send(
                            "Page.createIsolatedWorld",
                            {"frameId": main_frame_id, "worldName": "PageMapSecurity"},
                        )
                        self._scanner_context_id = world.get("executionContextId")
                except Exception:  # nosec B110 — best-effort fallback, failure is handled below
                    pass

            if self._scanner_context_id is None:
                return None

            eval_params: dict = {
                "expression": "JSON.stringify(window.__pagemap_security_report)",
                "contextId": self._scanner_context_id,
                "returnByValue": True,
            }
            result = await self._scanner_cdp_session.send("Runtime.evaluate", eval_params)
            value = result.get("result", {}).get("value")
            if value and isinstance(value, str):
                return json.loads(value)
        except Exception:
            logger.debug("Failed to read security report", exc_info=True)
        return None

    async def read_mutation_severity(self) -> int:
        """Read and atomically reset the DOM mutation severity from isolated world.

        Returns:
            0 = no mutations, 1 = normal, 2 = critical (script/iframe/form-action).
        Fail-open: returns 0 on any error.
        """
        if not self._scanner_cdp_session or self._scanner_context_id is None:
            return 0
        try:
            result = await self._scanner_cdp_session.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(() => { const s = window.__pagemap_mutation_severity || 0;"
                        " window.__pagemap_mutation_severity = 0; return s; })()"
                    ),
                    "contextId": self._scanner_context_id,
                    "returnByValue": True,
                },
            )
            val = result.get("result", {}).get("value")
            return int(val) if isinstance(val, (int, float)) and 0 <= val <= 2 else 0
        except Exception:  # nosec B110 — fail-open
            return 0

    async def install_ssrf_route_guard(self, url_validator: Callable[[str], str | None]) -> None:
        """Install a context-level route guard to block SSRF via JS-initiated navigation.

        Intercepts document/subdocument requests and validates their URLs
        using the provided sync validator. Image/script/stylesheet requests
        are allowed through for performance.

        Args:
            url_validator: Sync function that returns None if URL is safe,
                          or an error message string if blocked.
        """

        async def _route_handler(route: Route) -> None:
            request = route.request
            resource_type = request.resource_type
            # Only validate document navigations (page, iframe)
            if resource_type in ("document", "subdocument"):
                error = url_validator(request.url)
                if error:
                    logger.warning(
                        "Route guard blocked: url=%s type=%s reason=%s",
                        request.url,
                        resource_type,
                        error,
                    )
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await self.context.route("**/*", _route_handler)
        logger.info("SSRF route guard installed on browser context")

    async def stop(self) -> None:
        """Close browser and clean up. Safe to call on a crashed browser.

        Pool mode (_owns_browser=False): closes context only, leaves browser/playwright alone.
        Standalone mode (_owns_browser=True): closes everything (existing behaviour).
        """
        # Clean up batch pages first
        for page in list(self._batch_pages):
            if not page.is_closed():
                with suppress(Exception):
                    await page.close()
        self._batch_pages.clear()

        if self._cdp_session:
            with suppress(Exception):
                await self._cdp_session.detach()
            self._cdp_session = None

        # Close context (common to both modes)
        if self._context:
            with suppress(Exception):
                await self._context.close()
            self._context = None

        self._page = None
        self._pending_dialogs = []
        self._pending_new_page = None

        if self._owns_browser:
            # Standalone mode: close browser + playwright
            if self._browser:
                with suppress(Exception):
                    await self._browser.close()
                self._browser = None
            if self._playwright:
                with suppress(Exception):
                    await self._playwright.stop()
                self._playwright = None
        else:
            # Pool mode: release reference only, pool owns the browser
            self._browser = None

        logger.info("Browser session stopped (owned_browser=%s)", self._owns_browser)

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.stop()

    async def _on_dialog(self, dialog: Dialog) -> None:
        """Auto-handle JS dialogs with guaranteed accept/dismiss.

        Policy: alert/beforeunload → accept, confirm/prompt → dismiss.
        CRITICAL: Must ALWAYS call accept() or dismiss() — failure freezes page.
        """
        try:
            dtype = dialog.type
            message = dialog.message
            if dtype in ("alert", "beforeunload"):
                await dialog.accept()
                dismissed = False
            else:  # confirm, prompt
                await dialog.dismiss()
                dismissed = True
            info = DialogInfo(dialog_type=dtype, message=message, dismissed=dismissed)
            self._pending_dialogs.append(info)
            if len(self._pending_dialogs) > _MAX_DIALOG_BUFFER:
                self._pending_dialogs = self._pending_dialogs[-_MAX_DIALOG_BUFFER:]
            action_word = "dismissed" if dismissed else "accepted"
            logger.info(
                "JS dialog auto-handled: type=%s action=%s message=%.100s",
                dtype,
                action_word,
                message,
            )
        except Exception:
            logger.warning("JS dialog handler failed, attempting dismiss fallback", exc_info=True)
            with suppress(Exception):
                await dialog.dismiss()

    def drain_dialogs(self) -> list[DialogInfo]:
        """Return and clear pending dialog records (atomic, no lock needed)."""
        dialogs = self._pending_dialogs
        self._pending_dialogs = []
        return dialogs

    async def _on_new_page(self, page: Page) -> None:
        """Handle new page/popup opened in browser context.

        Stores the latest popup for consumption by consume_new_page().
        Closes any previously unconsumed popup (single-page model).
        Skips batch-managed pages.
        """
        if page in getattr(self, "_batch_pages", set()):
            return  # batch-managed page — skip popup handler
        old = self._pending_new_page
        self._pending_new_page = page
        if old is not None and not old.is_closed():
            with suppress(Exception):
                await old.close()
            logger.debug("Unclaimed popup closed: %s", old.url)
        logger.info("New page/popup detected: %s", page.url)

    def consume_new_page(self) -> Page | None:
        """Return and clear the pending new page (if any)."""
        page = self._pending_new_page
        self._pending_new_page = None
        return page

    async def switch_page(self, new_page: Page) -> None:
        """Switch to a new page, detaching CDP and closing the old page."""
        old_page = self._page
        if self._cdp_session is not None:
            with suppress(Exception):
                await self._cdp_session.detach()
            self._cdp_session = None
        self._page = new_page
        if old_page is not None and not old_page.is_closed():
            with suppress(Exception):
                await old_page.close()
        logger.info("Switched to new page: %s", new_page.url)

    async def create_batch_page(self) -> Page:
        """Create a new page for batch processing."""
        page = await self.context.new_page()
        self._batch_pages.add(page)
        return page

    async def close_batch_page(self, page: Page) -> None:
        """Close a batch-managed page."""
        self._batch_pages.discard(page)
        if not page.is_closed():
            with suppress(Exception):
                await page.close()

    async def wait_for_dom_settle_on(
        self,
        page: Page,
        quiet_ms: int | None = None,
        max_ms: int | None = None,
    ) -> dict | None:
        """Wait for DOM mutations to settle on a specific page (batch use)."""
        q = quiet_ms if quiet_ms is not None else self.config.settle_quiet_ms
        m = max_ms if max_ms is not None else self.config.settle_max_ms
        try:
            return await page.evaluate(_DOM_SETTLE_JS, [q, m])
        except Exception:
            return None

    async def navigate(self, url: str) -> NavigationResult:
        """Navigate to a URL with hybrid wait strategy.

        Strategies:
        - "networkidle": legacy — goto with networkidle wait
        - "load": goto with load event only
        - "hybrid" (default): goto with load, then attempt networkidle
          within budget; falls back to load+settle on timeout/error
        """
        import contextlib
        from urllib.parse import urlparse

        # Clear cookies/storage when switching domains
        current_url = self.page.url
        if current_url and current_url != "about:blank":
            current_host = urlparse(current_url).hostname or ""
            new_host = urlparse(url).hostname or ""
            if current_host and new_host and current_host != new_host:
                await self.context.clear_cookies()

        # Set Accept-Language matching the target site's locale.
        # NOTE: set_extra_http_headers() REPLACES all extra headers.
        # If other extra headers are added in the future, merge them here.
        accept_lang = accept_language_for_url(url)
        await self.context.set_extra_http_headers({"Accept-Language": accept_lang})

        strategy = self.config.wait_strategy

        if strategy == "networkidle":
            # Legacy path
            response = await self.page.goto(url, wait_until="networkidle", timeout=self.config.timeout_ms)
            settle = await self.wait_for_dom_settle()
            return NavigationResult(
                strategy="networkidle",
                settle_metrics=settle,
                http_status=response.status if response else None,
            )

        # Step 1: goto with "load" (window load event)
        response = await self.page.goto(url, wait_until="load", timeout=self.config.timeout_ms)

        # Step 2: networkidle attempt (hybrid only, asyncio.wait for safe cancellation)
        used_strategy = "load"
        if strategy == "hybrid":
            idle_task = asyncio.ensure_future(self.page.wait_for_load_state("networkidle"))
            done, _pending = await asyncio.wait(
                {idle_task},
                timeout=self.config.networkidle_budget_ms / 1000,
            )
            if idle_task in done:
                exc = idle_task.exception()
                if exc is None:
                    used_strategy = "networkidle"
                    logger.debug("networkidle achieved within budget")
                elif _is_browser_dead_error(exc):
                    raise exc  # browser death propagates
                else:
                    used_strategy = "load+settle"
                    logger.debug("networkidle completed with error: %s", exc)
            else:
                idle_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await idle_task
                used_strategy = "load+settle"
                logger.info(
                    "networkidle budget exceeded (%.1fs), proceeding with load+settle",
                    self.config.networkidle_budget_ms / 1000,
                )

        # Step 3: DOM settle (always)
        settle = await self.wait_for_dom_settle()
        return NavigationResult(
            strategy=used_strategy,
            settle_metrics=settle,
            http_status=response.status if response else None,
        )

    async def load_html(self, html: str, base_url: str = "about:blank") -> None:
        """Load raw HTML content directly (offline mode)."""
        await self.page.set_content(html, wait_until="domcontentloaded")

    async def get_cdp_session(self) -> CDPSession:
        """Get or create a CDP session for the current page."""
        if self._cdp_session is None:
            self._cdp_session = await self.context.new_cdp_session(self.page)
        return self._cdp_session

    async def wait_for_dom_settle(
        self,
        quiet_ms: int | None = None,
        max_ms: int | None = None,
    ) -> dict | None:
        """Wait for DOM mutations to settle using MutationObserver.

        Returns:
            Metrics dict {"waited_ms": int, "mutations": int, "reason": "quiet"|"timeout"}
            or None if page.evaluate failed (crash, navigation, etc.).
        """
        q = quiet_ms if quiet_ms is not None else self.config.settle_quiet_ms
        m = max_ms if max_ms is not None else self.config.settle_max_ms
        try:
            result = await self.page.evaluate(_DOM_SETTLE_JS, [q, m])
            logger.debug(
                "DOM settle: %dms, %d mutations, reason=%s",
                result.get("waited_ms", 0),
                result.get("mutations", 0),
                result.get("reason", "unknown"),
            )
            return result
        except Exception:
            logger.debug("DOM settle failed, continuing", exc_info=True)
            return None

    async def get_ax_tree(self, interesting_only: bool = False) -> dict | None:
        """Get the accessibility tree snapshot via CDP.

        Reuses the cached CDP session (get_cdp_session). On stale-session
        errors, reconnects once and retries.
        """
        for attempt in range(2):
            cdp = await self.get_cdp_session()
            try:
                async with asyncio.timeout(_CDP_AX_TREE_TIMEOUT):
                    result = await cdp.send("Accessibility.getFullAXTree")
                nodes = result.get("nodes", [])
                if not nodes:
                    return None
                return _cdp_ax_nodes_to_tree(nodes)
            except Exception:
                if attempt == 0:
                    logger.warning("CDP session stale in get_ax_tree, reconnecting")
                    with suppress(Exception, asyncio.CancelledError):
                        await asyncio.shield(cdp.detach())
                    self._cdp_session = None
                    continue
                raise
        return None  # unreachable; satisfies type checker

    async def get_page_html(self) -> str:
        """Get the current page's full HTML."""
        return await self.page.content()

    async def get_page_url(self) -> str:
        """Get the current page URL."""
        return self.page.url

    async def get_page_title(self) -> str:
        """Get the current page title."""
        return await self.page.title()

    async def click_element(self, xpath: str) -> None:
        """Click an element by XPath."""
        locator = self.page.locator(f"xpath={xpath}")
        await locator.click(timeout=5000)
        await self.wait_for_dom_settle(max_ms=2000)

    async def type_text(self, xpath: str, text: str) -> None:
        """Type text into an element by XPath."""
        locator = self.page.locator(f"xpath={xpath}")
        await locator.fill(text, timeout=5000)

    async def select_option(self, xpath: str, value: str) -> None:
        """Select an option in a dropdown by XPath."""
        locator = self.page.locator(f"xpath={xpath}")
        await locator.select_option(value, timeout=5000)

    async def press_key(self, key: str) -> None:
        """Press a keyboard key."""
        await self.page.keyboard.press(key)

    async def screenshot(self, path: str | Path | None = None) -> bytes:
        """Take a screenshot."""
        return await self.page.screenshot(path=str(path) if path else None)

    async def go_back(self, wait_until: str = "load", timeout_ms: int = 30000) -> str | None:
        """Navigate back in browser history.

        Returns the new URL after navigation, or None if no history entry.
        """
        response = await self.page.go_back(wait_until=wait_until, timeout=timeout_ms)
        if response is None:
            # No previous page in history
            return None
        await self.wait_for_dom_settle(max_ms=2000)
        return self.page.url

    async def get_scroll_position(self) -> dict:
        """Get current scroll position and page dimensions."""
        return await self.page.evaluate(_SCROLL_POSITION_JS)

    async def scroll(self, delta_x: int = 0, delta_y: int = 0) -> dict:
        """Scroll the page by the given deltas.

        Uses parameterized evaluate (not f-string) for injection safety.
        Returns scroll position after scrolling.
        """
        await self.page.evaluate("([dx, dy]) => window.scrollBy(dx, dy)", [delta_x, delta_y])
        await self.wait_for_dom_settle(max_ms=1500)
        return await self.page.evaluate(_SCROLL_POSITION_JS)


# ── Scroll position JS (static, no interpolation) ──────────────────

_SCROLL_POSITION_JS = """() => ({
    scrollX: Math.round(window.scrollX),
    scrollY: Math.round(window.scrollY),
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    clientWidth: document.documentElement.clientWidth,
    clientHeight: document.documentElement.clientHeight,
})"""

_DOM_SETTLE_JS = """([quietMs, maxMs]) => new Promise(resolve => {
  let mutations = 0;
  let quietTimer = null;
  let maxTimer = null;
  const start = performance.now();

  const finish = (reason) => {
    observer.disconnect();
    if (quietTimer) clearTimeout(quietTimer);
    if (maxTimer) clearTimeout(maxTimer);
    resolve({
      waited_ms: Math.round(performance.now() - start),
      mutations: mutations,
      reason: reason
    });
  };

  const resetQuiet = () => {
    if (quietTimer) clearTimeout(quietTimer);
    quietTimer = setTimeout(() => finish('quiet'), quietMs);
  };

  const observer = new MutationObserver((records) => {
    mutations += records.length;
    resetQuiet();
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true
  });

  resetQuiet();
  maxTimer = setTimeout(() => finish('timeout'), maxMs);
})"""


@asynccontextmanager
async def create_session(
    config: BrowserConfig | None = None,
) -> AsyncGenerator[BrowserSession, None]:
    """Context manager to create and manage a browser session."""
    session = BrowserSession(config)
    await session.start()
    try:
        yield session
    finally:
        await session.stop()
