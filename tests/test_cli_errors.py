# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CLI error handling (P0 raw traceback fix).

Covers:
- classify_network_error(): regex-based net::ERR_* classification
- from_exception() integration with network errors
- ProblemDetail.to_cli_text() formatting
- cmd_build() sync-level error catch
- main() top-level error handler
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.errors import BrowserError, SSRFError
from pagemap.problem_details import (
    _CLI_HINTS,
    ProblemDetail,
    ProblemType,
    classify_network_error,
    from_exception,
)

# ── TestClassifyNetworkError ─────────────────────────────────────────


class TestClassifyNetworkError:
    """Test regex-based net::ERR_* classification (4 buckets + hostname)."""

    def test_dns_name_not_resolved(self):
        msg = "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://not-a-real-domain.com/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.DNS_RESOLUTION_FAILED
        assert "not-a-real-domain.com" in human
        assert "resolve" in human.lower()

    def test_dns_no_hostname(self):
        msg = "net::ERR_NAME_NOT_RESOLVED"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.DNS_RESOLUTION_FAILED
        assert "resolve" in human.lower()

    def test_connection_refused(self):
        msg = "Page.goto: net::ERR_CONNECTION_REFUSED at https://localhost:9999/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.ACTION_FAILED
        assert "Connection failed" in human

    def test_connection_timed_out(self):
        msg = "Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://slow-site.com/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.PAGE_TIMEOUT
        assert "timed out" in human.lower()

    def test_connection_reset(self):
        msg = "net::ERR_CONNECTION_RESET at https://example.com/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, _ = result
        assert ptype == ProblemType.ACTION_FAILED

    def test_ssl_cert_error(self):
        msg = "Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://self-signed.example.com/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.VALIDATION_ERROR
        assert "SSL" in human or "TLS" in human

    def test_ssl_protocol_error(self):
        msg = "net::ERR_SSL_PROTOCOL_ERROR at https://bad-ssl.com/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.VALIDATION_ERROR

    def test_fallback_unknown_net_err(self):
        msg = "Page.goto: net::ERR_SOME_FUTURE_CODE at https://example.com/"
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.ACTION_FAILED
        assert "ERR_SOME_FUTURE_CODE" in human

    def test_no_net_err_returns_none(self):
        msg = "Something else went wrong"
        assert classify_network_error(msg) is None

    def test_playwright_full_format(self):
        """Full Playwright error message format with method prefix."""
        msg = (
            "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://not-a-real-domain-12345.com/\n"
            "=========================== logs ===========================\n"
            'navigating to "https://not-a-real-domain-12345.com/", waiting until "load"\n'
            "============================================================"
        )
        result = classify_network_error(msg)
        assert result is not None
        ptype, human = result
        assert ptype == ProblemType.DNS_RESOLUTION_FAILED
        assert "not-a-real-domain-12345.com" in human


# ── TestFromExceptionNetworkErrors ───────────────────────────────────


class TestFromExceptionNetworkErrors:
    """Test from_exception() integration with network error classification."""

    def test_dns_error_via_generic_exception(self):
        exc = Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://bad.com/")
        problem = from_exception(exc, tool_context="build")
        assert problem.type == ProblemType.DNS_RESOLUTION_FAILED.uri
        assert "bad.com" in problem.detail

    def test_connection_refused_via_generic_exception(self):
        exc = Exception("Page.goto: net::ERR_CONNECTION_REFUSED at https://localhost:9999/")
        problem = from_exception(exc, tool_context="get_page_map")
        assert problem.type == ProblemType.ACTION_FAILED.uri

    def test_timeout_error_takes_priority_over_net_err(self):
        """TimeoutError should be classified before net::ERR_* matching."""
        exc = TimeoutError("net::ERR_CONNECTION_TIMED_OUT")
        problem = from_exception(exc, tool_context="get_page_map")
        # TimeoutError with nav context → PAGE_TIMEOUT (not via net::ERR_*)
        assert problem.type == ProblemType.PAGE_TIMEOUT.uri

    def test_pagemap_error_takes_priority(self):
        """Known PageMapError subclasses should be classified before net::ERR_*."""
        exc = BrowserError("net::ERR_NAME_NOT_RESOLVED")
        problem = from_exception(exc, tool_context="build")
        # BrowserError → BROWSER_UNAVAILABLE, not DNS_RESOLUTION_FAILED
        assert problem.type == ProblemType.BROWSER_UNAVAILABLE.uri

    def test_non_net_err_exception_generic(self):
        exc = ValueError("something else")
        problem = from_exception(exc, tool_context="build")
        assert problem.type == "about:blank"

    def test_mcp_path_still_works(self):
        """from_exception() feeds into to_mcp_text() without breaking."""
        exc = Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://bad.com/")
        problem = from_exception(exc, tool_context="get_page_map")
        mcp_text = problem.to_mcp_text()
        assert "Error (get_page_map):" in mcp_text


# ── TestProblemDetailToCliText ───────────────────────────────────────


class TestProblemDetailToCliText:
    """Test ProblemDetail.to_cli_text() formatting."""

    def test_dns_error_with_hint(self):
        problem = ProblemDetail(
            type=ProblemType.DNS_RESOLUTION_FAILED.uri,
            title="DNS Resolution Failed",
            status=502,
            detail="Could not resolve domain name 'bad.com'",
        )
        text = problem.to_cli_text()
        assert text.startswith("Error: ")
        assert "bad.com" in text
        assert "Hint:" in text
        assert "spelling" in text

    def test_browser_unavailable_with_hint(self):
        problem = ProblemDetail(
            type=ProblemType.BROWSER_UNAVAILABLE.uri,
            title="Browser Unavailable",
            status=503,
            detail="Chromium is not installed",
        )
        text = problem.to_cli_text()
        assert "Hint:" in text
        assert "playwright install chromium" in text

    def test_about_blank_no_hint(self):
        problem = ProblemDetail(
            type="about:blank",
            title="",
            status=500,
            detail="Something went wrong",
        )
        text = problem.to_cli_text()
        assert text == "Error: Something went wrong"
        assert "Hint:" not in text

    def test_sanitized_detail_in_cli(self):
        """Detail should already be sanitized by from_exception()."""
        exc = Exception("Error at /Users/john/secret/path.py: net::ERR_NAME_NOT_RESOLVED at https://x.com/")
        problem = from_exception(exc, tool_context="build")
        text = problem.to_cli_text()
        assert "/Users/" not in text

    def test_all_cli_hints_have_valid_types(self):
        """Every key in _CLI_HINTS should be a valid ProblemType URI."""
        valid_uris = {pt.uri for pt in ProblemType}
        for uri in _CLI_HINTS:
            assert uri in valid_uris, f"Unknown type URI in _CLI_HINTS: {uri}"


# ── TestCmdBuildErrorHandling ────────────────────────────────────────


class TestCmdBuildErrorHandling:
    """Test cmd_build() sync-level error catch."""

    def _make_args(self, url: str | None = None, snapshots: bool = False, output: str | None = None):
        args = MagicMock()
        args.url = url
        args.snapshots = snapshots
        args.output = output
        args.snapshot_dir = None
        return args

    @patch("pagemap.cli._build_live", new_callable=AsyncMock)
    def test_dns_error_clean_exit(self, mock_build_live, capsys):
        mock_build_live.side_effect = Exception(
            "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://not-a-real-domain-12345.com/"
        )
        from pagemap.cli import cmd_build

        args = self._make_args(url="not-a-real-domain-12345.com")

        with pytest.raises(SystemExit) as exc_info:
            cmd_build(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.err
        assert "not-a-real-domain-12345.com" in captured.err
        assert "Hint:" in captured.err
        # No traceback on stdout
        assert "Traceback" not in captured.out

    @patch("pagemap.cli._build_live", new_callable=AsyncMock)
    def test_connection_refused_clean_exit(self, mock_build_live, capsys):
        mock_build_live.side_effect = Exception("Page.goto: net::ERR_CONNECTION_REFUSED at https://localhost:9999/")
        from pagemap.cli import cmd_build

        args = self._make_args(url="https://localhost:9999/")

        with pytest.raises(SystemExit) as exc_info:
            cmd_build(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.err

    @patch("pagemap.cli._build_live", new_callable=AsyncMock)
    def test_browser_error_clean_exit(self, mock_build_live, capsys):
        mock_build_live.side_effect = BrowserError(
            "Chromium is not installed and auto-install failed. Please run: playwright install chromium"
        )
        from pagemap.cli import cmd_build

        args = self._make_args(url="https://example.com")

        with pytest.raises(SystemExit) as exc_info:
            cmd_build(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.err
        assert "Hint:" in captured.err
        assert "playwright install chromium" in captured.err

    @patch("pagemap.cli._build_live", new_callable=AsyncMock)
    def test_keyboard_interrupt_propagates(self, mock_build_live):
        mock_build_live.side_effect = KeyboardInterrupt()
        from pagemap.cli import cmd_build

        args = self._make_args(url="https://example.com")

        with pytest.raises(KeyboardInterrupt):
            cmd_build(args)

    @patch("pagemap.cli._build_live", new_callable=AsyncMock)
    def test_no_traceback_on_stderr(self, mock_build_live, capsys):
        """Ensure no raw Python traceback leaks to stderr."""
        mock_build_live.side_effect = Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://bad.com/")
        from pagemap.cli import cmd_build

        args = self._make_args(url="bad.com")

        with pytest.raises(SystemExit):
            cmd_build(args)

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out


# ── TestMainTopLevelHandler ──────────────────────────────────────────


class TestMainTopLevelHandler:
    """Test main() top-level error handler."""

    def test_keyboard_interrupt_exit_130(self, capsys):
        from pagemap.cli import main

        with patch("pagemap.cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_args = MagicMock()
            mock_args.command = "build"
            mock_args.verbose = False
            mock_parse.return_value = mock_args

            # Inject commands dict that raises KeyboardInterrupt
            with (
                patch.dict("pagemap.cli.__dict__", {}),
                patch("pagemap.cli.cmd_build", side_effect=KeyboardInterrupt()),
                patch("pagemap.cli._has_internal", return_value=False),
                patch("pagemap.cli._has_benchmark", return_value=False),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 130

        captured = capsys.readouterr()
        assert "Interrupted" in captured.err

    def test_system_exit_preserved(self):
        from pagemap.cli import main

        with patch("pagemap.cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_args = MagicMock()
            mock_args.command = "build"
            mock_args.verbose = False
            mock_parse.return_value = mock_args

            with (
                patch("pagemap.cli.cmd_build", side_effect=SystemExit(42)),
                patch("pagemap.cli._has_internal", return_value=False),
                patch("pagemap.cli._has_benchmark", return_value=False),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 42

    def test_generic_exception_exit_1(self, capsys):
        from pagemap.cli import main

        with patch("pagemap.cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_args = MagicMock()
            mock_args.command = "build"
            mock_args.verbose = False
            mock_parse.return_value = mock_args

            with (
                patch("pagemap.cli.cmd_build", side_effect=RuntimeError("unexpected crash")),
                patch("pagemap.cli._has_internal", return_value=False),
                patch("pagemap.cli._has_benchmark", return_value=False),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Error:" in captured.err
        assert "Traceback" not in captured.err

    def test_verbose_shows_traceback(self, capsys):
        from pagemap.cli import main

        with patch("pagemap.cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_args = MagicMock()
            mock_args.command = "build"
            mock_args.verbose = True
            mock_parse.return_value = mock_args

            with (
                patch("pagemap.cli.cmd_build", side_effect=RuntimeError("unexpected crash")),
                patch("pagemap.cli._has_internal", return_value=False),
                patch("pagemap.cli._has_benchmark", return_value=False),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Error:" in captured.err
        assert "Traceback" in captured.err
        assert "unexpected crash" in captured.err

    def test_ssrf_error_exit_1(self, capsys):
        from pagemap.cli import main

        with patch("pagemap.cli.argparse.ArgumentParser.parse_args") as mock_parse:
            mock_args = MagicMock()
            mock_args.command = "build"
            mock_args.verbose = False
            mock_parse.return_value = mock_args

            with (
                patch("pagemap.cli.cmd_build", side_effect=SSRFError("blocked url")),
                patch("pagemap.cli._has_internal", return_value=False),
                patch("pagemap.cli._has_benchmark", return_value=False),
                pytest.raises(SystemExit) as exc_info,
            ):
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Error:" in captured.err
