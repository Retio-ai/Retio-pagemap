"""Tests for AX tree failure isolation (P8).

Covers:
1. Level 1: detect_all() isolates AX tree failures
2. Level 2: build_page_map_live() isolates detect_all() failures
3. Warning propagation through PageMap to serializer
4. Degraded mode: pruned context still available when detection fails
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from pagemap import Interactable, PageMap
from pagemap.interactive_detector import detect_all
from pagemap.serializer import to_agent_prompt, to_json

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(**overrides) -> PageMap:
    defaults = {
        "url": "https://example.com",
        "title": "Test",
        "page_type": "unknown",
        "interactables": [],
        "pruned_context": "Price: 10,000",
        "pruned_tokens": 50,
        "generation_ms": 42.0,
    }
    defaults.update(overrides)
    return PageMap(**defaults)


# ── Level 1: detect_all() isolation ──────────────────────────────────


class TestDetectAllAXIsolation:
    """Level 1: AX tree failure inside detect_all() returns [] + warning."""

    async def test_cdp_session_creation_failure(self):
        """CDP session creation failure -> empty list + warning."""
        mock_page = MagicMock()
        mock_page.context = MagicMock()
        mock_page.context.new_cdp_session = AsyncMock(side_effect=Exception("Target closed"))

        elements, warnings = await detect_all(mock_page, enable_tier3=False)

        assert elements == []
        assert len(warnings) == 1
        assert "AX tree detection failed" in warnings[0]
        assert "Exception" in warnings[0]

    async def test_get_full_ax_tree_protocol_error(self):
        """getFullAXTree CDP protocol error -> empty + warning."""
        mock_cdp = AsyncMock()
        mock_cdp.send = AsyncMock(side_effect=Exception("Protocol error"))
        mock_cdp.detach = AsyncMock()

        mock_page = MagicMock()
        mock_page.context = MagicMock()
        mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

        elements, warnings = await detect_all(mock_page, enable_tier3=False)

        assert elements == []
        assert len(warnings) == 1
        assert "AX tree detection failed" in warnings[0]

    async def test_no_warnings_on_success(self):
        """Successful detection returns empty warnings list."""
        mock_cdp = AsyncMock()
        mock_cdp.send = AsyncMock(return_value={"nodes": []})
        mock_cdp.detach = AsyncMock()

        mock_page = MagicMock()
        mock_page.context = MagicMock()
        mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

        elements, warnings = await detect_all(mock_page, enable_tier3=False)

        assert warnings == []

    async def test_warning_includes_exception_type(self):
        """Warning message includes the exception class name for diagnostics."""
        mock_page = MagicMock()
        mock_page.context = MagicMock()
        mock_page.context.new_cdp_session = AsyncMock(side_effect=TimeoutError("CDP timed out"))

        elements, warnings = await detect_all(mock_page, enable_tier3=False)

        assert "TimeoutError" in warnings[0]


# ── Level 2: build_page_map_live() isolation ──────────────────────────


class TestBuildPageMapLiveIsolation:
    """Level 2: detect_all() total failure still produces a PageMap."""

    async def test_detect_all_crash_still_returns_pagemap(self):
        """Even if detect_all crashes, PageMap with pruned context is returned."""
        from pagemap.page_map_builder import build_page_map_live

        mock_session = MagicMock()
        mock_session.page = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.get_page_url = AsyncMock(return_value="https://example.com")
        mock_session.get_page_title = AsyncMock(return_value="Test Page")
        mock_session.get_page_html = AsyncMock(return_value="<html><body><p>Price: 10,000</p></body></html>")

        with patch(
            "pagemap.page_map_builder.detect_all",
            side_effect=RuntimeError("unexpected fatal error"),
        ):
            page_map = await build_page_map_live(session=mock_session)

        assert page_map.interactables == []
        assert len(page_map.warnings) >= 1
        assert "detection failed" in page_map.warnings[0].lower()
        assert page_map.pruned_context  # pruned context still present

    async def test_detect_all_warning_propagated_to_pagemap(self):
        """AX tree warning from detect_all appears in PageMap.warnings."""
        from pagemap.page_map_builder import build_page_map_live

        mock_session = MagicMock()
        mock_session.page = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.get_page_url = AsyncMock(return_value="https://example.com")
        mock_session.get_page_title = AsyncMock(return_value="Test Page")
        mock_session.get_page_html = AsyncMock(return_value="<html><body>content</body></html>")

        ax_warning = "AX tree detection failed (Exception): interactive elements may be incomplete"
        with patch(
            "pagemap.page_map_builder.detect_all",
            return_value=([], [ax_warning]),
        ):
            page_map = await build_page_map_live(session=mock_session)

        assert any("AX tree" in w for w in page_map.warnings)


# ── Warning rendering in serializer ──────────────────────────────────


class TestWarningsSerialization:
    """Warnings appear in both agent prompt and JSON output."""

    def test_agent_prompt_shows_warnings_section(self):
        pm = _make_page_map(warnings=["AX tree detection failed: elements may be incomplete"])
        prompt = to_agent_prompt(pm)

        assert "## Warnings" in prompt
        assert "AX tree detection failed" in prompt

    def test_agent_prompt_no_warnings_when_empty(self):
        pm = _make_page_map(warnings=[])
        prompt = to_agent_prompt(pm)

        assert "## Warnings" not in prompt

    def test_warnings_rendered_before_actions(self):
        items = [
            Interactable(
                ref=1,
                role="button",
                name="OK",
                affordance="click",
                region="main",
                tier=1,
            )
        ]
        pm = _make_page_map(interactables=items, warnings=["Detection degraded"])
        prompt = to_agent_prompt(pm)

        warnings_pos = prompt.index("## Warnings")
        actions_pos = prompt.index("## Actions")
        assert warnings_pos < actions_pos

    def test_json_includes_warnings_when_present(self):
        pm = _make_page_map(warnings=["AX tree failed"])
        data = json.loads(to_json(pm))

        assert "warnings" in data
        assert data["warnings"] == ["AX tree failed"]

    def test_json_omits_warnings_when_empty(self):
        pm = _make_page_map(warnings=[])
        data = json.loads(to_json(pm))

        assert "warnings" not in data

    def test_multiple_warnings_rendered(self):
        pm = _make_page_map(warnings=["Warning 1", "Warning 2"])
        prompt = to_agent_prompt(pm)

        assert "- Warning 1" in prompt
        assert "- Warning 2" in prompt


# ── PageMap dataclass backward compat ────────────────────────────────


class TestPageMapWarningsField:
    """PageMap.warnings field backward compatibility."""

    def test_default_warnings_is_empty_list(self):
        pm = _make_page_map()
        assert pm.warnings == []

    def test_constructor_without_warnings_still_works(self):
        pm = PageMap(
            url="https://example.com",
            title="Test",
            page_type="unknown",
            interactables=[],
            pruned_context="",
            pruned_tokens=0,
            generation_ms=0.0,
        )
        assert pm.warnings == []
