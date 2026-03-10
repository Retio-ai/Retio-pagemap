# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for detail_level / max_content_tokens pruning budget resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pagemap.server import _DETAIL_LEVEL_TOKENS, _resolve_pruned_token_budget

# ── Unit tests: _resolve_pruned_token_budget ──────────────────────────


class TestResolveBudgetDefault:
    def test_none_none_returns_compact(self):
        assert _resolve_pruned_token_budget(None, None) == _DETAIL_LEVEL_TOKENS["compact"]


class TestResolveBudgetCompact:
    def test_compact_returns_1500(self):
        assert _resolve_pruned_token_budget("compact", None) == 1500


class TestResolveBudgetStandard:
    def test_standard_returns_3000(self):
        assert _resolve_pruned_token_budget("standard", None) == 3000


class TestResolveBudgetVerbose:
    def test_verbose_returns_12000(self):
        assert _resolve_pruned_token_budget("verbose", None) == 12000


class TestResolveBudgetMaxTokensOverride:
    def test_max_tokens_takes_precedence(self):
        """max_content_tokens overrides detail_level."""
        assert _resolve_pruned_token_budget("compact", 5000) == 5000


class TestResolveBudgetMaxTokensClamp:
    def test_clamp_upper(self):
        assert _resolve_pruned_token_budget(None, 999999) == 50000

    def test_clamp_lower(self):
        assert _resolve_pruned_token_budget(None, 10) == 100


class TestResolveBudgetUnknownLevel:
    def test_unknown_falls_back_to_compact(self):
        assert _resolve_pruned_token_budget("unknown_level", None) == _DETAIL_LEVEL_TOKENS["compact"]


# ── Integration tests: get_page_map passes budget to builder ──────────


@pytest.mark.asyncio
class TestGetPageMapDetailLevelIntegration:
    async def test_detail_level_standard_passes_3000(self, monkeypatch):
        """detail_level='standard' passes budget=3000 to build_page_map_live."""
        import pagemap.server as srv

        # Mock session
        mock_session = AsyncMock()
        mock_session.page = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.read_mutation_severity = AsyncMock(return_value=0)
        monkeypatch.setattr("pagemap.server._get_session", AsyncMock(return_value=mock_session))

        # Mock fingerprint → force Tier C (full rebuild)
        monkeypatch.setattr("pagemap.server.capture_dom_fingerprint", AsyncMock(return_value=None))

        # Track what budget gets passed to build_page_map_live
        captured_kwargs = {}
        mock_pm = MagicMock()
        mock_pm.metadata = {}
        mock_pm.url = "https://example.com"
        mock_pm.interactables = []

        async def mock_build(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_pm

        monkeypatch.setattr("pagemap.page_map_builder.build_page_map_live", mock_build)
        monkeypatch.setattr("pagemap.serializer.to_agent_prompt_secure", lambda *a, **kw: "mocked output")

        # Clear cache to force full build
        srv._state.cache.invalidate_all()

        await srv._get_page_map_impl(
            "https://example.com",
            task_hint=None,
            detail_level="standard",
            max_content_tokens=None,
            ctx=srv._create_stdio_context(),
        )
        assert captured_kwargs["max_pruned_tokens"] == 3000

    async def test_max_content_tokens_passes_value(self, monkeypatch):
        """max_content_tokens=5000 passes budget=5000 to build_page_map_live."""
        import pagemap.server as srv

        mock_session = AsyncMock()
        mock_session.page = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.read_mutation_severity = AsyncMock(return_value=0)
        monkeypatch.setattr("pagemap.server._get_session", AsyncMock(return_value=mock_session))

        monkeypatch.setattr("pagemap.server.capture_dom_fingerprint", AsyncMock(return_value=None))

        captured_kwargs = {}
        mock_pm = MagicMock()
        mock_pm.metadata = {}
        mock_pm.url = "https://example.com"
        mock_pm.interactables = []

        async def mock_build(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_pm

        monkeypatch.setattr("pagemap.page_map_builder.build_page_map_live", mock_build)
        monkeypatch.setattr("pagemap.serializer.to_agent_prompt_secure", lambda *a, **kw: "mocked output")

        srv._state.cache.invalidate_all()

        await srv._get_page_map_impl(
            "https://example.com",
            task_hint=None,
            detail_level=None,
            max_content_tokens=5000,
            ctx=srv._create_stdio_context(),
        )
        assert captured_kwargs["max_pruned_tokens"] == 5000
