# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DiagnosticResult methods: to_dict, has_issues, warning_message."""

from __future__ import annotations

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


class TestHasIssues:
    def test_empty_result_no_issues(self):
        result = DiagnosticResult()
        assert result.has_issues() is False

    def test_page_state_is_issue(self):
        result = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.BOT_BLOCKED,
                confidence=0.9,
                signals=("test",),
            )
        )
        assert result.has_issues() is True

    def test_antibot_visible_is_issue(self):
        result = DiagnosticResult(
            antibot=AntibotDetection(
                provider=AntibotProvider.TURNSTILE,
                confidence=0.95,
                signals=("test",),
                challenge_visible=True,
            )
        )
        assert result.has_issues() is True

    def test_antibot_not_visible_no_issue(self):
        result = DiagnosticResult(
            antibot=AntibotDetection(
                provider=AntibotProvider.GENERIC,
                confidence=0.7,
                signals=("test",),
                challenge_visible=False,
            )
        )
        assert result.has_issues() is False

    def test_spa_not_hydrated_is_issue(self):
        result = DiagnosticResult(
            spa_status=SpaStatus(
                framework=SpaFramework.REACT,
                hydrated=False,
            )
        )
        assert result.has_issues() is True

    def test_spa_hydrated_no_issue(self):
        result = DiagnosticResult(
            spa_status=SpaStatus(
                framework=SpaFramework.REACT,
                hydrated=True,
            )
        )
        assert result.has_issues() is False


class TestToDict:
    def test_empty_result(self):
        result = DiagnosticResult()
        assert result.to_dict() == {}

    def test_page_state_serialized(self):
        result = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.ERROR_PAGE,
                confidence=0.95,
                signals=("http_status=404",),
                detail="HTTP 404 error",
            )
        )
        d = result.to_dict()
        assert d["page_state"]["state"] == "error_page"
        assert d["page_state"]["confidence"] == 0.95
        assert d["page_state"]["detail"] == "HTTP 404 error"
        assert d["page_state"]["signals"] == ["http_status=404"]

    def test_antibot_serialized(self):
        result = DiagnosticResult(
            antibot=AntibotDetection(
                provider=AntibotProvider.RECAPTCHA,
                confidence=0.95,
                signals=("pattern_match='g-recaptcha'",),
                challenge_visible=True,
            )
        )
        d = result.to_dict()
        assert d["antibot"]["provider"] == "recaptcha"
        assert d["antibot"]["challenge_visible"] is True

    def test_suggested_actions_serialized(self):
        result = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.BOT_BLOCKED,
                confidence=0.9,
                signals=("test",),
            ),
            suggested_actions=(
                SuggestedAction(
                    action="get_page_map",
                    reason="Refresh after block",
                    priority=1,
                ),
            ),
        )
        d = result.to_dict()
        assert len(d["suggested_actions"]) == 1
        assert d["suggested_actions"][0]["action"] == "get_page_map"


class TestWarningMessage:
    def test_no_issues_returns_none(self):
        result = DiagnosticResult()
        assert result.warning_message() is None

    def test_page_state_warning(self):
        result = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.LOGIN_REQUIRED,
                confidence=0.8,
                signals=("test",),
                detail="Login barrier detected",
            )
        )
        msg = result.warning_message()
        assert msg is not None
        assert "login_required" in msg
        assert "Login barrier detected" in msg

    def test_antibot_warning(self):
        result = DiagnosticResult(
            antibot=AntibotDetection(
                provider=AntibotProvider.CLOUDFLARE,
                confidence=0.9,
                signals=("test",),
                challenge_visible=True,
            )
        )
        msg = result.warning_message()
        assert msg is not None
        assert "cloudflare" in msg

    def test_spa_not_hydrated_warning(self):
        result = DiagnosticResult(
            spa_status=SpaStatus(
                framework=SpaFramework.VUE,
                hydrated=False,
            )
        )
        msg = result.warning_message()
        assert msg is not None
        assert "vue" in msg
