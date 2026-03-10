# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 antibot provider detection."""

from __future__ import annotations

from pagemap.diagnostics import AntibotProvider
from pagemap.diagnostics.antibot_detector import detect_antibot


class TestProviderDetection:
    def test_turnstile(self):
        html = '<html><body><div class="cf-turnstile"></div></body></html>'
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.provider == AntibotProvider.TURNSTILE
        assert result.confidence >= 0.90

    def test_recaptcha(self):
        html = '<html><body><div class="g-recaptcha" data-sitekey="abc"></div></body></html>'
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.provider == AntibotProvider.RECAPTCHA

    def test_hcaptcha(self):
        html = '<html><body><div class="h-captcha" data-hcaptcha="true"></div></body></html>'
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.provider == AntibotProvider.HCAPTCHA

    def test_cloudflare(self):
        html = "<html><body><h1>Just a moment...</h1><p>cf-browser-verification</p></body></html>"
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.provider == AntibotProvider.CLOUDFLARE

    def test_akamai(self):
        html = "<html><body><script>akamai bot manager _abck</script></body></html>"
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.provider == AntibotProvider.AKAMAI

    def test_generic_captcha(self):
        html = '<html><body><div class="captcha-container">Please solve</div></body></html>'
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.provider == AntibotProvider.GENERIC
        assert result.confidence <= 0.75


class TestChallengeVisibility:
    def test_visible_challenge(self):
        html = '<html><body><div class="cf-turnstile">Verify</div></body></html>'
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        assert result.challenge_visible is True

    def test_non_visible_challenge(self):
        html = '<html><body><div class="cf-turnstile"></div><p>' + "x " * 200 + "</p></body></html>"
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is not None
        # Body text is long enough, so challenge should not be classified as visible
        assert result.challenge_visible is False


class TestNoDetection:
    def test_normal_page(self):
        html = "<html><body><h1>Normal Page</h1><p>Welcome to our store</p></body></html>"
        result = detect_antibot(raw_html=html, html_lower=html.lower())
        assert result is None

    def test_empty_html(self):
        result = detect_antibot(raw_html="", html_lower="")
        assert result is None


class TestNeverRaises:
    def test_malformed_html(self):
        result = detect_antibot(raw_html="<<<>>>", html_lower="<<<>>>")
        assert result is None
