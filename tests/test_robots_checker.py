# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for pagemap.robots_checker (RFC 9309 compliance)."""

from __future__ import annotations

import asyncio
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from pagemap.robots_checker import (
    _ERROR_TTL,
    RobotsChecker,
)

# ── helpers ──────────────────────────────────────────────────────────


def _mock_response(body: str = "", status: int = 200, headers: dict | None = None):
    """Create a mock HTTP response for urllib.request.urlopen."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8")
    resp.headers = MagicMock()
    _headers = headers or {}
    resp.headers.get = lambda key, default="": _headers.get(key, default)
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


DISALLOW_ALL = "User-agent: *\nDisallow: /"
ALLOW_ALL = "User-agent: *\nAllow: /"
DISALLOW_SEARCH = "User-agent: *\nDisallow: /search"
WILDCARD_DISALLOW = "User-agent: *\nDisallow: /search?*"
DOLLAR_ANCHOR = "User-agent: *\nDisallow: /*.json$"
LONGEST_MATCH = "User-agent: *\nDisallow: /\nAllow: /public/"
SPECIFIC_AGENT = "User-agent: *\nDisallow: /\n\nUser-agent: PageMapBot\nAllow: /\n"
EMPTY_ROBOTS = ""


# ── Basic behavior (8 tests) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowed_when_no_robots_txt():
    """404 → allow all."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError("http://example.com/robots.txt", 404, "Not Found", {}, None)
        allowed, reason = await checker.is_allowed("http://example.com/page")
    assert allowed is True
    assert reason == ""


@pytest.mark.asyncio
async def test_blocked_when_disallowed():
    """Disallow: / blocks everything."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(DISALLOW_ALL)
        allowed, reason = await checker.is_allowed("http://example.com/page")
    assert allowed is False
    assert "disallows" in reason


@pytest.mark.asyncio
async def test_allowed_when_explicitly_allowed():
    """Allow: / permits all."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        allowed, reason = await checker.is_allowed("http://example.com/page")
    assert allowed is True


@pytest.mark.asyncio
async def test_wildcard_disallow():
    """Disallow: /search?* blocks query strings."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(WILDCARD_DISALLOW)
        allowed, _ = await checker.is_allowed("http://example.com/search?q=test")
    assert allowed is False


@pytest.mark.asyncio
async def test_dollar_end_anchor():
    """Disallow: /*.json$ blocks .json but not .json?q=1."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(DOLLAR_ANCHOR)
        blocked, _ = await checker.is_allowed("http://example.com/data.json")
        allowed, _ = await checker.is_allowed("http://example.com/data.json?q=1")
    assert blocked is False
    assert allowed is True


@pytest.mark.asyncio
async def test_longest_match_precedence():
    """Allow: /public/ overrides Disallow: / (longest match wins)."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(LONGEST_MATCH)
        allowed, _ = await checker.is_allowed("http://example.com/public/doc")
        blocked, _ = await checker.is_allowed("http://example.com/private/doc")
    assert allowed is True
    assert blocked is False


@pytest.mark.asyncio
async def test_empty_robots_allows_all():
    """Empty robots.txt allows everything."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(EMPTY_ROBOTS)
        allowed, _ = await checker.is_allowed("http://example.com/anything")
    assert allowed is True


@pytest.mark.asyncio
async def test_specific_agent_overrides_wildcard():
    """PageMapBot-specific Allow overrides wildcard Disallow."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(SPECIFIC_AGENT)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is True


# ── RFC 9309 status codes (6 tests) ─────────────────────────────────


@pytest.mark.asyncio
async def test_200_parses_rules():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(DISALLOW_SEARCH)
        blocked, _ = await checker.is_allowed("http://example.com/search")
        allowed, _ = await checker.is_allowed("http://example.com/about")
    assert blocked is False
    assert allowed is True


@pytest.mark.asyncio
async def test_401_disallows_all():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError("http://example.com/robots.txt", 401, "Unauthorized", {}, None)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is False


@pytest.mark.asyncio
async def test_403_disallows_all():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError("http://example.com/robots.txt", 403, "Forbidden", {}, None)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is False


@pytest.mark.asyncio
async def test_404_allows_all():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError("http://example.com/robots.txt", 404, "Not Found", {}, None)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is True


@pytest.mark.asyncio
async def test_5xx_fail_open():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError("http://example.com/robots.txt", 500, "Server Error", {}, None)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is True


@pytest.mark.asyncio
async def test_timeout_fail_open():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = TimeoutError("Connection timed out")
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is True


# ── Cache (7 tests) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_hit_no_refetch():
    """Second call uses cache, no re-fetch."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://example.com/page1")
        await checker.is_allowed("http://example.com/page2")
    assert mock_open.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires_after_ttl():
    """Cache entry expires after TTL."""
    checker = RobotsChecker(default_ttl=0.1)  # 100ms
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://example.com/page")

    await asyncio.sleep(0.15)

    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://example.com/page")
        assert mock_open.call_count == 1  # re-fetched


@pytest.mark.asyncio
async def test_different_origins_separate():
    """Different origins have separate cache entries."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://a.com/page")
        await checker.is_allowed("http://b.com/page")
    assert mock_open.call_count == 2
    assert checker.cache_size == 2


@pytest.mark.asyncio
async def test_invalidate_single():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://a.com/page")
        await checker.is_allowed("http://b.com/page")
    assert checker.cache_size == 2
    checker.invalidate("http://a.com")
    assert checker.cache_size == 1


@pytest.mark.asyncio
async def test_invalidate_all():
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://a.com/page")
        await checker.is_allowed("http://b.com/page")
    checker.invalidate()
    assert checker.cache_size == 0


@pytest.mark.asyncio
async def test_error_entry_short_ttl():
    """Fail-open entries use short TTL."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError("http://example.com/robots.txt", 500, "Server Error", {}, None)
        await checker.is_allowed("http://example.com/page")

    entry = checker._cache.get("http://example.com")
    assert entry is not None
    assert entry.ttl == _ERROR_TTL


@pytest.mark.asyncio
async def test_cache_control_max_age_respected():
    """Cache-Control: max-age sets dynamic TTL."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL, headers={"Cache-Control": "max-age=7200"})
        await checker.is_allowed("http://example.com/page")

    entry = checker._cache.get("http://example.com")
    assert entry is not None
    assert entry.ttl == 7200.0


# ── Origin extraction (4 tests) ─────────────────────────────────────


def test_https_default_port():
    assert RobotsChecker._origin("https://example.com/path") == "https://example.com"


def test_http_default_port():
    assert RobotsChecker._origin("http://example.com/path") == "http://example.com"


def test_custom_port():
    assert RobotsChecker._origin("http://example.com:8080/path") == "http://example.com:8080"


def test_path_stripped():
    assert RobotsChecker._origin("https://example.com/a/b/c?q=1#frag") == "https://example.com"


# ── Protego integration (4 tests) ───────────────────────────────────


@pytest.mark.asyncio
async def test_protego_wildcard_star():
    """Protego handles * wildcard."""
    checker = RobotsChecker()
    robots_txt = "User-agent: *\nDisallow: /search?*"
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(robots_txt)
        blocked, _ = await checker.is_allowed("http://example.com/search?q=test")
        allowed, _ = await checker.is_allowed("http://example.com/about")
    assert blocked is False
    assert allowed is True


@pytest.mark.asyncio
async def test_protego_dollar_anchor():
    """Protego handles $ anchor."""
    checker = RobotsChecker()
    robots_txt = "User-agent: *\nDisallow: /*.pdf$"
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(robots_txt)
        blocked, _ = await checker.is_allowed("http://example.com/doc.pdf")
        allowed, _ = await checker.is_allowed("http://example.com/doc.pdf?page=2")
    assert blocked is False
    assert allowed is True


@pytest.mark.asyncio
async def test_protego_longest_match():
    """Protego uses longest-match precedence."""
    checker = RobotsChecker()
    robots_txt = "User-agent: *\nDisallow: /\nAllow: /public/docs/"
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(robots_txt)
        allowed, _ = await checker.is_allowed("http://example.com/public/docs/readme")
        blocked, _ = await checker.is_allowed("http://example.com/private")
    assert allowed is True
    assert blocked is False


@pytest.mark.asyncio
async def test_protego_crawl_delay_ignored():
    """Crawl-delay is parsed but not enforced."""
    checker = RobotsChecker()
    robots_txt = "User-agent: *\nCrawl-delay: 10\nAllow: /"
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(robots_txt)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is True


# ── Edge cases (6 tests) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_robots_txt():
    """Malformed robots.txt → parsed as best-effort (Protego handles gracefully)."""
    checker = RobotsChecker()
    malformed = "this is not\na valid robots\ntxt file\nrandom garbage"
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(malformed)
        allowed, _ = await checker.is_allowed("http://example.com/page")
    # Protego parses gracefully — no valid rules means allow all
    assert allowed is True


@pytest.mark.asyncio
async def test_very_large_robots_txt():
    """Large robots.txt (100KB+) is parsed normally."""
    checker = RobotsChecker()
    # Generate ~100KB robots.txt
    lines = ["User-agent: *"]
    for i in range(5000):
        lines.append(f"Disallow: /path{i}/")
    lines.append("Allow: /allowed/")
    large_robots = "\n".join(lines)
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(large_robots)
        blocked, _ = await checker.is_allowed("http://example.com/path42/page")
        allowed, _ = await checker.is_allowed("http://example.com/allowed/page")
    assert blocked is False
    assert allowed is True


@pytest.mark.asyncio
async def test_non_utf8_encoding():
    """Non-UTF-8 content handled via errors=replace."""
    checker = RobotsChecker()
    # Simulate a response that has latin-1 content
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        resp = _mock_response(ALLOW_ALL)
        # Override read to return latin-1 bytes with non-ascii
        resp.read.return_value = "User-agent: *\nAllow: /\n# Stra\xdfe".encode("latin-1")
        mock_open.return_value = resp
        allowed, _ = await checker.is_allowed("http://example.com/page")
    assert allowed is True


@pytest.mark.asyncio
async def test_concurrent_requests_same_origin():
    """Concurrent requests to same origin don't duplicate fetches."""
    checker = RobotsChecker()
    call_count = 0

    def _counting_open(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_response(ALLOW_ALL)

    with patch("pagemap.robots_checker.urllib.request.urlopen", side_effect=_counting_open):
        # First call populates cache
        await checker.is_allowed("http://example.com/page1")
        # Subsequent calls use cache
        results = await asyncio.gather(
            checker.is_allowed("http://example.com/page2"),
            checker.is_allowed("http://example.com/page3"),
            checker.is_allowed("http://example.com/page4"),
        )
    # Only 1 fetch should have occurred (first call)
    assert call_count == 1
    assert all(r[0] is True for r in results)


@pytest.mark.asyncio
async def test_robot_user_agent_header():
    """Fetch request includes PageMapBot UA header."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL)
        await checker.is_allowed("http://example.com/page")

    # Check the Request object passed to urlopen
    call_args = mock_open.call_args
    req = call_args[0][0]  # first positional arg
    assert "PageMapBot" in req.get_header("User-agent")


@pytest.mark.asyncio
async def test_cache_control_min_60s():
    """Cache-Control: max-age below 60s is clamped to 60s."""
    checker = RobotsChecker()
    with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
        mock_open.return_value = _mock_response(ALLOW_ALL, headers={"Cache-Control": "max-age=10"})
        await checker.is_allowed("http://example.com/page")

    entry = checker._cache.get("http://example.com")
    assert entry is not None
    assert entry.ttl == 60.0
