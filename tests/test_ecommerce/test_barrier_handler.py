# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for barrier detection (Layer 0).

Covers 6 CMP providers, login walls, age verification,
false positives, and hypothesis fuzz testing.
"""

from __future__ import annotations

import pytest

from pagemap.ecommerce import BarrierResult, BarrierType
from pagemap.ecommerce.barrier_handler import detect_barriers
from pagemap.ecommerce.cookie_patterns import detect_cookie_provider
from pagemap.ecommerce.login_detector import (
    detect_age_gate,
    detect_login_wall,
    detect_region_block,
)

from .conftest import COOKIEBOT_HTML, LOGIN_FORM_HTML, ONETRUST_HTML

# ── Cookie Consent Detection ───────────────────────────────────────


class TestCookiePatterns:
    """6 CMP providers + generic detection."""

    def test_cookiebot(self):
        html = '<div id="CybotCookiebotDialog">cookie banner</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "cookiebot"
        assert result.confidence >= 0.9

    def test_onetrust(self):
        html = '<div id="onetrust-banner-sdk">cookie notice</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "onetrust"
        assert result.confidence >= 0.9

    def test_trustarc(self):
        html = '<div class="truste-consent">privacy notice</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "trustarc"
        assert result.confidence >= 0.85

    def test_didomi(self):
        html = '<div id="didomi-popup">consent popup</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "didomi"
        assert result.confidence >= 0.85

    def test_quantcast(self):
        html = '<div class="qc-cmp-ui">GDPR consent</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "quantcast"
        assert result.confidence >= 0.85

    def test_generic_cookie_banner(self):
        html = '<div class="cookie-banner"><button>Accept</button></div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "generic"
        assert result.confidence >= 0.5

    def test_generic_gdpr_banner(self):
        html = '<div class="gdpr-consent-notice">Privacy</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "generic"

    def test_no_cookie_banner(self):
        html = "<div>Normal product page with no cookies stuff</div>"
        result = detect_cookie_provider(html.lower())
        assert result is None

    def test_named_cmp_takes_priority_over_generic(self):
        html = '<div id="CybotCookiebotDialog" class="cookie-banner">banner</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "cookiebot"  # Not generic


# ── Login Wall Detection ──────────────────────────────────────────


class TestLoginDetector:
    def test_login_form_detected(self, make_interactable):
        html = LOGIN_FORM_HTML
        interactables = [
            make_interactable(ref=1, role="button", name="로그인"),
        ]
        result = detect_login_wall(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert result.has_password is True
        assert result.confidence >= 0.5
        assert len(result.form_fields) > 0

    def test_login_oauth_detected(self, make_interactable):
        html = LOGIN_FORM_HTML
        interactables = []
        result = detect_login_wall(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert "google" in result.oauth_providers
        assert "kakao" in result.oauth_providers

    def test_no_login_wall(self, make_interactable):
        html = "<div>Normal page with <form><input type='text' name='search'></form></div>"
        result = detect_login_wall(html, html.lower(), "https://example.com", [], "unknown")
        assert result is None

    def test_age_gate_detected(self):
        html = '<div class="age-verification">Are you over 18?</div>'
        confidence, signals = detect_age_gate(html.lower())
        assert confidence >= 0.7
        assert len(signals) > 0

    def test_age_gate_korean(self):
        html = '<div class="modal">나이 확인이 필요합니다</div>'
        confidence, _ = detect_age_gate(html.lower())
        assert confidence >= 0.7

    def test_region_block_detected(self):
        html = "<p>This content is not available in your region.</p>"
        confidence, signals = detect_region_block(html.lower())
        assert confidence >= 0.7
        assert len(signals) > 0

    def test_no_region_block(self):
        html = "<p>Welcome to our store!</p>"
        confidence, _ = detect_region_block(html.lower())
        assert confidence == 0.0


# ── Barrier Handler (Orchestrator) ─────────────────────────────────


class TestBarrierHandler:
    def test_cookie_barrier_full_flow(self, make_interactable):
        """Cookie banner → BarrierResult with accept_ref."""
        html = COOKIEBOT_HTML
        interactables = [
            make_interactable(ref=1, role="button", name="Accept all cookies", affordance="click"),
        ]
        result = detect_barriers(html, html.lower(), "https://example.com", interactables, "product_detail")
        assert result is not None
        assert result.barrier_type == BarrierType.COOKIE_CONSENT
        assert result.provider == "cookiebot"
        assert result.auto_dismissible is True
        assert result.accept_ref == 1

    def test_onetrust_barrier(self, make_interactable):
        html = ONETRUST_HTML
        interactables = [
            make_interactable(ref=1, role="button", name="Accept All", affordance="click"),
        ]
        result = detect_barriers(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert result.barrier_type == BarrierType.COOKIE_CONSENT
        assert result.provider == "onetrust"
        assert result.accept_ref == 1

    def test_login_barrier(self, make_interactable):
        html = LOGIN_FORM_HTML
        interactables = [
            make_interactable(ref=1, role="button", name="로그인"),
        ]
        result = detect_barriers(html, html.lower(), "https://example.com", interactables, "login")
        assert result is not None
        assert result.barrier_type == BarrierType.LOGIN_REQUIRED
        assert result.auto_dismissible is False
        assert len(result.form_fields) > 0

    def test_age_verification_barrier(self, make_interactable):
        html = '<div class="age-verification">Are you over 18? <input type="date" name="birthdate"></div>'
        result = detect_barriers(html, html.lower(), "https://example.com", [], "product_detail")
        assert result is not None
        assert result.barrier_type == BarrierType.AGE_VERIFICATION

    def test_region_restricted_barrier(self, make_interactable):
        html = "<div><h1>Not available in your region</h1><p>Region restricted content.</p></div>"
        result = detect_barriers(html, html.lower(), "https://example.com", [], "unknown")
        assert result is not None
        assert result.barrier_type == BarrierType.REGION_RESTRICTED

    def test_no_barrier(self, make_interactable):
        html = "<html><body><h1>Welcome to our store!</h1><p>Browse products.</p></body></html>"
        result = detect_barriers(html, html.lower(), "https://example.com", [], "product_detail")
        assert result is None

    def test_cookie_priority_over_login(self, make_interactable):
        """Cookie banner takes priority over login form (more common)."""
        html = COOKIEBOT_HTML + LOGIN_FORM_HTML
        interactables = [
            make_interactable(ref=1, role="button", name="Accept all cookies", affordance="click"),
            make_interactable(ref=2, role="button", name="로그인", affordance="click"),
        ]
        result = detect_barriers(html, html.lower(), "https://example.com", interactables, "login")
        assert result is not None
        assert result.barrier_type == BarrierType.COOKIE_CONSENT

    def test_barrier_never_raises_on_bad_html(self):
        """detect_barriers() must never raise."""
        result = detect_barriers("", "", "invalid-url", [], "unknown")
        assert result is None

    def test_barrier_never_raises_on_binary(self):
        html = "\x00\x01\x02\xff" * 100
        result = detect_barriers(html, html.lower(), "https://example.com", [], "unknown")
        assert result is None or isinstance(result, BarrierResult)


# ── BarrierResult methods ──────────────────────────────────────────


class TestBarrierResult:
    def test_to_dict(self):
        result = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="cookiebot",
            auto_dismissible=True,
            accept_ref=1,
            confidence=0.95,
            signals=("cmp:cookiebot",),
        )
        d = result.to_dict()
        assert d["barrier_type"] == "cookie_consent"
        assert d["provider"] == "cookiebot"
        assert d["accept_ref"] == 1

    def test_warning_message_cookie(self):
        result = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="onetrust",
            auto_dismissible=True,
            accept_ref=5,
            confidence=0.9,
        )
        msg = result.warning_message()
        assert "onetrust" in msg
        assert "[5]" in msg

    def test_warning_message_login(self):
        result = BarrierResult(
            barrier_type=BarrierType.LOGIN_REQUIRED,
            provider="generic",
            auto_dismissible=False,
            accept_ref=None,
            confidence=0.8,
        )
        msg = result.warning_message()
        assert "Login required" in msg

    def test_with_matched_ref(self, make_interactable):
        """with_matched_ref re-matches accept button in final interactables."""
        result = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="cookiebot",
            auto_dismissible=True,
            accept_ref=99,  # stale ref
            confidence=0.95,
            accept_terms=("accept all", "accept cookies"),
        )
        final_interactables = [
            make_interactable(ref=1, role="link", name="Home"),
            make_interactable(ref=2, role="button", name="Accept All Cookies", affordance="click"),
            make_interactable(ref=3, role="button", name="Settings"),
        ]
        updated = result.with_matched_ref(final_interactables)
        assert updated.accept_ref == 2

    def test_with_matched_ref_no_match(self, make_interactable):
        result = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="generic",
            auto_dismissible=True,
            accept_ref=99,
            confidence=0.7,
            accept_terms=("accept all",),
        )
        final = [make_interactable(ref=1, role="button", name="Submit")]
        updated = result.with_matched_ref(final)
        assert updated.accept_ref is None

    def test_with_matched_ref_empty_terms(self, make_interactable):
        result = BarrierResult(
            barrier_type=BarrierType.LOGIN_REQUIRED,
            provider="generic",
            auto_dismissible=False,
            accept_ref=None,
            confidence=0.8,
        )
        updated = result.with_matched_ref([make_interactable()])
        assert updated.accept_ref is None


# ── False positive checks ──────────────────────────────────────────


class TestFalsePositives:
    def test_cookie_in_recipe_not_detected(self):
        """A recipe page mentioning cookies (food) should not trigger cookie consent."""
        html = """
        <html><body>
        <h1>Best Chocolate Chip Cookie Recipe</h1>
        <p>These cookies are amazing! Mix flour, sugar, and chocolate chips.</p>
        <p>Bake the cookies at 350°F for 12 minutes.</p>
        </body></html>
        """
        result = detect_cookie_provider(html.lower())
        assert result is None

    def test_login_link_not_login_wall(self, make_interactable):
        """A simple login link in header is not a login wall."""
        html = '<header><a href="/login">Log in</a></header><main><p>Products here</p></main>'
        result = detect_login_wall(html, html.lower(), "https://example.com", [], "product_detail")
        assert result is None


# ── Hypothesis Fuzz Testing ────────────────────────────────────────

try:
    from hypothesis import given, settings, strategies as st

    _HAS_HYPOTHESIS = True
except ImportError:
    _HAS_HYPOTHESIS = False


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestBarrierFuzz:
    @given(html=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, deadline=2000)
    def test_barrier_never_crashes(self, html):
        """detect_barriers() must never raise regardless of input."""
        result = detect_barriers(html, html.lower(), "https://example.com", [], "unknown")
        assert result is None or isinstance(result, BarrierResult)

    @given(html=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, deadline=2000)
    def test_cookie_provider_never_crashes(self, html):
        from pagemap.ecommerce.cookie_patterns import CookieConsentPattern

        result = detect_cookie_provider(html.lower())
        assert result is None or isinstance(result, CookieConsentPattern)

    @given(html=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, deadline=2000)
    def test_login_detector_never_crashes(self, html):
        from pagemap.ecommerce.login_detector import LoginFormInfo

        result = detect_login_wall(html, html.lower(), "https://example.com", [], "unknown")
        assert result is None or isinstance(result, LoginFormInfo)
