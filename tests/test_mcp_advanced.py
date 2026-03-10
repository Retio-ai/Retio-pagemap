# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for mcp_advanced — Pydantic output models, schema registry, task config."""

from __future__ import annotations

import json

import pytest

from pagemap.mcp_advanced import (
    TOOL_OUTPUT_SCHEMAS,
    BatchGetPageMapResult,
    BatchPageMapEntry,
    ChangeDetails,
    ChangeType,
    DialogInfo,
    ExecuteActionResult,
    FillFormResult,
    FormFieldResult,
    GetPageMapResult,
    GetPageStateResult,
    NavigateBackResult,
    NavigationHint,
    PageMetadata,
    ScrollPageResult,
    TaskSupportConfig,
    ToolError,
    WaitForResult,
    get_output_schema,
)

# ── ChangeType enum ─────────────────────────────────────────────────────


class TestChangeType:
    def test_values(self):
        assert set(ChangeType) == {"none", "content", "navigation", "dialog", "download"}

    def test_str_enum(self):
        assert ChangeType.none == "none"
        assert isinstance(ChangeType.navigation, str)


# ── Model creation and serialization ────────────────────────────────────


class TestGetPageMapResult:
    def test_minimal(self):
        result = GetPageMapResult(
            url="https://example.com",
            title="Example",
            interactables=5,
            pruned_context="Hello world",
            pruned_tokens=3,
            generation_ms=42.5,
        )
        assert result.url == "https://example.com"
        assert result.page_type == "unknown"
        assert result.metadata is None
        assert result.navigation_hints == []
        assert result.warnings == []

    def test_full(self):
        result = GetPageMapResult(
            url="https://example.com",
            title="Example",
            page_type="product",
            interactables=10,
            pruned_context="content",
            pruned_tokens=100,
            generation_ms=250.0,
            metadata=PageMetadata(language="en", viewport_width=1280, viewport_height=720),
            navigation_hints=[NavigationHint(ref=1, label="Home", url="/")],
            warnings=["slow page"],
        )
        data = result.model_dump()
        assert data["page_type"] == "product"
        assert data["metadata"]["language"] == "en"
        assert len(data["navigation_hints"]) == 1

    def test_json_roundtrip(self):
        result = GetPageMapResult(
            url="https://example.com",
            title="Test",
            interactables=0,
            pruned_context="",
            pruned_tokens=0,
            generation_ms=1.0,
        )
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["url"] == "https://example.com"
        restored = GetPageMapResult.model_validate(parsed)
        assert restored == result


class TestExecuteActionResult:
    def test_defaults(self):
        result = ExecuteActionResult(
            description="Clicked button",
            current_url="https://example.com",
        )
        assert result.change == ChangeType.none
        assert result.refs_expired is True
        assert result.change_details is None
        assert result.dialogs == []

    def test_with_dialog(self):
        result = ExecuteActionResult(
            description="Clicked delete",
            current_url="https://example.com",
            change=ChangeType.dialog,
            dialogs=[DialogInfo(type="confirm", message="Are you sure?", accepted=True)],
        )
        data = result.model_dump()
        assert data["change"] == "dialog"
        assert len(data["dialogs"]) == 1

    def test_with_navigation(self):
        result = ExecuteActionResult(
            description="Navigated",
            current_url="https://example.com/new",
            change=ChangeType.navigation,
            change_details=ChangeDetails(
                old_url="https://example.com/old",
                new_url="https://example.com/new",
                description="Page navigation",
            ),
        )
        assert result.change_details.old_url == "https://example.com/old"


class TestGetPageStateResult:
    def test_creation(self):
        result = GetPageStateResult(
            url="https://example.com",
            title="Test",
            has_page_map=True,
            scroll_y=100,
            viewport_height=720,
            total_height=5000,
        )
        assert result.has_page_map is True
        assert result.scroll_y == 100


class TestNavigateBackResult:
    def test_creation(self):
        result = NavigateBackResult(
            previous_url="https://example.com/page2",
            current_url="https://example.com/page1",
        )
        assert result.refs_expired is True


class TestScrollPageResult:
    def test_at_bottom(self):
        result = ScrollPageResult(
            direction="down",
            scroll_y=4280,
            viewport_height=720,
            total_height=5000,
            at_bottom=True,
        )
        assert result.at_bottom is True
        assert result.at_top is False


class TestFillFormResult:
    def test_mixed_results(self):
        result = FillFormResult(
            fields_completed=2,
            results=[
                FormFieldResult(ref=1, action="type", success=True),
                FormFieldResult(ref=2, action="select", success=True),
                FormFieldResult(ref=3, action="click", success=False, error="Element not found"),
            ],
        )
        assert result.fields_completed == 2
        assert len(result.results) == 3
        assert result.results[2].error == "Element not found"


class TestWaitForResult:
    def test_timeout(self):
        result = WaitForResult(condition_met=False, elapsed_ms=10000.0)
        assert result.condition_met is False

    def test_success(self):
        result = WaitForResult(condition_met=True, elapsed_ms=250.5)
        assert result.condition_met is True


class TestBatchGetPageMapResult:
    def test_creation(self):
        entry = BatchPageMapEntry(
            url="https://example.com",
            success=True,
            page_map=GetPageMapResult(
                url="https://example.com",
                title="Test",
                interactables=5,
                pruned_context="content",
                pruned_tokens=10,
                generation_ms=100.0,
            ),
        )
        result = BatchGetPageMapResult(
            total=2,
            succeeded=1,
            failed=1,
            results=[
                entry,
                BatchPageMapEntry(url="https://bad.com", success=False, error="Timeout"),
            ],
        )
        assert result.total == 2
        assert result.results[0].page_map is not None
        assert result.results[1].error == "Timeout"


class TestToolError:
    def test_defaults(self):
        err = ToolError(error="Something went wrong")
        assert err.type == "about:blank"
        assert err.status == 500
        assert err.refs_expired is False
        assert err.recovery_hint == ""

    def test_rfc9457_style(self):
        err = ToolError(
            error="Element not found",
            type="https://www.retio.ai/pagemap/errors/ref-not-found",
            status=404,
            refs_expired=True,
            recovery_hint="Call get_page_map to refresh refs",
        )
        data = err.model_dump()
        assert data["status"] == 404
        assert data["refs_expired"] is True


# ── Output Schema Registry ──────────────────────────────────────────────


class TestOutputSchemaRegistry:
    def test_all_tools_mapped(self):
        expected_tools = {
            "get_page_map",
            "execute_action",
            "get_page_state",
            "take_screenshot",
            "navigate_back",
            "scroll_page",
            "fill_form",
            "wait_for",
            "batch_get_page_map",
            "open_tab",
            "switch_tab",
            "list_tabs",
            "close_tab",
        }
        assert set(TOOL_OUTPUT_SCHEMAS.keys()) == expected_tools

    def test_take_screenshot_is_none(self):
        assert TOOL_OUTPUT_SCHEMAS["take_screenshot"] is None

    def test_all_non_none_are_basemodel(self):
        from pydantic import BaseModel

        for name, model in TOOL_OUTPUT_SCHEMAS.items():
            if model is not None:
                assert issubclass(model, BaseModel), f"{name} should be a BaseModel subclass"

    def test_get_output_schema_returns_dict(self):
        schema = get_output_schema("get_page_map")
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "url" in schema["properties"]
        assert "title" in schema["properties"]

    def test_get_output_schema_none_for_screenshot(self):
        assert get_output_schema("take_screenshot") is None

    def test_get_output_schema_none_for_unknown(self):
        assert get_output_schema("nonexistent_tool") is None

    def test_all_schemas_are_valid_json_schema(self):
        for name, model in TOOL_OUTPUT_SCHEMAS.items():
            if model is None:
                continue
            schema = get_output_schema(name)
            assert isinstance(schema, dict), f"{name} schema should be a dict"
            # Verify it's JSON-serializable
            json_str = json.dumps(schema)
            assert json.loads(json_str) == schema

    def test_schema_has_required_fields(self):
        schema = get_output_schema("execute_action")
        assert "properties" in schema
        assert "description" in schema["properties"]
        assert "current_url" in schema["properties"]
        assert "change" in schema["properties"]

    def test_tool_error_schema(self):
        schema = ToolError.model_json_schema(mode="serialization")
        assert "error" in schema["properties"]
        assert "type" in schema["properties"]
        assert "status" in schema["properties"]


# ── TaskSupportConfig ───────────────────────────────────────────────────


class TestTaskSupportConfig:
    def test_defaults(self):
        config = TaskSupportConfig()
        assert config.enabled is False
        assert config.store_type == "in_memory"
        assert config.default_ttl_ms == 60_000

    def test_custom(self):
        config = TaskSupportConfig(enabled=True, store_type="redis", default_ttl_ms=120_000)
        assert config.enabled is True
        assert config.store_type == "redis"

    def test_frozen(self):
        config = TaskSupportConfig()
        with pytest.raises(AttributeError):
            config.enabled = True  # type: ignore[misc]
