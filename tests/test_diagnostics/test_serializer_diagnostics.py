# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for serializer ## Diagnostics section output."""

from __future__ import annotations

from pagemap import PageMap
from pagemap.diagnostics import (
    AntibotDetection,
    AntibotProvider,
    DiagnosticResult,
    PageFailureState,
    PageStateDiagnosis,
    SpaFramework,
    SpaStatus,
    SuggestedAction,
)
from pagemap.serializer import to_agent_prompt


def _make_page_map(diagnostics: DiagnosticResult | None = None) -> PageMap:
    return PageMap(
        url="https://example.com",
        title="Test Page",
        page_type="unknown",
        interactables=[],
        pruned_context="Test content",
        pruned_tokens=10,
        generation_ms=50.0,
        diagnostics=diagnostics,
    )


class TestSerializerDiagnosticsSection:
    def test_no_diagnostics_no_section(self):
        prompt = to_agent_prompt(_make_page_map())
        assert "## Diagnostics" not in prompt

    def test_healthy_diagnostics_no_section(self):
        diag = DiagnosticResult()  # no issues
        prompt = to_agent_prompt(_make_page_map(diagnostics=diag))
        assert "## Diagnostics" not in prompt

    def test_page_state_renders_section(self):
        diag = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.BOT_BLOCKED,
                confidence=0.9,
                signals=("page_type=blocked",),
                detail="Page classified as blocked",
            )
        )
        prompt = to_agent_prompt(_make_page_map(diagnostics=diag))
        assert "## Diagnostics" in prompt
        assert "bot_blocked" in prompt
        assert "Page classified as blocked" in prompt

    def test_antibot_visible_renders(self):
        diag = DiagnosticResult(
            antibot=AntibotDetection(
                provider=AntibotProvider.TURNSTILE,
                confidence=0.95,
                signals=("test",),
                challenge_visible=True,
            )
        )
        prompt = to_agent_prompt(_make_page_map(diagnostics=diag))
        assert "## Diagnostics" in prompt
        assert "turnstile" in prompt

    def test_spa_not_hydrated_renders(self):
        diag = DiagnosticResult(
            spa_status=SpaStatus(
                framework=SpaFramework.REACT,
                hydrated=False,
            )
        )
        prompt = to_agent_prompt(_make_page_map(diagnostics=diag))
        assert "## Diagnostics" in prompt
        assert "react" in prompt

    def test_suggested_actions_render(self):
        diag = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.ERROR_PAGE,
                confidence=0.9,
                signals=("test",),
            ),
            suggested_actions=(
                SuggestedAction(
                    action="navigate",
                    reason="Try a different URL",
                    priority=1,
                ),
            ),
        )
        prompt = to_agent_prompt(_make_page_map(diagnostics=diag))
        assert "Try a different URL" in prompt
        assert "navigate" in prompt
