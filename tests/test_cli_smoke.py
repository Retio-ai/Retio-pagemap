# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Subprocess-based CLI smoke tests.

Unlike test_cli_ux.py and test_cli_errors.py which call Python functions
directly with mock args, these tests invoke ``python -m pagemap.cli`` as a
real subprocess.  This catches issues invisible to in-process tests:

- Raw traceback leaks to stderr
- Exit-code mismatches when run as a real process
- ``--output`` creating directories instead of files
- stdout / stderr separation bugs

Note: conftest.py safety nets (_block_real_browser, _reset_state) do not
affect subprocess tests — each subprocess is a fresh Python process.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

PYTHON = sys.executable
CLI = [PYTHON, "-m", "pagemap.cli"]

LOCAL_TIMEOUT = 10
NET_TIMEOUT = 60


@pytest.mark.smoke
@pytest.mark.timeout(120)
class TestCLISmoke:
    """Subprocess-based CLI smoke tests."""

    @staticmethod
    def _run(*args: str, timeout: int = LOCAL_TIMEOUT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*CLI, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )

    # ── Local tests (no network) ─────────────────────────────────

    def test_build_without_url_exits_nonzero(self):
        """build with no --url flag should fail."""
        r = self._run("build")
        assert r.returncode != 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert "--url" in r.stderr, f"stderr: {r.stderr}"

    def test_help_includes_usage_info(self):
        """build --help should show usage information."""
        r = self._run("build", "--help")
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert "--url" in r.stdout, f"stdout: {r.stdout}"
        assert "example" in r.stdout.lower(), f"stdout: {r.stdout}"

    def test_unknown_subcommand_exits_nonzero(self):
        """Unknown subcommand should fail."""
        r = self._run("notacommand")
        assert r.returncode != 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"

    def test_format_and_output_mutually_exclusive(self, tmp_path):
        """--format and -o cannot be used together."""
        r = self._run("build", "--url", "x", "--format", "json", "-o", str(tmp_path / "o.json"))
        assert r.returncode != 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert "mutually exclusive" in r.stderr.lower(), f"stderr: {r.stderr}"

    # ── Network tests (require Playwright Chromium) ──────────────

    @pytest.mark.network
    def test_invalid_domain_no_traceback(self):
        """Invalid domain should fail cleanly without Python traceback."""
        r = self._run("build", "--url", "https://not-a-real-domain-xyzzy.test", timeout=NET_TIMEOUT)
        assert r.returncode != 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert "Traceback" not in r.stderr, f"stderr: {r.stderr}"

    @pytest.mark.network
    def test_format_json_outputs_valid_json(self):
        """--format json should produce valid JSON on stdout."""
        r = self._run("build", "--url", "https://example.com", "--format", "json", timeout=NET_TIMEOUT)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        try:
            json.loads(r.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"stdout is not valid JSON:\nstdout: {r.stdout}\nstderr: {r.stderr}")

    @pytest.mark.network
    def test_format_text_outputs_nonempty(self):
        """--format text should produce non-empty output."""
        r = self._run("build", "--url", "https://example.com", "--format", "text", timeout=NET_TIMEOUT)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert len(r.stdout.strip()) > 0, "stdout was empty"

    @pytest.mark.network
    def test_output_creates_file_not_dir(self, tmp_path):
        """-o should create a file, not a directory."""
        out = tmp_path / "out.json"
        r = self._run("build", "--url", "https://example.com", "-o", str(out), timeout=NET_TIMEOUT)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert out.is_file(), f"{out} is not a file"
        content = out.read_text(encoding="utf-8")
        assert len(content) > 0, f"{out} is empty"
        try:
            json.loads(content)
        except json.JSONDecodeError:
            pytest.fail(f"{out} is not valid JSON:\ncontent: {content}\nstderr: {r.stderr}")

    @pytest.mark.network
    def test_example_dot_com_succeeds(self):
        """Basic build against example.com should succeed."""
        r = self._run("build", "--url", "https://example.com", "--format", "text", timeout=NET_TIMEOUT)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
