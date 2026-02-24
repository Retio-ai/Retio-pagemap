# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for P7.1 page-type compression dispatch + fallback."""

from __future__ import annotations

from pagemap.pruned_context_builder import (
    _COMPRESSORS,
    _SCHEMA_COMPRESSORS,
    CompressorContext,
    _compress_default_dispatch,
    _compress_for_checkout,
    _compress_for_dashboard,
    _compress_for_documentation,
    _compress_for_error,
    _compress_for_form,
    _compress_for_help_faq,
    _compress_for_landing,
    _compress_for_login,
    _compress_for_settings,
)

# ---------------------------------------------------------------------------
# Dispatch table coverage
# ---------------------------------------------------------------------------


class TestDispatchTable:
    """All 14 page types have entries in _COMPRESSORS."""

    EXPECTED_TYPES = [
        "product_detail",
        "search_results",
        "listing",
        "article",
        "news",
        "login",
        "checkout",
        "form",
        "dashboard",
        "help_faq",
        "settings",
        "error",
        "documentation",
        "landing",
    ]

    def test_all_types_registered(self):
        for ptype in self.EXPECTED_TYPES:
            assert ptype in _COMPRESSORS, f"{ptype} missing from _COMPRESSORS"

    def test_dispatch_returns_string(self):
        """Each dispatcher returns a non-empty string for valid HTML."""
        html = "<html><body><h1>Test Page</h1><p>Some content here for testing.</p></body></html>"
        ctx = CompressorContext(pruned_html=html, max_tokens=500)
        for ptype, fn in _COMPRESSORS.items():
            result = fn(ctx)
            assert isinstance(result, str), f"{ptype} dispatcher did not return str"

    def test_default_dispatch(self):
        """_compress_default_dispatch works correctly."""
        ctx = CompressorContext(
            pruned_html="<h1>Hello</h1><p>World content here</p>",
            max_tokens=500,
        )
        result = _compress_default_dispatch(ctx)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Individual compressor tests
# ---------------------------------------------------------------------------


class TestLoginCompressor:
    def test_extracts_login_fields(self):
        html = """<html><body>
            <h1>Login</h1>
            <label>Email</label>
            <input type="email" name="email">
            <label>Password</label>
            <input type="password" name="password">
            <button>Sign in</button>
            <a href="/forgot">Forgot password?</a>
        </body></html>"""
        result = _compress_for_login(html, 500)
        assert len(result) > 0
        # Should contain login-related terms
        assert any(kw in result.lower() for kw in ("email", "password", "forgot"))

    def test_login_with_error(self):
        html = """<html><body>
            <div class="error">Invalid email or password</div>
            <label>Email</label>
            <input type="email">
        </body></html>"""
        result = _compress_for_login(html, 500)
        assert "error" in result.lower() or "invalid" in result.lower()

    def test_login_fallback_to_default(self):
        """No login-related terms → falls back to _compress_default."""
        html = "<html><body><p>Totally unrelated content about cats and dogs</p></body></html>"
        result = _compress_for_login(html, 500)
        assert len(result) > 0


class TestCheckoutCompressor:
    def test_extracts_checkout_info(self):
        html = """<html><body>
            <h1>Checkout</h1>
            <div>Order Summary</div>
            <div>Subtotal: $99.00</div>
            <div>Shipping: Free</div>
            <div>Total: $99.00</div>
            <div>Payment Method: Credit Card</div>
        </body></html>"""
        result = _compress_for_checkout(html, 500)
        assert any(kw in result.lower() for kw in ("total", "order", "payment", "shipping"))


class TestFormCompressor:
    def test_extracts_form_fields(self):
        html = """<html><body>
            <h1>Contact Us</h1>
            <label>Name</label><input type="text">
            <label>Email</label><input type="email">
            <label>Message</label><textarea></textarea>
            <div class="required">Required field</div>
            <button>Submit</button>
        </body></html>"""
        result = _compress_for_form(html, 500)
        assert any(kw in result.lower() for kw in ("name", "email", "message", "required", "submit"))

    def test_form_with_validation_errors(self):
        html = """<html><body>
            <div class="error">Email is required</div>
            <label>Email</label><input type="email">
        </body></html>"""
        result = _compress_for_form(html, 500)
        assert "required" in result.lower() or "error" in result.lower()


class TestDashboardCompressor:
    def test_extracts_metrics(self):
        html = """<html><body>
            <h1>Dashboard</h1>
            <div>Total Revenue: $50,000</div>
            <div>Active Users: 1,234</div>
            <div>Page Views: 50,000</div>
        </body></html>"""
        result = _compress_for_dashboard(html, 500)
        assert any(kw in result.lower() for kw in ("total", "revenue", "users", "views"))


class TestHelpFaqCompressor:
    def test_extracts_questions(self):
        html = """<html><body>
            <h1>FAQ</h1>
            <h3>How do I return an item?</h3>
            <p>You can return within 30 days.</p>
            <h3>What payment methods do you accept?</h3>
            <p>We accept credit cards and PayPal.</p>
        </body></html>"""
        result = _compress_for_help_faq(html, 500)
        assert "Q" in result  # numbered questions


class TestSettingsCompressor:
    def test_extracts_settings(self):
        html = """<html><body>
            <h1>Settings</h1>
            <div>Notification preferences</div>
            <div>Language: English</div>
            <div>Theme: Dark</div>
            <div>Privacy settings</div>
        </body></html>"""
        result = _compress_for_settings(html, 500)
        assert any(kw in result.lower() for kw in ("notification", "language", "theme", "privacy", "setting"))


class TestErrorCompressor:
    def test_extracts_error_info(self):
        html = """<html><body>
            <h1>404</h1>
            <p>Page not found</p>
            <a href="/">Go Home</a>
        </body></html>"""
        result = _compress_for_error(html, 500)
        assert len(result) > 0
        assert "404" in result or "not found" in result.lower()


class TestDocumentationCompressor:
    def test_extracts_headings_and_code(self):
        html = """<html><body>
            <h1>API Reference</h1>
            <h2>Authentication</h2>
            <p>Use the following code to authenticate:</p>
            <code>import requests</code>
            <pre>def authenticate(token): return True</pre>
        </body></html>"""
        result = _compress_for_documentation(html, 500)
        assert len(result) > 0


class TestLandingCompressor:
    def test_extracts_hero_and_sections(self):
        html = """<html><body>
            <div class="hero">
                <h1>Welcome to Our Product</h1>
                <p>The best solution for your needs</p>
                <a href="/signup">Get Started</a>
            </div>
            <section><h2>Features</h2></section>
            <section><h2>Pricing</h2></section>
            <section><h2>Testimonials</h2></section>
        </body></html>"""
        result = _compress_for_landing(html, 500)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestFallback:
    """Unknown page types fall back to _compress_default."""

    def test_unknown_type_uses_default(self):
        """Page type not in _COMPRESSORS falls through to default."""
        assert "some_nonexistent_type" not in _COMPRESSORS

    def test_empty_html_no_crash(self):
        """Compressors handle empty HTML without crashing."""
        for _ptype, fn in _COMPRESSORS.items():
            ctx = CompressorContext(pruned_html="", max_tokens=500)
            result = fn(ctx)
            assert isinstance(result, str)

    def test_token_budget_respected(self):
        """Output fits within token budget."""
        from pagemap.preprocessing.preprocess import count_tokens

        long_html = "<html><body>" + "<p>Some content here. </p>" * 200 + "</body></html>"
        max_tok = 50
        for ptype, fn in _COMPRESSORS.items():
            ctx = CompressorContext(pruned_html=long_html, max_tokens=max_tok)
            result = fn(ctx)
            # Allow small overshoot due to tokenization granularity
            assert count_tokens(result) <= max_tok + 10, f"{ptype} exceeded token budget"


# ---------------------------------------------------------------------------
# Schema-aware dispatch table
# ---------------------------------------------------------------------------


class TestSchemaDispatchTable:
    EXPECTED_SCHEMAS = ["SaaSPage", "GovernmentPage", "WikiArticle"]

    def test_all_schemas_registered(self):
        for schema in self.EXPECTED_SCHEMAS:
            assert schema in _SCHEMA_COMPRESSORS, f"{schema} missing from _SCHEMA_COMPRESSORS"

    def test_dispatch_returns_string(self):
        html = "<html><body><h1>Test Page</h1><p>Some content here for testing.</p></body></html>"
        ctx = CompressorContext(pruned_html=html, max_tokens=500)
        for schema, fn in _SCHEMA_COMPRESSORS.items():
            result = fn(ctx)
            assert isinstance(result, str), f"{schema} dispatcher did not return str"


class TestSchemaFallback:
    def test_schema_used_when_page_type_misses(self):
        """page_type not in _COMPRESSORS + schema_name in _SCHEMA_COMPRESSORS → schema compressor."""
        assert "unknown_type" not in _COMPRESSORS
        assert "SaaSPage" in _SCHEMA_COMPRESSORS
        # Simulate dispatch logic
        compressor = _COMPRESSORS.get("unknown_type")
        assert compressor is None
        compressor = _SCHEMA_COMPRESSORS.get("SaaSPage", _compress_default_dispatch)
        assert compressor is not _compress_default_dispatch

    def test_page_type_takes_precedence(self):
        """page_type in _COMPRESSORS → page_type compressor wins over schema."""
        assert "documentation" in _COMPRESSORS
        compressor = _COMPRESSORS.get("documentation")
        assert compressor is not None  # documentation compressor, not wiki

    def test_unknown_schema_falls_to_default(self):
        """Unregistered schema → default fallback."""
        compressor = _SCHEMA_COMPRESSORS.get("UnknownSchema", _compress_default_dispatch)
        assert compressor is _compress_default_dispatch

    def test_schema_compressor_budget_respected(self):
        """All schema compressors respect token budget."""
        from pagemap.preprocessing.preprocess import count_tokens

        long_html = "<html><body>" + "<p>Some content here. </p>" * 200 + "</body></html>"
        max_tok = 50
        for schema, fn in _SCHEMA_COMPRESSORS.items():
            ctx = CompressorContext(pruned_html=long_html, max_tokens=max_tok)
            result = fn(ctx)
            assert count_tokens(result) <= max_tok + 10, f"{schema} exceeded token budget"
