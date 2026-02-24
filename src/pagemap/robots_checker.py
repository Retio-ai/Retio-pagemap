# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""robots.txt compliance checker (RFC 9309).

Protego-based parser with wildcard (*/$) support, longest-match priority,
origin-level cache, and fail-open semantics.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from protego import Protego

# Version lookup — same pattern as telemetry/collector.py:22-27
try:
    from importlib.metadata import version as _pkg_version

    _PAGEMAP_VERSION = _pkg_version("retio-pagemap")
except Exception:
    _PAGEMAP_VERSION = "unknown"

logger = logging.getLogger(__name__)

ROBOT_USER_AGENT = f"PageMapBot/{_PAGEMAP_VERSION}"
_ROBOTS_FETCH_TIMEOUT = 10  # RFC 9309 recommends ≤30s
_DEFAULT_TTL = 3600.0  # 1-hour fallback
_ERROR_TTL = 300.0  # 5-minute TTL for fail-open entries


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    robots: Protego | None  # None = fetch failed (fail-open)
    fetched_at: float  # time.monotonic()
    ttl: float


class RobotsChecker:
    """RFC 9309 robots.txt checker with origin-level caching.

    - Wildcard (*, $) and longest-match via Protego
    - Cache-Control: max-age dynamic TTL
    - fail-open on errors (never blocks due to fetch failure)
    """

    def __init__(self, *, default_ttl: float = _DEFAULT_TTL) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl

    async def is_allowed(self, url: str) -> tuple[bool, str]:
        """Check whether *url* is allowed by robots.txt.

        Returns:
            (True, "") if allowed, (False, reason) if blocked.
        """
        origin = self._origin(url)

        async with self._lock:
            entry = self._cache.get(origin)
            if entry and (time.monotonic() - entry.fetched_at) < entry.ttl:
                return self._check(entry, url, origin)

        # Cache miss or expired — fetch
        entry = await self._fetch_and_parse(origin)
        async with self._lock:
            self._cache[origin] = entry

        return self._check(entry, url, origin)

    async def _fetch_and_parse(self, origin: str) -> _CacheEntry:
        """Fetch and parse robots.txt for *origin*."""
        robots_url = f"{origin}/robots.txt"

        def _sync_fetch() -> _CacheEntry:
            try:
                req = urllib.request.Request(
                    robots_url,
                    headers={"User-Agent": ROBOT_USER_AGENT},
                )
                with urllib.request.urlopen(req, timeout=_ROBOTS_FETCH_TIMEOUT) as resp:  # noqa: S310  # nosec B310
                    if 200 <= resp.status < 300:
                        body = resp.read().decode("utf-8", errors="replace")
                        robots = Protego.parse(body)
                        ttl = self._extract_cache_ttl(resp) or self._default_ttl
                        return _CacheEntry(robots=robots, fetched_at=time.monotonic(), ttl=ttl)
                    elif resp.status in (401, 403):
                        # RFC 9309: access restricted → disallow all
                        robots = Protego.parse("User-agent: *\nDisallow: /")
                        return _CacheEntry(robots=robots, fetched_at=time.monotonic(), ttl=self._default_ttl)
                    elif 400 <= resp.status < 500:
                        # 4xx → no robots.txt = allow all
                        return _CacheEntry(robots=None, fetched_at=time.monotonic(), ttl=self._default_ttl)
                    else:
                        # 5xx → fail-open
                        return _CacheEntry(robots=None, fetched_at=time.monotonic(), ttl=_ERROR_TTL)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    robots = Protego.parse("User-agent: *\nDisallow: /")
                    return _CacheEntry(robots=robots, fetched_at=time.monotonic(), ttl=self._default_ttl)
                elif 400 <= e.code < 500:
                    return _CacheEntry(robots=None, fetched_at=time.monotonic(), ttl=self._default_ttl)
                else:
                    return _CacheEntry(robots=None, fetched_at=time.monotonic(), ttl=_ERROR_TTL)
            except Exception:
                logger.debug("robots.txt fetch failed for %s", robots_url, exc_info=True)
                return _CacheEntry(robots=None, fetched_at=time.monotonic(), ttl=_ERROR_TTL)

        return await asyncio.wait_for(
            asyncio.to_thread(_sync_fetch),
            timeout=_ROBOTS_FETCH_TIMEOUT + 5,
        )

    def _check(self, entry: _CacheEntry, url: str, origin: str) -> tuple[bool, str]:
        """Evaluate a cached entry against *url*."""
        if entry.robots is None:
            return True, ""  # fail-open
        if entry.robots.can_fetch(url, ROBOT_USER_AGENT):
            return True, ""
        return False, f"robots.txt at {origin} disallows access for PageMapBot"

    def invalidate(self, origin: str | None = None) -> None:
        """Clear cache for a specific origin, or all origins."""
        if origin is None:
            self._cache.clear()
        else:
            self._cache.pop(origin, None)

    @staticmethod
    def _origin(url: str) -> str:
        """Extract scheme://host[:port] (normalized)."""
        p = urlparse(url)
        scheme = p.scheme or "https"
        host = p.hostname or ""
        port = p.port
        # Omit default ports
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{host}"

    @staticmethod
    def _extract_cache_ttl(resp) -> float | None:
        """Parse Cache-Control: max-age from response. Returns None if absent."""
        cc = resp.headers.get("Cache-Control", "")
        for directive in cc.split(","):
            directive = directive.strip().lower()
            if directive.startswith("max-age="):
                try:
                    return max(60.0, float(directive[8:]))  # minimum 60s
                except ValueError:
                    pass
        return None

    @property
    def cache_size(self) -> int:
        """Number of cached origins."""
        return len(self._cache)
