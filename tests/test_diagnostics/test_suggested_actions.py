# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 suggested recovery actions."""

from __future__ import annotations

from pagemap.diagnostics import (
    ActionDiagnosis,
    ActionFailureType,
    PageFailureState,
    PageStateDiagnosis,
)
from pagemap.diagnostics.suggested_actions import (
    suggest_action_recovery,
    suggest_page_recovery,
)


class TestPageRecovery:
    def test_bot_blocked(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.BOT_BLOCKED,
            confidence=0.95,
            signals=("page_type=blocked",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "wait_for"

    def test_empty_results(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.EMPTY_RESULTS,
            confidence=0.85,
            signals=("text_match",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "navigate"

    def test_out_of_stock(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.OUT_OF_STOCK,
            confidence=0.80,
            signals=("text_match",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "get_page_map"

    def test_login_required(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.LOGIN_REQUIRED,
            confidence=0.70,
            signals=("text_match",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "execute_action"

    def test_error_page(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.ERROR_PAGE,
            confidence=0.95,
            signals=("http_status=404",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1

    def test_age_verification(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.AGE_VERIFICATION,
            confidence=0.80,
            signals=("text_match",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1

    def test_region_restricted(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.REGION_RESTRICTED,
            confidence=0.80,
            signals=("text_match",),
        )
        result = suggest_page_recovery(diag)
        assert len(result) >= 1

    def test_all_states_covered(self):
        """Every PageFailureState should produce at least one suggestion."""
        for state in PageFailureState:
            diag = PageStateDiagnosis(
                state=state,
                confidence=0.80,
                signals=("test",),
            )
            result = suggest_page_recovery(diag)
            assert len(result) >= 1, f"No suggestions for {state.value}"


class TestActionRecovery:
    def test_element_hidden(self):
        diag = ActionDiagnosis(
            failure_type=ActionFailureType.ELEMENT_HIDDEN,
            confidence=0.90,
            signals=("test",),
        )
        result = suggest_action_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "scroll_page"

    def test_state_changed(self):
        diag = ActionDiagnosis(
            failure_type=ActionFailureType.STATE_CHANGED,
            confidence=0.90,
            signals=("test",),
        )
        result = suggest_action_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "get_page_map"

    def test_timeout_exceeded(self):
        diag = ActionDiagnosis(
            failure_type=ActionFailureType.TIMEOUT_EXCEEDED,
            confidence=0.95,
            signals=("test",),
        )
        result = suggest_action_recovery(diag)
        assert len(result) >= 1
        assert result[0].action == "wait_for"

    def test_element_blocked(self):
        diag = ActionDiagnosis(
            failure_type=ActionFailureType.ELEMENT_BLOCKED,
            confidence=0.90,
            signals=("test",),
        )
        result = suggest_action_recovery(diag)
        assert len(result) >= 1

    def test_navigation_unexpected(self):
        diag = ActionDiagnosis(
            failure_type=ActionFailureType.NAVIGATION_UNEXPECTED,
            confidence=0.85,
            signals=("test",),
        )
        result = suggest_action_recovery(diag)
        assert len(result) >= 1
