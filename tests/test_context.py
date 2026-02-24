# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for context.py — RequestContext extraction from server.py.

Validates:
1. RequestContext is frozen + slotted dataclass
2. All fields accessible
3. Backward-compatible import from pagemap.server
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

from pagemap.cache import PageMapCache
from pagemap.context import RequestContext
from pagemap.template_cache import InMemoryTemplateCache


def _make_ctx(**overrides) -> RequestContext:
    defaults = {
        "request_id": "ctx_test_0001",
        "session_id": "sess_test_0001",
        "client_id": "",
        "cache": PageMapCache(),
        "template_cache": InMemoryTemplateCache(),
        "get_session": AsyncMock(),
    }
    defaults.update(overrides)
    return RequestContext(**defaults)


class TestContextFrozen:
    """RequestContext must be immutable."""

    def test_field_change_raises(self):
        ctx = _make_ctx()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ctx.request_id = "new_value"

    def test_delete_raises(self):
        ctx = _make_ctx()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            del ctx.session_id


class TestContextFields:
    """All specified fields are accessible."""

    def test_all_fields_present(self):
        ctx = _make_ctx()
        assert ctx.request_id == "ctx_test_0001"
        assert ctx.session_id == "sess_test_0001"
        assert ctx.client_id == ""
        assert isinstance(ctx.cache, PageMapCache)
        assert isinstance(ctx.template_cache, InMemoryTemplateCache)
        assert ctx.get_session is not None

    def test_uses_slots(self):
        ctx = _make_ctx()
        assert not hasattr(ctx, "__dict__")
        with pytest.raises((AttributeError, TypeError)):
            ctx.extra = 1  # type: ignore[attr-defined]

    def test_field_count(self):
        fields = dataclasses.fields(RequestContext)
        assert len(fields) == 7

    def test_get_session_hidden_from_repr(self):
        ctx = _make_ctx()
        r = repr(ctx)
        assert "get_session" not in r


class TestImportFromServer:
    """Backward-compatible import: from pagemap.server import RequestContext."""

    def test_import_from_server(self):
        from pagemap.server import RequestContext as ServerRC

        assert ServerRC is RequestContext
