# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for popup overlay detection (AX tree + regex + negative filters)."""

from __future__ import annotations

from pagemap.ecommerce.popup_detector import PopupOverlayResult, detect_popup_overlay


class TestAXTreeDialog:
    """Phase 1: AX tree dialog detection."""

    def test_dialog_with_promo_keyword(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="dialog", name="Newsletter signup — get 10% off"),
            make_interactable(ref=2, role="button", name="Close", affordance="click"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is not None
        assert result.provider == "dialog"
        assert result.confidence >= 0.80

    def test_dialog_without_promo(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="dialog", name="Important notice"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is not None
        assert result.confidence >= 0.60

    def test_alertdialog_detected(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="alertdialog", name="Subscribe to our newsletter!"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is not None
        assert result.provider == "dialog"

    def test_negative_filter_cookie(self, make_interactable):
        """Cookie dialog should NOT be detected as popup."""
        interactables = [
            make_interactable(ref=1, role="dialog", name="Cookie consent settings"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is None

    def test_negative_filter_login(self, make_interactable):
        """Login dialog should NOT be detected as popup."""
        interactables = [
            make_interactable(ref=1, role="dialog", name="Sign-in to continue"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is None

    def test_negative_filter_quick_view(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="dialog", name="Quick view product details"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is None

    def test_negative_filter_cart(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="dialog", name="Mini cart drawer"),
        ]
        result = detect_popup_overlay("", interactables)
        assert result is None


class TestHTMLRegex:
    """Phase 2: HTML regex fallback."""

    def test_newsletter_popup(self, make_interactable):
        html = '<div class="newsletter-popup"><p>Subscribe!</p></div>'
        result = detect_popup_overlay(html.lower(), [])
        assert result is not None
        assert result.provider == "newsletter"

    def test_app_banner(self, make_interactable):
        html = '<div id="app-banner-overlay"><p>Download our app</p></div>'
        result = detect_popup_overlay(html.lower(), [])
        assert result is not None
        assert result.provider == "app-banner"

    def test_promo_popup(self, make_interactable):
        html = '<div class="promo-popup"><p>Special offer!</p></div>'
        result = detect_popup_overlay(html.lower(), [])
        assert result is not None
        assert result.provider == "promo"

    def test_exit_intent(self, make_interactable):
        html = '<div class="exit-intent"><p>Wait! Don\'t go!</p></div>'
        result = detect_popup_overlay(html.lower(), [])
        assert result is not None
        assert result.provider == "exit-intent"

    def test_subscribe_modal(self, make_interactable):
        html = '<div class="subscribe-popup"><p>Join our list</p></div>'
        result = detect_popup_overlay(html.lower(), [])
        assert result is not None
        assert result.provider == "newsletter"

    def test_no_popup(self, make_interactable):
        html = "<div class='product-list'><p>Products here</p></div>"
        result = detect_popup_overlay(html.lower(), [])
        assert result is None

    def test_cookie_class_not_popup(self, make_interactable):
        """Cookie-related classes should NOT match popup regex."""
        html = '<div class="cookie-popup"><p>We use cookies</p></div>'
        # The negative filter in _POPUP_HTML_RE should not match cookie classes
        # because cookie classes don't match _POPUP_HTML_RE patterns
        result = detect_popup_overlay(html.lower(), [])
        # cookie-popup doesn't match our popup patterns (newsletter, app, promo, etc.)
        assert result is None


class TestPriority:
    """AX tree takes priority over HTML regex."""

    def test_ax_tree_priority(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="dialog", name="Newsletter promo"),
        ]
        html = '<div class="newsletter-popup">Subscribe</div>'
        result = detect_popup_overlay(html.lower(), interactables)
        assert result is not None
        assert result.provider == "dialog"  # AX tree, not HTML regex


class TestNeverRaises:
    """detect_popup_overlay() must never raise."""

    def test_empty_input(self):
        result = detect_popup_overlay("", [])
        assert result is None

    def test_binary_input(self):
        html = "\x00\x01\x02\xff" * 50
        result = detect_popup_overlay(html, [])
        assert result is None or isinstance(result, PopupOverlayResult)
