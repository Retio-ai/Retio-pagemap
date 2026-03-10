# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for enhanced diagnostics — Phase A1, D.

Covers: page_state JSON key, error_page cache eviction,
antibot session state, stealth recommendations.
"""

from __future__ import annotations

import pytest

from pagemap.diagnostics import (
    AntibotDetection,
    AntibotProvider,
    AntibotSessionState,
    PageFailureState,
    PageStateDiagnosis,
)
from pagemap.diagnostics.antibot_detector import (
    _stealth_recommendations,
    detect_antibot,
    update_session_state,
)
from pagemap.diagnostics.suggested_actions import suggest_page_recovery

# ── AntibotSessionState ───────────────────────────────────────────


class TestAntibotSessionState:
    def test_initial_state(self):
        state = AntibotSessionState()
        assert state.detection_count == 0
        assert state.consecutive_blocks == 0
        assert state.resolved is False

    def test_update_with_detection(self):
        state = AntibotSessionState()
        detection = AntibotDetection(
            provider=AntibotProvider.CLOUDFLARE,
            confidence=0.9,
            signals=("pattern_match='cf-browser-verification'",),
            challenge_visible=True,
        )
        update_session_state(state, detection)
        assert state.detection_count == 1
        assert state.consecutive_blocks == 1
        assert state.last_provider == "cloudflare"
        assert state.resolved is False
        assert state.first_detected_at != ""

    def test_consecutive_increments(self):
        state = AntibotSessionState()
        detection = AntibotDetection(
            provider=AntibotProvider.TURNSTILE,
            confidence=0.95,
            signals=(),
        )
        update_session_state(state, detection)
        update_session_state(state, detection)
        update_session_state(state, detection)
        assert state.detection_count == 3
        assert state.consecutive_blocks == 3

    def test_resolution_on_none(self):
        state = AntibotSessionState()
        detection = AntibotDetection(
            provider=AntibotProvider.CLOUDFLARE,
            confidence=0.9,
            signals=(),
        )
        update_session_state(state, detection)
        update_session_state(state, detection)
        # Now detection clears
        update_session_state(state, None)
        assert state.resolved is True
        assert state.consecutive_blocks == 0
        assert state.detection_count == 2

    def test_no_resolution_if_no_prior_block(self):
        state = AntibotSessionState()
        update_session_state(state, None)
        assert state.resolved is False

    def test_reset(self):
        state = AntibotSessionState(
            detection_count=5,
            last_provider="cloudflare",
            consecutive_blocks=3,
            first_detected_at="2026-01-01T00:00:00",
            resolved=True,
        )
        state.reset()
        assert state.detection_count == 0
        assert state.consecutive_blocks == 0
        assert state.last_provider == ""
        assert state.resolved is False


# ── Stealth Recommendations ───────────────────────────────────────


class TestStealthRecommendations:
    def test_cloudflare_tips(self):
        tips = _stealth_recommendations(AntibotProvider.CLOUDFLARE, 0)
        assert "slow_down_requests" in tips
        assert "add_random_delays" in tips

    def test_turnstile_tips(self):
        tips = _stealth_recommendations(AntibotProvider.TURNSTILE, 0)
        assert "slow_down_requests" in tips

    def test_akamai_tips(self):
        tips = _stealth_recommendations(AntibotProvider.AKAMAI, 0)
        assert "rotate_user_agent" in tips
        assert "avoid_headless_detection" in tips

    def test_recaptcha_tips(self):
        tips = _stealth_recommendations(AntibotProvider.RECAPTCHA, 0)
        assert "manual_verification_needed" in tips

    def test_hcaptcha_tips(self):
        tips = _stealth_recommendations(AntibotProvider.HCAPTCHA, 0)
        assert "manual_verification_needed" in tips

    def test_generic_tips(self):
        tips = _stealth_recommendations(AntibotProvider.GENERIC, 0)
        assert "increase_page_load_delay" in tips

    def test_consecutive_3_extra_tips(self):
        tips = _stealth_recommendations(AntibotProvider.CLOUDFLARE, 3)
        assert "consider_alternative_url" in tips
        assert "site_may_require_authentication" in tips

    def test_consecutive_below_3_no_extra(self):
        tips = _stealth_recommendations(AntibotProvider.CLOUDFLARE, 2)
        assert "consider_alternative_url" not in tips


# ── Antibot Detection with stealth_tips ───────────────────────────


class TestAntibotDetectionStealth:
    def test_stealth_tips_populated(self):
        html = '<div class="cf-turnstile"></div>'
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert len(result.stealth_tips) > 0

    def test_no_antibot_no_tips(self):
        html = "<html><body><h1>Normal page</h1></body></html>"
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is None


# ── Error Page Cache Eviction ──────────────────────────────────────


class TestErrorPageEviction:
    def test_error_page_forces_evict(self):
        """ERROR_PAGE state should set _force_cache_evict."""
        from pagemap.diagnostics import run_page_diagnostics

        html = """
        <html><head><title>404 Not Found</title></head>
        <body><h1>404 Not Found</h1><p>Page not found.</p></body>
        </html>
        """
        warnings: list[str] = []
        metadata: dict = {}
        result = run_page_diagnostics(
            raw_html=html,
            html_lower=html.lower(),
            page_url="https://example.com/not-found",
            page_type="error",
            interactables=[],
            warnings=warnings,
            metadata=metadata,
            http_status=404,
        )
        if result and result.page_state and result.page_state.state == PageFailureState.ERROR_PAGE:
            assert metadata.get("_force_cache_evict") is True


# ── Suggested Actions Enhanced Params ──────────────────────────────


class TestSuggestedActionsParams:
    def test_bot_blocked_has_stealth_tips(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.BOT_BLOCKED,
            confidence=0.9,
            signals=(),
        )
        actions = suggest_page_recovery(diag)
        wait_action = [a for a in actions if a.action == "wait_for"]
        assert len(wait_action) == 1
        assert "stealth_tips" in wait_action[0].params

    def test_login_has_form_fields_param(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.LOGIN_REQUIRED,
            confidence=0.8,
            signals=(),
        )
        actions = suggest_page_recovery(diag)
        exec_action = [a for a in actions if a.action == "execute_action"]
        assert len(exec_action) == 1
        assert exec_action[0].params.get("form_fields") is True

    def test_age_has_accept_ref_param(self):
        diag = PageStateDiagnosis(
            state=PageFailureState.AGE_VERIFICATION,
            confidence=0.85,
            signals=(),
        )
        actions = suggest_page_recovery(diag)
        exec_action = [a for a in actions if a.action == "execute_action"]
        assert len(exec_action) == 1
        assert exec_action[0].params.get("accept_ref") is True


# ── Hypothesis Fuzz ───────────────────────────────────────────────

try:
    from hypothesis import given, settings, strategies as st

    _HAS_HYPOTHESIS = True
except ImportError:
    _HAS_HYPOTHESIS = False


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestAntibotSessionFuzz:
    @given(
        detections=st.lists(
            st.one_of(
                st.just(None),
                st.builds(
                    AntibotDetection,
                    provider=st.sampled_from(list(AntibotProvider)),
                    confidence=st.floats(min_value=0.0, max_value=1.0),
                    signals=st.just(()),
                    challenge_visible=st.booleans(),
                ),
            ),
            min_size=0,
            max_size=20,
        )
    )
    @settings(max_examples=50, deadline=2000)
    def test_session_state_consistency(self, detections):
        state = AntibotSessionState()
        for det in detections:
            update_session_state(state, det)
        # Invariants
        assert state.detection_count >= 0
        assert state.consecutive_blocks >= 0
        assert state.detection_count >= state.consecutive_blocks or state.resolved
