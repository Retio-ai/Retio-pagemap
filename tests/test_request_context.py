# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for RequestContext (Phase α) and TOOL_ERROR telemetry integration.

Validates:
1. RequestContext dataclass contract (frozen, slots, fields)
2. _create_stdio_context() factory behaviour
3. Architectural invariants for _impl functions (AST + inspect)
4. ctx injection path — Phase β readiness
5. _safe_error → TOOL_ERROR telemetry emission
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pagemap.server as srv
from pagemap import Interactable, PageMap
from pagemap.cache import PageMapCache
from pagemap.server import RequestContext, _create_stdio_context, _safe_error
from pagemap.template_cache import InMemoryTemplateCache

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
    """Minimal PageMap — reuses project pattern from test_agent_friendly_errors.py."""
    return PageMap(
        url=url,
        title="Test Page",
        page_type="unknown",
        interactables=[
            Interactable(
                ref=1,
                role="button",
                name="Submit",
                affordance="click",
                region="main",
                tier=1,
                selector="#submit-btn",
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_ctx(**overrides) -> RequestContext:
    """RequestContext with real PageMapCache + InMemoryTemplateCache (fake infra).

    Only mock get_session (I/O boundary).
    """
    defaults = {
        "request_id": "test000000ab",
        "session_id": "testsession0001",
        "client_id": "",
        "cache": PageMapCache(),
        "template_cache": InMemoryTemplateCache(),
        "get_session": AsyncMock(),
    }
    defaults.update(overrides)
    return RequestContext(**defaults)


# ── TestRequestContextContract ───────────────────────────────────────


class TestRequestContextContract:
    """Dataclass contract tests — design intent, not language features."""

    def test_immutable_after_creation(self):
        ctx = _make_ctx()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ctx.request_id = "x"
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            del ctx.session_id

    def test_uses_slots(self):
        ctx = _make_ctx()
        assert not hasattr(ctx, "__dict__"), "RequestContext must use __slots__"
        with pytest.raises((AttributeError, TypeError)):
            ctx.foo = 1  # type: ignore[attr-defined]

    def test_get_session_hidden_from_repr(self):
        ctx = _make_ctx()
        r = repr(ctx)
        assert "get_session" not in r, "callable should not appear in repr (log safety)"

    def test_fields_match_specification(self):
        fields = {f.name: f.type for f in dataclasses.fields(RequestContext)}
        expected_names = {
            "request_id",
            "session_id",
            "client_id",
            "cache",
            "template_cache",
            "client_ip",
            "get_session",
        }
        assert set(fields.keys()) == expected_names
        assert len(fields) == 7


# ── TestCreateStdioContext ───────────────────────────────────────────


class TestCreateStdioContext:
    """Tests for _create_stdio_context() factory."""

    def test_returns_request_context(self):
        ctx = _create_stdio_context()
        assert isinstance(ctx, RequestContext)

    def test_request_id_format_and_length(self):
        ctx = _create_stdio_context()
        assert re.fullmatch(r"[0-9a-f]{12}", ctx.request_id), f"request_id must be 12-char hex, got: {ctx.request_id!r}"

    def test_session_id_from_state(self):
        ctx = _create_stdio_context()
        assert ctx.session_id == srv._state.session_id

    def test_client_id_empty_for_stdio(self):
        ctx = _create_stdio_context()
        assert ctx.client_id == "", "STDIO transport must use empty client_id"

    def test_cache_identity_with_state(self):
        ctx = _create_stdio_context()
        assert ctx.cache is srv._state.cache

    def test_template_cache_identity_with_state(self):
        ctx = _create_stdio_context()
        assert ctx.template_cache is srv._state.template_cache

    def test_get_session_is_module_wrapper(self):
        ctx = _create_stdio_context()
        assert ctx.get_session is srv._get_session

    def test_unique_request_ids(self):
        ids = {_create_stdio_context().request_id for _ in range(50)}
        assert len(ids) == 50, "request_ids must be unique (uuid collision)"


# ── TestImplArchitecturalInvariants ──────────────────────────────────

_IMPL_NAMES = [
    "_get_page_map_impl",
    "_execute_action_impl",
    "_get_page_state_impl",
    "_take_screenshot_impl",
    "_navigate_back_impl",
    "_scroll_page_impl",
    "_fill_form_impl",
    "_wait_for_impl",
    "_batch_get_page_map_impl",
]


def _get_server_ast() -> ast.Module:
    """Parse server.py into AST (cached per test session)."""
    source = inspect.getsource(srv)
    return ast.parse(textwrap.dedent(source))


class TestImplArchitecturalInvariants:
    """AST + inspect based architectural guards for Phase α."""

    def test_exactly_nine_impl_functions_exist(self):
        tree = _get_server_ast()
        impl_funcs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.endswith("_impl")
        ]
        assert sorted(impl_funcs) == sorted(_IMPL_NAMES), (
            f"Expected exactly 9 _impl functions. Found: {sorted(impl_funcs)}"
        )

    @pytest.mark.parametrize("name", _IMPL_NAMES)
    def test_accepts_ctx_kwonly(self, name: str):
        func = getattr(srv, name)
        sig = inspect.signature(func)
        assert "ctx" in sig.parameters, f"{name} missing 'ctx' parameter"
        param = sig.parameters["ctx"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, f"{name}: 'ctx' must be keyword-only"
        assert param.default is None, f"{name}: 'ctx' default must be None, got {param.default!r}"

    @pytest.mark.parametrize("name", _IMPL_NAMES)
    def test_no_state_reference_in_body(self, name: str):
        """_impl functions must not reference _state directly in their body.

        Indirect via _create_stdio_context() is acceptable.
        """
        tree = _get_server_ast()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                # Walk the function body, skip the top-level to find attribute refs
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and child.id == "_state":
                        pytest.fail(
                            f"{name} references '_state' directly at line ~{child.lineno}. "
                            "Use ctx fields or _create_stdio_context() instead."
                        )

    @pytest.mark.parametrize("name", _IMPL_NAMES)
    def test_has_ctx_none_guard(self, name: str):
        """Each _impl must have `if ctx is None:` + `_create_stdio_context()` fallback."""
        source = inspect.getsource(getattr(srv, name))
        assert re.search(r"if ctx is None:", source), f"{name} missing 'if ctx is None:' guard"
        assert "_create_stdio_context()" in source, f"{name} missing '_create_stdio_context()' fallback"


# ── TestCtxInjection ─────────────────────────────────────────────────


class TestCtxInjection:
    """Phase β readiness — verify ctx injection overrides module _state."""

    async def test_get_page_state_uses_injected_cache(self):
        """Injected ctx.cache is used, not srv._state.cache."""
        separate_cache = PageMapCache()
        pm = _make_page_map()
        separate_cache.store(pm, None)

        mock_session = AsyncMock()
        mock_session.get_page_url = AsyncMock(return_value="https://example.com")
        mock_session.get_page_title = AsyncMock(return_value="Test Page")

        ctx = _make_ctx(cache=separate_cache, get_session=AsyncMock(return_value=mock_session))

        result = await srv._get_page_state_impl(ctx=ctx)
        data = json.loads(result)
        assert data["has_page_map"] is True
        assert data["page_map_interactables"] == 1
        # Global state must be untouched
        assert srv._state.cache.active is None

    async def test_state_cache_isolation(self):
        """ctx(B) uses cache B, not cache A from srv._state."""
        pm_a = _make_page_map(url="https://a.com")
        srv._state.cache.store(pm_a, None)

        cache_b = PageMapCache()
        pm_b = _make_page_map(url="https://b.com")
        cache_b.store(pm_b, None)

        mock_session = AsyncMock()
        mock_session.get_page_url = AsyncMock(return_value="https://b.com")
        mock_session.get_page_title = AsyncMock(return_value="B Page")

        ctx = _make_ctx(cache=cache_b, get_session=AsyncMock(return_value=mock_session))

        result = await srv._get_page_state_impl(ctx=ctx)
        data = json.loads(result)
        assert data["url"] == "https://b.com"
        assert data["has_page_map"] is True
        # State A is untouched
        assert srv._state.cache.active.url == "https://a.com"

    async def test_execute_action_finds_ref_in_injected_cache(self):
        """_execute_action_impl uses injected ctx.cache to find refs."""
        separate_cache = PageMapCache()
        pm = _make_page_map()
        separate_cache.store(pm, None)

        ctx = _make_ctx(cache=separate_cache)

        # ref=999 doesn't exist → should give "ref not found" (proves cache was read)
        result = await srv._execute_action_impl(ref=999, action="click", ctx=ctx)
        assert "ref [999] not found" in result
        # But ref=1 does exist in separate_cache → should try to act (will fail on session)
        assert srv._state.cache.active is None  # global untouched

    async def test_fallback_skipped_when_ctx_provided(self):
        """When ctx is explicitly provided, _create_stdio_context is not called."""
        mock_session = AsyncMock()
        mock_session.get_page_url = AsyncMock(return_value="https://example.com")
        mock_session.get_page_title = AsyncMock(return_value="Test")

        ctx = _make_ctx(get_session=AsyncMock(return_value=mock_session))

        with patch("pagemap.server._create_stdio_context") as mock_factory:
            await srv._get_page_state_impl(ctx=ctx)
            mock_factory.assert_not_called()


# ── TestToolErrorIntegration ─────────────────────────────────────────


class TestToolErrorIntegration:
    """TOOL_ERROR telemetry emission — complements test_agent_friendly_errors.py."""

    def test_safe_error_emits_tool_error(self):
        pytest.importorskip("pagemap.telemetry")
        with patch("pagemap.server._telem") as mock_telem:
            _safe_error("get_page_map", ValueError("boom"))
            mock_telem.assert_called_once()
            args = mock_telem.call_args
            assert args[0][0] == "pagemap.tool.error"
            payload = args[0][1]
            assert payload["context"] == "get_page_map"
            assert payload["error_type"] == "ValueError"

    def test_safe_error_survives_telem_failure(self):
        pytest.importorskip("pagemap.telemetry")
        with patch("pagemap.server._telem", side_effect=RuntimeError("telem down")):
            result = _safe_error("get_page_map", ValueError("boom"))
            # Must still return a string, not raise
            assert isinstance(result, str)
            assert "boom" in result

    async def test_execute_action_emits_tool_error(self):
        """Inline TOOL_ERROR emission in _execute_action_impl (L1427-1432)."""
        pytest.importorskip("pagemap.telemetry")
        separate_cache = PageMapCache()
        pm = _make_page_map()
        separate_cache.store(pm, None)

        # Mock session whose locator raises
        mock_session = AsyncMock()
        mock_page = MagicMock()
        mock_page.locator = MagicMock(side_effect=RuntimeError("element gone"))
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.wait_for_timeout = AsyncMock()
        mock_session.page = mock_page

        ctx = _make_ctx(
            cache=separate_cache,
            get_session=AsyncMock(return_value=mock_session),
        )

        with patch("pagemap.server._telem") as mock_telem:
            await srv._execute_action_impl(ref=1, action="click", ctx=ctx)
            # Should have called _telem with TOOL_ERROR
            tool_error_calls = [c for c in mock_telem.call_args_list if c[0][0] == "pagemap.tool.error"]
            assert len(tool_error_calls) >= 1, f"Expected TOOL_ERROR emission, got calls: {mock_telem.call_args_list}"
            payload = tool_error_calls[0][0][1]
            assert payload["context"] == "execute_action"
            assert "error_type" in payload
