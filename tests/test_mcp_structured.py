# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for mcp_advanced — structured output models, __str__, schema registry, TaskSupport."""

from __future__ import annotations

import json

import pytest

from pagemap.mcp_advanced import (
    TOOL_OUTPUT_SCHEMAS,
    BatchGetPageMapResult,
    ExecuteActionResult,
    FillFormResult,
    FormFieldResult,
    GetPageMapResult,
    GetPageStateResult,
    NavigateBackResult,
    ScrollPageResult,
    TaskSupportConfig,
    WaitForResult,
)

# ── __str__ methods ────────────────────────────────────────────────────


class TestGetPageMapResultStr:
    def test_non_empty(self):
        r = GetPageMapResult(
            url="https://example.com",
            title="Example",
            interactables=5,
            pruned_context="Page content here",
            pruned_tokens=100,
            generation_ms=50.0,
        )
        s = str(r)
        assert len(s) > 0
        assert "Page content here" in s

    def test_returns_pruned_context(self):
        r = GetPageMapResult(
            url="https://example.com",
            title="Example",
            interactables=0,
            pruned_context="the page content",
            pruned_tokens=10,
            generation_ms=1.0,
        )
        assert str(r) == "the page content"


class TestExecuteActionResultStr:
    def test_contains_description(self):
        r = ExecuteActionResult(description="Clicked button", current_url="https://example.com")
        s = str(r)
        assert "Clicked button" in s
        assert "current_url" in s

    def test_json_parseable(self):
        r = ExecuteActionResult(description="Clicked", current_url="https://example.com")
        data = json.loads(str(r))
        assert data["description"] == "Clicked"


class TestGetPageStateResultStr:
    def test_contains_url(self):
        r = GetPageStateResult(url="https://example.com", title="Ex", has_page_map=True)
        s = str(r)
        assert "example.com" in s

    def test_json_parseable(self):
        r = GetPageStateResult(url="https://example.com", title="Ex", has_page_map=False)
        data = json.loads(str(r))
        assert data["has_page_map"] is False


class TestNavigateBackResultStr:
    def test_contains_url(self):
        r = NavigateBackResult(previous_url="https://a.com", current_url="https://b.com")
        s = str(r)
        assert "https://b.com" in s
        assert "Refs are now expired" in s


class TestScrollPageResultStr:
    def test_contains_direction(self):
        r = ScrollPageResult(direction="down", scroll_y=100)
        s = str(r)
        assert "down" in s
        assert "get_page_map" in s


class TestFillFormResultStr:
    def test_format(self):
        r = FillFormResult(
            fields_completed=2,
            results=[
                FormFieldResult(ref=1, action="type", success=True),
                FormFieldResult(ref=2, action="select", success=False, error="not found"),
            ],
        )
        s = str(r)
        assert "2/2 fields completed" in s
        assert "ref=1" in s
        assert "FAIL" in s


class TestWaitForResultStr:
    def test_met(self):
        r = WaitForResult(condition_met=True, elapsed_ms=500.0)
        assert "met" in str(r).lower()

    def test_not_met(self):
        r = WaitForResult(condition_met=False, elapsed_ms=5000.0)
        assert "NOT met" in str(r)


class TestBatchGetPageMapResultStr:
    def test_json_parseable(self):
        r = BatchGetPageMapResult(total=3, succeeded=2, failed=1, results=[])
        data = json.loads(str(r))
        assert data["total"] == 3
        assert data["succeeded"] == 2


# ── model_dump ─────────────────────────────────────────────────────────


class TestModelDump:
    @pytest.mark.parametrize(
        "model_cls,kwargs",
        [
            (
                GetPageMapResult,
                {
                    "url": "u",
                    "title": "t",
                    "interactables": 0,
                    "pruned_context": "p",
                    "pruned_tokens": 0,
                    "generation_ms": 0,
                },
            ),
            (ExecuteActionResult, {"description": "d", "current_url": "u"}),
            (GetPageStateResult, {"url": "u", "title": "t", "has_page_map": True}),
            (NavigateBackResult, {"previous_url": "a", "current_url": "b"}),
            (ScrollPageResult, {"direction": "up", "scroll_y": 0}),
            (FillFormResult, {"fields_completed": 0, "results": []}),
            (WaitForResult, {"condition_met": True, "elapsed_ms": 0}),
            (BatchGetPageMapResult, {"total": 0, "succeeded": 0, "failed": 0, "results": []}),
        ],
    )
    def test_model_dump_json(self, model_cls, kwargs):
        instance = model_cls(**kwargs)
        dump = instance.model_dump(mode="json")
        assert isinstance(dump, dict)
        # Should be JSON-serializable
        json.dumps(dump)


# ── Schema registry ───────────────────────────────────────────────────


class TestToolOutputSchemas:
    def test_thirteen_entries(self):
        assert len(TOOL_OUTPUT_SCHEMAS) == 13

    def test_screenshot_is_none(self):
        assert TOOL_OUTPUT_SCHEMAS["take_screenshot"] is None

    def test_eight_models_plus_five_none(self):
        models = [v for v in TOOL_OUTPUT_SCHEMAS.values() if v is not None]
        nones = [v for v in TOOL_OUTPUT_SCHEMAS.values() if v is None]
        assert len(models) == 8
        assert len(nones) == 5  # take_screenshot + 4 multi-tab tools


# ── TaskSupport config ────────────────────────────────────────────────


class TestTaskSupportConfig:
    def test_defaults(self):
        cfg = TaskSupportConfig()
        assert cfg.enabled is False
        assert cfg.store_type == "in_memory"
        assert cfg.default_ttl_ms == 60_000
