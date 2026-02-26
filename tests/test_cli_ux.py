# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CLI UX improvements (#9)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── #9a: --output file mode ──────────────────────────────────────


class TestOutputFileMode:
    def test_file_mode_with_suffix(self):
        from pagemap.cli import _validate_output_path

        path, is_file = _validate_output_path("/tmp/test_output.json")
        assert path == Path("/tmp/test_output.json")
        assert is_file is True

    def test_dir_mode_without_suffix(self):
        from pagemap.cli import _validate_output_path

        path, is_file = _validate_output_path("/tmp/test_output_dir")
        assert path == Path("/tmp/test_output_dir")
        assert is_file is False

    def test_none_when_not_specified(self):
        from pagemap.cli import _validate_output_path

        path, is_file = _validate_output_path(None)
        assert path is None
        assert is_file is False

    def test_empty_string(self):
        from pagemap.cli import _validate_output_path

        path, is_file = _validate_output_path("")
        assert path is None
        assert is_file is False


# ── #9b: --url required for build ────────────────────────────────


class TestUrlRequired:
    def test_build_without_url_exits(self, capsys):
        from pagemap.cli import cmd_build

        args = argparse.Namespace(
            url=None,
            snapshots=False,
            snapshot_dir=None,
            output=None,
            format=None,
            offline=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            cmd_build(args)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Error: --url is required" in captured.err
        assert "Examples:" in captured.err


# ── #9c: --help improvements ────────────────────────────────────


class TestServeHelp:
    def test_serve_help_contains_server_options(self, capsys):
        """serve --help should display forwarded server options."""
        from pagemap.cli import main

        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["pagemap", "serve", "--help"]):
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--transport" in captured.out
        assert "--port" in captured.out
        assert "--allow-local" in captured.out

    def test_serve_help_no_duplicate_help(self, capsys):
        """Server -h/--help should not appear in forwarded section."""
        from pagemap.cli import main

        with pytest.raises(SystemExit), patch.object(sys, "argv", ["pagemap", "serve", "--help"]):
            main()
        captured = capsys.readouterr()
        assert captured.out.count("-h, --help") == 1

    def test_get_server_options_help_not_empty(self):
        """_get_server_options_help should return non-empty text."""
        from pagemap.cli import _get_server_options_help

        text = _get_server_options_help()
        assert len(text) > 0
        assert "--transport" in text

    def test_get_server_options_help_optional_arguments_header(self):
        """_get_server_options_help should parse 'optional arguments:' header (Python <3.10)."""
        from pagemap.cli import _get_server_options_help

        fake_help = (
            "usage: server [options]\n"
            "\n"
            "optional arguments:\n"
            "  -h, --help     show this help message and exit\n"
            "  --transport T  transport type\n"
            "  --port PORT    port number\n"
        )

        def fake_parse(args):
            import sys

            sys.stdout.write(fake_help)
            raise SystemExit(0)

        with patch("pagemap.server._parse_server_args", fake_parse):
            text = _get_server_options_help()
        assert "--transport" in text
        assert "--port" in text
        # -h/--help should be stripped
        assert "-h, --help" not in text

    def test_serve_help_action_import_error_fallback(self, capsys):
        """_ServeHelpAction should show fallback message on ImportError."""
        from pagemap.cli import main

        with (
            pytest.raises(SystemExit),
            patch.object(sys, "argv", ["pagemap", "serve", "--help"]),
            patch("pagemap.cli._get_server_options_help", side_effect=ImportError("mocked missing dep")),
        ):
            main()
        captured = capsys.readouterr()
        assert "could not load server options" in captured.err


class TestHelpImprovements:
    def test_build_help_has_epilog(self):
        """Build parser should have examples in epilog."""
        from pagemap.cli import main

        with pytest.raises(SystemExit), patch.object(sys, "argv", ["pagemap", "build", "--help"]):
            main()

    def test_build_parser_has_format_class(self):
        """Verify the parser uses RawDescriptionHelpFormatter."""
        from pagemap.cli import main

        with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["pagemap", "build", "--help"]):
            main()
        assert exc_info.value.code == 0


# ── #9d: --format flag ──────────────────────────────────────────


class TestFormatFlag:
    def test_format_and_output_mutually_exclusive(self, capsys):
        from pagemap.cli import cmd_build

        args = argparse.Namespace(
            url="https://example.com",
            snapshots=False,
            snapshot_dir=None,
            output="/tmp/out.json",
            format="json",
            offline=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            cmd_build(args)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.err

    def test_format_choices(self):
        """Verify valid format choices are json, text, markdown."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        p = subparsers.add_parser("build")
        p.add_argument("--url", type=str)
        p.add_argument("--format", type=str, choices=["json", "text", "markdown"])

        # Valid
        args = parser.parse_args(["build", "--url", "http://x.com", "--format", "json"])
        assert args.format == "json"

        # Invalid
        with pytest.raises(SystemExit):
            parser.parse_args(["build", "--format", "xml"])


# ── #9e: Progress spinner ───────────────────────────────────────


class TestProgressSpinner:
    def test_status_spinner_non_tty(self):
        """Spinner should be silent when stderr is not a TTY."""
        from pagemap._progress import status_spinner

        with patch.object(sys.stderr, "isatty", return_value=False), status_spinner("Testing..."):
            pass  # Should not raise

    def test_print_step_non_tty(self, capsys):
        """print_step should be silent when not a TTY."""
        from pagemap._progress import print_step

        with patch.object(sys.stderr, "isatty", return_value=False):
            print_step("Step 1")
        # Nothing on stderr
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_print_step_tty(self, capsys):
        """print_step should output when TTY."""
        from pagemap._progress import print_step

        with patch.object(sys.stderr, "isatty", return_value=True):
            print_step("Step 1")
        captured = capsys.readouterr()
        assert "Step 1" in captured.err

    def test_spinner_fallback_no_rich(self):
        """Should work even without rich installed."""
        from pagemap._progress import status_spinner

        with patch.object(sys.stderr, "isatty", return_value=True), status_spinner("Working..."):
            pass
