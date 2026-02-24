# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for robots.txt checking and bot UA in server.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pagemap.server as srv
from pagemap.server import _check_robots, _parse_server_args

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


@pytest.fixture
def _reset_robots_flags():
    """Reset robots-related server flags after test."""
    orig_ignore = srv._ignore_robots
    orig_bot_ua = srv._bot_ua
    orig_checker = srv._robots_checker
    yield
    srv._ignore_robots = orig_ignore
    srv._bot_ua = orig_bot_ua
    srv._robots_checker = orig_checker


# ── CLI flag tests (6 tests) ────────────────────────────────────────


class TestParseServerArgsRobots:
    """Tests for --ignore-robots and --bot-ua CLI args."""

    def test_ignore_robots_cli_flag(self):
        args = _parse_server_args(["--ignore-robots"])
        assert args.ignore_robots is True

    def test_ignore_robots_env_var(self):
        with patch.dict("os.environ", {"PAGEMAP_IGNORE_ROBOTS": "1"}):
            args = _parse_server_args([])
            assert args.ignore_robots is True

    def test_ignore_robots_default_false(self):
        args = _parse_server_args([])
        assert args.ignore_robots is False

    def test_bot_ua_cli_flag(self):
        args = _parse_server_args(["--bot-ua"])
        assert args.bot_ua is True

    def test_bot_ua_env_var(self):
        with patch.dict("os.environ", {"PAGEMAP_BOT_UA": "1"}):
            args = _parse_server_args([])
            assert args.bot_ua is True

    def test_all_flags_combined(self):
        args = _parse_server_args(["--allow-local", "--telemetry", "--ignore-robots", "--bot-ua"])
        assert args.allow_local is True
        assert args.telemetry is True
        assert args.ignore_robots is True
        assert args.bot_ua is True


# ── _check_robots tests (3 tests) ───────────────────────────────────


class TestCheckRobots:
    """Tests for the _check_robots() wrapper in server.py."""

    async def test_check_robots_none_when_disabled(self, _reset_robots_flags):
        """No checker → always returns None (allowed)."""
        srv._robots_checker = None
        result = await _check_robots("http://example.com/page")
        assert result is None

    async def test_check_robots_none_when_allowed(self, _reset_robots_flags):
        """Allowed URL → returns None."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()
        with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response("User-agent: *\nAllow: /")
            result = await _check_robots("http://example.com/page")
        assert result is None

    async def test_check_robots_error_when_blocked(self, _reset_robots_flags):
        """Blocked URL → returns error string."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()
        with patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response("User-agent: *\nDisallow: /")
            result = await _check_robots("http://example.com/page")
        assert result is not None
        assert "disallows" in result


# ── get_page_map integration (4 tests) ──────────────────────────────


class TestGetPageMapRobots:
    """Integration: robots.txt blocking in get_page_map tool."""

    async def test_get_page_map_blocked_by_robots(self, _reset_robots_flags):
        """Robots-blocked URL returns error message."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        mock_session = AsyncMock()
        mock_session.navigate = AsyncMock()

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._get_session", return_value=mock_session),
        ):
            mock_open.return_value = _mock_response("User-agent: *\nDisallow: /")
            result = await srv.get_page_map("http://example.com/blocked")

        assert "Error:" in result
        assert "robots.txt" in result
        mock_session.navigate.assert_not_called()

    async def test_get_page_map_allowed_by_robots(self, _reset_robots_flags):
        """Robots-allowed URL proceeds to navigation."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        # Create a mock session + page_map
        mock_session = AsyncMock()
        mock_session.navigate = AsyncMock()
        mock_session.page = MagicMock()
        mock_session.page.url = "http://example.com/allowed"

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._get_page_map_impl", return_value="PageMap result") as mock_impl,
        ):
            mock_open.return_value = _mock_response("User-agent: *\nAllow: /")
            result = await srv.get_page_map("http://example.com/allowed")

        assert "Error:" not in result
        mock_impl.assert_called_once()

    async def test_get_page_map_no_check_when_url_none(self, _reset_robots_flags):
        """url=None (refresh current page) → no robots check."""
        from pagemap.robots_checker import RobotsChecker

        checker = RobotsChecker()
        srv._robots_checker = checker

        with (
            patch.object(checker, "is_allowed") as mock_is_allowed,
            patch("pagemap.server._get_page_map_impl", return_value="PageMap result"),
        ):
            result = await srv.get_page_map(None)

        mock_is_allowed.assert_not_called()
        assert "Error:" not in result

    async def test_robots_error_message_actionable(self, _reset_robots_flags):
        """Error message includes actionable guidance."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._get_session", return_value=AsyncMock()),
        ):
            mock_open.return_value = _mock_response("User-agent: *\nDisallow: /")
            result = await srv.get_page_map("http://example.com/blocked")

        assert "different URL" in result or "guidance" in result


# ── batch integration (3 tests) ─────────────────────────────────────


class TestBatchRobots:
    """Integration: robots.txt blocking in batch_get_page_map."""

    async def test_batch_blocked_urls_in_pre_errors(self, _reset_robots_flags):
        """Robots-blocked URLs appear in pre_errors."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._get_session", return_value=AsyncMock()),
        ):
            mock_open.return_value = _mock_response("User-agent: *\nDisallow: /")
            result = await srv.batch_get_page_map(["http://example.com/blocked"])

        import json

        parsed = json.loads(result)
        assert parsed["results"][0]["status"] == "error"
        assert "robots" in parsed["results"][0]["error"].lower() or "disallows" in parsed["results"][0]["error"].lower()

    async def test_batch_robots_telemetry_emitted(self, _reset_robots_flags):
        """Batch robots blocking emits ROBOTS_BLOCKED telemetry per URL."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._telem") as mock_telem,
            patch("pagemap.server._get_session", return_value=AsyncMock()),
        ):
            mock_open.return_value = _mock_response("User-agent: *\nDisallow: /")
            await srv.batch_get_page_map(
                [
                    "http://example.com/page1",
                    "http://other.com/page2",
                ]
            )

        from pagemap.telemetry.events import ROBOTS_BLOCKED

        telem_calls = [c for c in mock_telem.call_args_list if c[0][0] == ROBOTS_BLOCKED]
        assert len(telem_calls) == 2
        origins = {c[0][1]["origin"] for c in telem_calls}
        assert origins == {"http://example.com", "http://other.com"}

    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    async def test_batch_mixed_allowed_and_blocked(self, _reset_robots_flags):
        """Mixed batch: blocked URLs in errors, allowed URLs proceed."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        robots_txt = "User-agent: *\nDisallow: /blocked/\nAllow: /"

        mock_session = AsyncMock()
        mock_session.page = MagicMock()
        mock_session.page.url = "http://example.com/"
        mock_session.navigate = AsyncMock()
        mock_session.create_batch_page = AsyncMock()
        mock_session.close_batch_page = AsyncMock()

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._get_session", return_value=mock_session),
        ):
            mock_open.return_value = _mock_response(robots_txt)
            # The blocked URL should be in pre_errors
            # The allowed URL should proceed to batch processing
            result = await srv.batch_get_page_map(
                [
                    "http://example.com/blocked/page",
                    "http://example.com/allowed/page",
                ]
            )

        import json

        parsed = json.loads(result)
        # At least one result should be an error for the blocked URL
        blocked_results = [r for r in parsed["results"] if r.get("status") == "error"]
        assert len(blocked_results) >= 1


# ── Telemetry test (1 test) ─────────────────────────────────────────


class TestRobotsTelemetry:
    async def test_robots_telemetry_emitted(self, _reset_robots_flags):
        """Robots blocking emits ROBOTS_BLOCKED telemetry event."""
        from pagemap.robots_checker import RobotsChecker

        srv._robots_checker = RobotsChecker()

        with (
            patch("pagemap.robots_checker.urllib.request.urlopen") as mock_open,
            patch("pagemap.server._telem") as mock_telem,
            patch("pagemap.server._get_session", return_value=AsyncMock()),
        ):
            mock_open.return_value = _mock_response("User-agent: *\nDisallow: /")
            await srv.get_page_map("http://example.com/blocked")

        # Check that _telem was called with ROBOTS_BLOCKED
        from pagemap.telemetry.events import ROBOTS_BLOCKED

        telem_calls = [c for c in mock_telem.call_args_list if c[0][0] == ROBOTS_BLOCKED]
        assert len(telem_calls) == 1
        payload = telem_calls[0][0][1]
        assert payload["url"] == "http://example.com/blocked"
        assert payload["origin"] == "http://example.com"


# ── UA integration (4 tests) ────────────────────────────────────────


class TestBotUserAgent:
    """Tests for BOT_USER_AGENT constant and --bot-ua flag."""

    def test_default_ua_is_chrome(self):
        from pagemap.browser_session import DEFAULT_USER_AGENT

        assert "Chrome" in DEFAULT_USER_AGENT

    def test_bot_ua_flag_changes_config(self, _reset_robots_flags):
        """--bot-ua flag is parsed correctly."""
        args = _parse_server_args(["--bot-ua"])
        assert args.bot_ua is True

    def test_bot_ua_includes_version(self):
        from pagemap.browser_session import BOT_USER_AGENT

        assert "PageMapBot/" in BOT_USER_AGENT

    def test_bot_ua_includes_info_url(self):
        from pagemap.browser_session import BOT_USER_AGENT

        assert "github.com/Retio-ai/pagemap" in BOT_USER_AGENT
