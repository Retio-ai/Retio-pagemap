# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for token scrubbing utilities — detect, redact, and report API key leaks."""

from __future__ import annotations

import importlib.util
from unittest.mock import patch

import pytest

from pagemap.token_security import (
    contains_token,
    scrub_and_report,
    scrub_from_text,
    scrub_headers,
)

_skip_no_telemetry = pytest.mark.skipif(
    importlib.util.find_spec("pagemap.telemetry") is None,
    reason="pagemap.telemetry not available in public release",
)

# A valid-format sk-pm key (52 chars: "sk-pm-v1-" + 43 base64url chars)
_FAKE_KEY = "sk-pm-v1-" + "A" * 43


# ── TestScrubFromText ─────────────────────────────────────────────────


class TestScrubFromText:
    """Tests for scrub_from_text()."""

    def test_scrubs_single_key(self):
        text = f"token is {_FAKE_KEY} here"
        assert scrub_from_text(text) == "token is sk-pm-*** here"

    def test_scrubs_multiple_keys(self):
        key2 = "sk-pm-v2-" + "B" * 43
        text = f"first {_FAKE_KEY} second {key2}"
        result = scrub_from_text(text)
        assert result == "first sk-pm-*** second sk-pm-***"

    def test_no_key_returns_unchanged(self):
        text = "nothing sensitive here"
        assert scrub_from_text(text) == text

    def test_empty_string(self):
        assert scrub_from_text("") == ""

    def test_partial_key_not_scrubbed(self):
        text = "sk-pm-v1-tooshort"
        assert scrub_from_text(text) == text


# ── TestContainsToken ─────────────────────────────────────────────────


class TestContainsToken:
    """Tests for contains_token()."""

    def test_detects_key(self):
        assert contains_token(f"has {_FAKE_KEY}") is True

    def test_no_key_returns_false(self):
        assert contains_token("clean text") is False

    def test_empty_string_returns_false(self):
        assert contains_token("") is False


# ── TestScrubHeaders ──────────────────────────────────────────────────


class TestScrubHeaders:
    """Tests for scrub_headers()."""

    def test_masks_bearer_token(self):
        headers = [(b"authorization", b"Bearer secret-token-value")]
        result = scrub_headers(headers)
        assert result == [(b"authorization", b"Bearer ***")]

    def test_scrubs_key_in_non_auth_header(self):
        headers = [(b"x-custom", f"key={_FAKE_KEY}".encode("latin-1"))]
        result = scrub_headers(headers)
        assert result == [(b"x-custom", b"key=sk-pm-***")]

    def test_preserves_clean_headers(self):
        headers = [
            (b"content-type", b"application/json"),
            (b"x-request-id", b"abc-123"),
        ]
        result = scrub_headers(headers)
        assert result == headers

    def test_case_insensitive_auth_header(self):
        headers = [(b"Authorization", b"Bearer my-secret")]
        result = scrub_headers(headers)
        assert result == [(b"Authorization", b"Bearer ***")]


# ── TestScrubAndReport ────────────────────────────────────────────────


class TestScrubAndReport:
    """Tests for scrub_and_report()."""

    def test_scrubs_and_returns(self):
        result = scrub_and_report(f"has {_FAKE_KEY}", field="url")
        assert result == "has sk-pm-***"

    def test_no_key_returns_unchanged(self):
        text = "clean text"
        assert scrub_and_report(text) == text

    @_skip_no_telemetry
    @patch("pagemap.telemetry.emit")
    def test_emits_telemetry_on_detection(self, mock_emit):
        scrub_and_report(f"leak {_FAKE_KEY}", field="body")

        mock_emit.assert_called_once()
        call_args = mock_emit.call_args
        assert call_args[0][0] == "pagemap.security.prompt_injection_sanitized"
        payload = call_args[0][1]
        assert payload["field"] == "body"
        assert payload["pattern"] == "sk-pm-*"

    def test_empty_string(self):
        assert scrub_and_report("") == ""
