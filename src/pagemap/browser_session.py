"""Playwright browser session management for Page Map.

Manages Chromium lifecycle, CDP sessions, and anti-automation flags.
Supports both live browsing and offline HTML loading.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
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
DEFAULT_LOCALE = "ko-KR"

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


def _cdp_ax_nodes_to_tree(nodes: list[dict]) -> dict | None:
    """Convert CDP Accessibility.getFullAXTree flat node list to a nested tree.

    Matches the format of the old Playwright page.accessibility.snapshot():
    {"role": "...", "name": "...", "value": "...", "focused": false, "children": [...]}
    """
    if not nodes:
        return None

    node_map: dict[str, dict] = {}
    for n in nodes:
        node_id = n.get("nodeId", "")
        role_obj = n.get("role", {})
        name_obj = n.get("name", {})
        role = role_obj.get("value", "") if isinstance(role_obj, dict) else str(role_obj)
        name = name_obj.get("value", "") if isinstance(name_obj, dict) else str(name_obj)

        # Extract properties
        value = ""
        focused = False
        for prop in n.get("properties", []):
            prop_name = prop.get("name", "")
            prop_val = prop.get("value", {})
            v = prop_val.get("value", "") if isinstance(prop_val, dict) else prop_val
            if prop_name == "value":
                value = str(v)
            elif prop_name == "focused":
                focused = bool(v)

        tree_node = {
            "role": role,
            "name": name,
            "value": value,
            "focused": focused,
            "children": [],
            "backendDOMNodeId": n.get("backendDOMNodeId"),
        }
        node_map[node_id] = tree_node

    # Build parent-child relationships
    for n in nodes:
        node_id = n.get("nodeId", "")
        child_ids = n.get("childIds", [])
        parent_node = node_map.get(node_id)
        if parent_node:
            for cid in child_ids:
                child_node = node_map.get(cid)
                if child_node:
                    parent_node["children"].append(child_node)

    # Root is the first node
    root_id = nodes[0].get("nodeId", "")
    return node_map.get(root_id)


@dataclass
class BrowserConfig:
    """Browser launch configuration."""

    headless: bool = True
    locale: str = DEFAULT_LOCALE
    viewport_width: int = 1280
    viewport_height: int = 800
    user_agent: str = DEFAULT_USER_AGENT
    timeout_ms: int = 30000
    wait_until: str = "networkidle"


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

    async def start(self) -> None:
        """Launch browser and create initial page."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--lang={self.config.locale}",
                # Core isolation
                "--disable-extensions",
                "--disable-plugins",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-gpu",
                "--no-first-run",
                # Popups handled by context.on("page") → auto-switch
                # S3: WebRTC IP leak prevention
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                # S3: Disable dangerous features (single flag — last wins)
                "--disable-features=ServiceWorker,WebRtcHideLocalIpsWithMdns",
                # S3: Auto-deny permission prompts (camera, geo, mic, etc.)
                "--deny-permission-prompts",
                # S3: Suppress crash/telemetry outbound calls
                "--disable-breakpad",
                "--no-pings",
                "--disable-domain-reliability",
                "--disable-component-update",
                "--disable-client-side-phishing-detection",
                # S3: Block external app/intent deep links
                "--disable-external-intent-requests",
                # S3: No error/repost dialogs in headless
                "--noerrdialogs",
                "--disable-prompt-on-repost",
            ],
        )
        self._context = await self._browser.new_context(
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
        )
        # Auto-handle JS dialogs (alert/confirm/prompt/beforeunload)
        self._context.on("dialog", self._on_dialog)
        # Handle popups/new tabs: auto-track for consume_new_page()
        self._context.on("page", self._on_new_page)

        self._page = await self._context.new_page()

        # S3: Block dangerous URL schemes at context level (covers all pages)
        await self._install_scheme_block_route()

        logger.info("Browser session started (headless=%s)", self.config.headless)

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
        """Close browser and clean up. Safe to call on a crashed browser."""
        if self._cdp_session:
            with suppress(Exception):
                await self._cdp_session.detach()
            self._cdp_session = None
        if self._browser:
            with suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright:
            with suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
        self._page = None
        self._context = None
        self._pending_dialogs = []
        self._pending_new_page = None
        logger.info("Browser session stopped")

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
        """
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

    async def navigate(self, url: str) -> None:
        """Navigate to a URL and wait for load."""
        from urllib.parse import urlparse

        # Clear cookies/storage when switching domains
        current_url = self.page.url
        if current_url and current_url != "about:blank":
            current_host = urlparse(current_url).hostname or ""
            new_host = urlparse(url).hostname or ""
            if current_host and new_host and current_host != new_host:
                await self.context.clear_cookies()

        await self.page.goto(
            url,
            wait_until=self.config.wait_until,
            timeout=self.config.timeout_ms,
        )
        # Extra settle time for dynamic content
        await self.page.wait_for_timeout(1500)

    async def load_html(self, html: str, base_url: str = "about:blank") -> None:
        """Load raw HTML content directly (offline mode)."""
        await self.page.set_content(html, wait_until="domcontentloaded")

    async def get_cdp_session(self) -> CDPSession:
        """Get or create a CDP session for the current page."""
        if self._cdp_session is None:
            self._cdp_session = await self.context.new_cdp_session(self.page)
        return self._cdp_session

    async def get_ax_tree(self, interesting_only: bool = False) -> dict | None:
        """Get the accessibility tree snapshot via CDP.

        Returns a tree dict matching the old Playwright accessibility.snapshot() format:
        {"role": "...", "name": "...", "children": [...], ...}
        """
        cdp = await self.context.new_cdp_session(self.page)
        try:
            result = await cdp.send("Accessibility.getFullAXTree")
            nodes = result.get("nodes", [])
            if not nodes:
                return None
            return _cdp_ax_nodes_to_tree(nodes)
        finally:
            await cdp.detach()

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
        await self.page.wait_for_timeout(500)

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
        await self.page.wait_for_timeout(1000)  # settle for dynamic content
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
        await self.page.wait_for_timeout(500)  # lazy-load settle
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
