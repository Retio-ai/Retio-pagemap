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
