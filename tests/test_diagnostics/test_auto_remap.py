# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 auto-remap functionality."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.diagnostics import ActionDiagnosis, ActionFailureType
from pagemap.diagnostics.auto_remap import _REMAPPABLE, MAX_AUTO_REMAPS, maybe_auto_remap


class TestRemappableTypes:
    def test_element_hidden_is_remappable(self):
        assert ActionFailureType.ELEMENT_HIDDEN in _REMAPPABLE

    def test_element_blocked_is_remappable(self):
        assert ActionFailureType.ELEMENT_BLOCKED in _REMAPPABLE

    def test_state_changed_is_remappable(self):
        assert ActionFailureType.STATE_CHANGED in _REMAPPABLE

    def test_timeout_not_remappable(self):
        assert ActionFailureType.TIMEOUT_EXCEEDED not in _REMAPPABLE

    def test_navigation_not_remappable(self):
        assert ActionFailureType.NAVIGATION_UNEXPECTED not in _REMAPPABLE


class TestMaxRemaps:
    def test_max_remaps_is_one(self):
        assert MAX_AUTO_REMAPS == 1


def _make_diagnosis(failure_type: ActionFailureType = ActionFailureType.ELEMENT_HIDDEN) -> ActionDiagnosis:
    return ActionDiagnosis(
        failure_type=failure_type,
        confidence=0.9,
        signals=("test",),
        original_error="element not visible",
        ref=1,
        action="click",
    )


def _make_ctx(remap_count: int = 0):
    """Create a mock RequestContext with mutable cache."""
    cache = MagicMock()
    cache._auto_remap_count = remap_count
    ctx = MagicMock()
    ctx.cache = cache
    ctx.get_session = AsyncMock()
    return ctx


@pytest.mark.asyncio
class TestMaybeAutoRemapAsync:
    async def test_non_remappable_returns_none(self):
        ctx = _make_ctx()
        result = await maybe_auto_remap(
            diagnosis=_make_diagnosis(ActionFailureType.TIMEOUT_EXCEEDED),
            ctx=ctx,
            original_error="timeout",
        )
        assert result is None

    async def test_max_reached_returns_none(self):
        ctx = _make_ctx(remap_count=MAX_AUTO_REMAPS)
        result = await maybe_auto_remap(
            diagnosis=_make_diagnosis(),
            ctx=ctx,
            original_error="not visible",
        )
        assert result is None

    @patch("pagemap.core.serializer.to_agent_prompt")
    @patch("pagemap.core.page_map_builder.build_page_map_live", new_callable=AsyncMock)
    async def test_success_returns_json(self, mock_build, mock_prompt):
        mock_page_map = MagicMock()
        mock_build.return_value = mock_page_map
        mock_prompt.return_value = "## PageMap\ntest content"

        ctx = _make_ctx(remap_count=0)

        result = await maybe_auto_remap(
            diagnosis=_make_diagnosis(),
            ctx=ctx,
            original_error="element not visible",
        )
        assert result is not None
        data = json.loads(result)
        assert data["refs_expired"] is True
        assert data["auto_remap"]["status"] == "success"
        assert ctx.cache.store.called
        assert ctx.cache._auto_remap_count == 1

    async def test_session_error_returns_none(self):
        ctx = _make_ctx()
        ctx.get_session = AsyncMock(side_effect=RuntimeError("no browser"))

        result = await maybe_auto_remap(
            diagnosis=_make_diagnosis(),
            ctx=ctx,
            original_error="not visible",
        )
        # Should never raise — returns None on any error
        assert result is None
