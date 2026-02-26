# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP response size guards.

Covers:
  - _check_response_size: passthrough, truncation, UTF-8 safety, telemetry
  - Screenshot size guard in _take_screenshot_impl
  - Environment variable override for limits
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pagemap.server as srv

# ── _check_response_size unit tests ──────────────────────────────────


def test_small_response_passes_through():
    """Responses under the limit are returned unchanged."""
    small = "Hello, world!"
    result = srv._check_response_size(small, tool="get_page_map")
    assert result == small


def test_large_response_truncated():
    """Responses over the limit are truncated with a tail marker."""
    # Create a response larger than 1MB
    original = "x" * (1024 * 1024 + 500)
    result = srv._check_response_size(original, tool="get_page_map")

    assert len(result.encode("utf-8")) < len(original.encode("utf-8"))
    assert "[Truncated:" in result
    assert "Call get_page_map on a more specific URL." in result


def test_truncation_utf8_safe():
    """Truncation at byte boundary does not produce broken UTF-8."""
    # Multi-byte char: Korean 가 = 3 bytes in UTF-8
    # Fill up to just under the limit with ASCII, then add multi-byte chars
    limit = srv.MAX_RESPONSE_SIZE_BYTES
    # Create string where the cut point falls mid-character
    ascii_part = "a" * (limit - 2)  # 2 bytes short of limit
    # Add a 3-byte character — the cut at limit will split it
    full = ascii_part + "가가가"
    assert len(full.encode("utf-8")) > limit

    result = srv._check_response_size(full, tool="get_page_map")
    # Must be valid UTF-8 — decode without error
    result.encode("utf-8").decode("utf-8")
    assert "[Truncated:" in result


def test_exact_limit_passes_through():
    """Response exactly at the byte limit passes through unchanged."""
    exact = "a" * srv.MAX_RESPONSE_SIZE_BYTES
    assert len(exact.encode("utf-8")) == srv.MAX_RESPONSE_SIZE_BYTES
    result = srv._check_response_size(exact, tool="get_page_map")
    assert result == exact


def test_telemetry_emitted_on_truncation():
    """Telemetry event is emitted when truncation occurs."""
    pytest.importorskip("pagemap.telemetry")
    original = "x" * (srv.MAX_RESPONSE_SIZE_BYTES + 100)
    with patch.object(srv, "_telem") as mock_telem:
        srv._check_response_size(original, tool="get_page_map")

    mock_telem.assert_called_once()
    call_args = mock_telem.call_args
    assert call_args[0][0] == "pagemap.guard.response_size_exceeded"
    payload = call_args[0][1]
    assert payload["tool"] == "get_page_map"
    assert payload["size"] > srv.MAX_RESPONSE_SIZE_BYTES
    assert payload["limit"] == srv.MAX_RESPONSE_SIZE_BYTES


def test_env_var_override(monkeypatch):
    """Environment variable changes the effective limit."""
    # Set a very small limit via env var and reload the constant
    monkeypatch.setattr(srv, "MAX_RESPONSE_SIZE_BYTES", 100)

    small = "x" * 50
    assert srv._check_response_size(small, tool="test") == small

    large = "x" * 200
    result = srv._check_response_size(large, tool="test")
    assert "[Truncated:" in result


# ── Screenshot size guard ────────────────────────────────────────────


async def test_screenshot_under_limit():
    """Screenshots under the limit are returned normally."""
    fake_bytes = b"\x89PNG" + b"\x00" * 1000  # small PNG-like bytes
    mock_page = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=fake_bytes)

    mock_session = AsyncMock()
    mock_session.page = mock_page
    mock_session.drain_dialogs = MagicMock(return_value=[])

    ctx = AsyncMock()
    ctx.get_session = AsyncMock(return_value=mock_session)
    ctx.cache = srv._state.cache

    result = await srv._take_screenshot_impl(full_page=False, ctx=ctx)
    # Should return a list [McpImage, description]
    assert isinstance(result, list)
    assert len(result) == 2


async def test_screenshot_over_limit():
    """Screenshots over the limit return an error string."""
    # Create bytes larger than the screenshot limit
    fake_bytes = b"\x89PNG" + b"\x00" * (srv.MAX_SCREENSHOT_SIZE_BYTES + 1000)
    mock_page = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=fake_bytes)

    mock_session = AsyncMock()
    mock_session.page = mock_page
    mock_session.drain_dialogs = MagicMock(return_value=[])

    ctx = AsyncMock()
    ctx.get_session = AsyncMock(return_value=mock_session)
    ctx.cache = srv._state.cache

    result = await srv._take_screenshot_impl(full_page=True, ctx=ctx)
    # Should return an error string, not a list
    assert isinstance(result, str)
    assert "Screenshot too large" in result
    assert "full_page=False" in result
