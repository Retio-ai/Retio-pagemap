"""Playwright browser session management for Page Map.

Manages Chromium lifecycle, CDP sessions, and anti-automation flags.
Supports both live browsing and offline HTML loading.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    async_playwright,
)

logger = logging.getLogger(__name__)

# Default browser config
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
DEFAULT_LOCALE = "ko-KR"
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
                # Security hardening
                "--disable-extensions",
                "--disable-plugins",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-gpu",
                "--no-first-run",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
            locale=self.config.locale,
            user_agent=self.config.user_agent,
        )
        self._page = await self._context.new_page()

        # Block dangerous URL schemes via route intercept
        await self._page.route(
            lambda url: url.startswith(("chrome://", "devtools://", "chrome-extension://", "file://")),
            lambda route: route.abort("blockedbyclient"),
        )

        logger.info("Browser session started (headless=%s)", self.config.headless)

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
        logger.info("Browser session stopped")

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.stop()

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
