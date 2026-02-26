# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the RFC 9457 problem_details module.

Covers:
- Backward compatibility (to_mcp_text matches old _safe_error output)
- Core ProblemDetail dataclass behaviour
- Starlette response generation
- Secret/path sanitization (including hypothesis property-based)
- Factory functions
- Taxonomy completeness
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pagemap.errors import (
    ApiKeyError,
    BrowserError,
    PageMapBuildError,
    PageMapError,
    RateLimitError,
    ResourceExhaustionError,
    SanitizationError,
    SSRFError,
)
from pagemap.problem_details import (
    _ERROR_BASE,
    _RECOVERY_HINTS,
    _TYPE_METADATA,
    MAX_DETAIL_LENGTH,
    ProblemDetail,
    ProblemType,
    from_auth_invalid,
    from_auth_missing,
    from_browser_dead,
    from_exception,
    from_insufficient_credits,
    from_rate_limit,
    from_robots,
    from_server_busy,
    from_ssrf,
    from_validation,
    sanitize_detail,
)
from pagemap.rate_limiter import RateLimitResult

# ── 3a. Backward Compatibility Snapshot Tests (P0) ───────────────────


class TestBackwardCompatibility:
    """to_mcp_text() MUST produce identical output to the old _safe_error()."""

    @pytest.mark.parametrize("context,hint", list(_RECOVERY_HINTS.items()))
    def test_all_hint_contexts(self, context: str, hint: str):
        exc = Exception("something went wrong")
        problem = from_exception(exc, tool_context=context)
        result = problem.to_mcp_text()
        assert hint in result
        assert f"Error ({context}):" in result

    def test_batch_prefix_matching(self):
        """'batch [url]' context should match 'batch' hint via prefix."""
        exc = Exception("navigation failed")
        problem = from_exception(exc, tool_context="batch [https://example.com]")
        result = problem.to_mcp_text()
        assert _RECOVERY_HINTS["batch"] in result

    def test_truncation_at_200_chars(self):
        exc = Exception("x" * 300)
        problem = from_exception(exc, tool_context="get_page_map")
        result = problem.to_mcp_text()
        assert "..." in result
        assert _RECOVERY_HINTS["get_page_map"] in result

    def test_api_key_redaction(self):
        exc = Exception("key=sk-ant-abc123xyz789def456 leaked")
        problem = from_exception(exc, tool_context="get_page_map")
        result = problem.to_mcp_text()
        assert "sk-ant-abc123xyz789def456" not in result
        assert "<redacted>" in result

    def test_bearer_token_redaction(self):
        exc = Exception("Auth failed with Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        problem = from_exception(exc, tool_context="test")
        result = problem.to_mcp_text()
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "<redacted>" in result

    def test_filesystem_path_redaction(self):
        exc = Exception("Error in /Users/john/.ssh/id_rsa")
        problem = from_exception(exc, tool_context="take_screenshot")
        result = problem.to_mcp_text()
        assert "/Users/john/.ssh/id_rsa" not in result
        assert "<path>" in result

    def test_url_paths_preserved(self):
        exc = Exception("Page timed out: https://www.musinsa.com/products/4960603")
        problem = from_exception(exc, tool_context="test")
        result = problem.to_mcp_text()
        assert "/products/4960603" in result

    def test_no_hint_context(self):
        exc = Exception("something went wrong")
        problem = from_exception(exc, tool_context="unknown_tool")
        result = problem.to_mcp_text()
        assert result == "Error (unknown_tool): something went wrong"
        for hint in _RECOVERY_HINTS.values():
            assert hint not in result

    def test_output_format_with_hint(self):
        """Exact format: 'Error (<ctx>): <msg>. <hint>'"""
        exc = Exception("oops")
        problem = from_exception(exc, tool_context="get_page_map")
        result = problem.to_mcp_text()
        expected = f"Error (get_page_map): oops. {_RECOVERY_HINTS['get_page_map']}"
        assert result == expected

    def test_output_format_without_hint(self):
        """Exact format: 'Error (<ctx>): <msg>'"""
        exc = Exception("oops")
        problem = from_exception(exc, tool_context="unknown")
        result = problem.to_mcp_text()
        assert result == "Error (unknown): oops"


# ── 3b. Core ProblemDetail Tests ─────────────────────────────────────


class TestProblemDetail:
    def test_default_values(self):
        p = ProblemDetail()
        assert p.type == "about:blank"
        assert p.title == ""
        assert p.status == 500
        assert p.detail == ""
        assert p.instance == ""
        assert p.extensions == {}

    def test_frozen_immutability(self):
        p = ProblemDetail()
        with pytest.raises(AttributeError):
            p.status = 404  # type: ignore[misc]

    def test_to_dict_omits_empty_fields(self):
        p = ProblemDetail(type="about:blank", status=500)
        d = p.to_dict()
        assert "title" not in d
        assert "detail" not in d
        assert "instance" not in d

    def test_to_dict_extensions_at_top_level(self):
        p = ProblemDetail(extensions={"retry_after": 30, "scope": "client"})
        d = p.to_dict()
        assert d["retry_after"] == 30
        assert d["scope"] == "client"

    def test_to_dict_extensions_never_shadow_standard(self):
        p = ProblemDetail(
            type="about:blank",
            title="Original",
            status=500,
            extensions={"type": "evil", "status": 999, "title": "Hacked"},
        )
        d = p.to_dict()
        assert d["type"] == "about:blank"
        assert d["status"] == 500
        assert d["title"] == "Original"

    def test_to_json_valid_json(self):
        p = ProblemDetail(type="about:blank", status=500, detail="test error")
        parsed = json.loads(p.to_json())
        assert parsed["status"] == 500
        assert parsed["detail"] == "test error"

    def test_to_json_non_ascii_preserved(self):
        p = ProblemDetail(detail="에러 발생")
        j = p.to_json()
        assert "에러 발생" in j
        parsed = json.loads(j)
        assert parsed["detail"] == "에러 발생"


class TestToResponse:
    def test_status_code_matches(self):
        p = ProblemDetail(status=422)
        resp = p.to_response()
        assert resp.status_code == 422

    def test_content_type_problem_json(self):
        p = ProblemDetail()
        resp = p.to_response()
        assert resp.media_type == "application/problem+json"

    def test_retry_after_header(self):
        p = ProblemDetail(extensions={"retry_after": 2.5})
        resp = p.to_response()
        assert resp.headers.get("retry-after") == "3"

    def test_rate_limit_headers(self):
        p = ProblemDetail(extensions={"limit": 30, "remaining": 5})
        resp = p.to_response()
        assert resp.headers.get("ratelimit-limit") == "30"
        assert resp.headers.get("ratelimit-remaining") == "5"

    def test_cache_control_no_store(self):
        p = ProblemDetail()
        resp = p.to_response()
        assert resp.headers.get("cache-control") == "no-store"

    def test_content_language_en(self):
        p = ProblemDetail()
        resp = p.to_response()
        assert resp.headers.get("content-language") == "en"

    def test_no_extra_headers_when_no_extensions(self):
        p = ProblemDetail()
        resp = p.to_response()
        assert "retry-after" not in resp.headers
        assert "ratelimit-limit" not in resp.headers
        assert "ratelimit-remaining" not in resp.headers


# ── 3c. Sanitization Tests ───────────────────────────────────────────


class TestSanitizeDetail:
    # Existing 5 patterns
    def test_redacts_sk_api_key(self):
        result = sanitize_detail("key is sk-ant-abc123xyz789def456ghi")
        assert "sk-ant-abc123xyz789def456ghi" not in result
        assert "<redacted>" in result

    def test_redacts_bearer_token(self):
        result = sanitize_detail("Auth: Bearer eyJtoken123abc.payload.sig")
        assert "eyJtoken123abc" not in result
        assert "Bearer <redacted>" in result

    def test_redacts_env_var_patterns(self):
        result = sanitize_detail("ANTHROPIC_API_KEY=sk-ant-secret123value")
        assert "secret123value" not in result
        assert "<redacted>" in result

    def test_strips_filesystem_paths(self):
        result = sanitize_detail("Error in /Users/john/.ssh/id_rsa")
        assert "/Users/john/.ssh/id_rsa" not in result
        assert "<path>" in result

    def test_preserves_url_paths(self):
        result = sanitize_detail("Failed: https://example.com/api/v1/search?q=test")
        assert "/api/v1/search" in result

    def test_truncates_at_max_length(self):
        result = sanitize_detail("x" * 300)
        assert len(result) == MAX_DETAIL_LENGTH + 3  # + "..."
        assert result.endswith("...")

    # New 5 patterns
    def test_redacts_basic_auth(self):
        result = sanitize_detail("Header: Basic dXNlcjpwYXNzd29yZA==")
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert "Basic <redacted>" in result

    def test_redacts_connection_string_creds(self):
        result = sanitize_detail("mongodb://admin:password@host:27017/db")
        assert "admin:password" not in result
        assert "://<redacted>@" in result

    def test_redacts_jwt_tokens(self):
        # Construct a realistic JWT-like string
        jwt = "eyJhbGciOiJIUzI1Ng.eyJzdWIiOiIxMjM0NTY3ODkw.SflKxwRJSMeKKF2QT4fwpM"
        result = sanitize_detail(f"Token: {jwt}")
        assert jwt not in result
        assert "<redacted>" in result

    def test_redacts_aws_access_keys(self):
        result = sanitize_detail("AWS key: AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "<redacted>" in result

    def test_redacts_github_tokens(self):
        token = "ghp_" + "a" * 36
        result = sanitize_detail(f"GitHub: {token}")
        assert token not in result
        assert "<redacted>" in result

    # Extension sanitization
    def test_sanitizes_extension_string_values(self):
        from pagemap.problem_details import _sanitize_extensions

        ext = {"url": "https://safe.com", "secret": "Bearer mysecrettoken123"}
        result = _sanitize_extensions(ext)
        assert "mysecrettoken123" not in result["secret"]
        assert result["url"] == "https://safe.com"

    # Edge cases
    def test_empty_string(self):
        assert sanitize_detail("") == ""

    def test_no_redaction_needed(self):
        result = sanitize_detail("Simple error message")
        assert result == "Simple error message"

    def test_strips_windows_path(self):
        result = sanitize_detail(r"File C:\Users\admin\secrets\key.pem not found")
        assert "admin" not in result
        assert "<path>" in result

    def test_strips_tmp_path(self):
        result = sanitize_detail("Error reading /tmp/pagemap_cache/session.json")
        assert "/tmp/pagemap_cache" not in result
        assert "<path>" in result

    def test_strips_var_path(self):
        result = sanitize_detail("Log at /var/log/pagemap/error.log")
        assert "/var/log" not in result
        assert "<path>" in result

    def test_preserves_wiki_path(self):
        result = sanitize_detail("Error at https://en.wikipedia.org/wiki/Python")
        assert "/wiki/Python" in result


class TestSanitizeProperty:
    """Hypothesis property-based tests."""

    @given(st.text(max_size=1000))
    def test_output_never_exceeds_max_length(self, text: str):
        result = sanitize_detail(text)
        assert len(result) <= MAX_DETAIL_LENGTH + 3  # +3 for "..."

    @given(st.text(max_size=500))
    @settings(deadline=timedelta(milliseconds=500))
    def test_no_catastrophic_backtracking(self, text: str):
        """sanitize_detail must complete within deadline for any input."""
        sanitize_detail(text)

    @given(st.text(max_size=200))
    def test_known_secrets_always_redacted(self, text: str):
        # Inject a known secret pattern
        injected = f"sk-testkey12345678 {text}"
        result = sanitize_detail(injected)
        assert "sk-testkey12345678" not in result


# ── 3d. Factory Function Tests ───────────────────────────────────────


class TestFromException:
    def test_ssrf_error_mapping(self):
        exc = SSRFError("Blocked: internal IP")
        p = from_exception(exc)
        assert p.type == ProblemType.SSRF_BLOCKED.uri
        assert p.status == 403

    def test_browser_error_mapping(self):
        exc = BrowserError("chromium crashed")
        p = from_exception(exc)
        assert p.type == ProblemType.BROWSER_UNAVAILABLE.uri
        assert p.status == 503

    def test_rate_limit_error_with_extensions(self):
        exc = RateLimitError("rate limited", retry_after=5.0, limit=30, remaining=0)
        p = from_exception(exc)
        assert p.type == ProblemType.RATE_LIMIT_EXCEEDED.uri
        assert p.status == 429
        assert p.extensions["retry_after"] == 5.0
        assert p.extensions["limit"] == 30
        assert p.extensions["remaining"] == 0

    def test_api_key_error_with_client_id(self):
        exc = ApiKeyError("invalid key", client_id="client-123")
        p = from_exception(exc)
        assert p.type == ProblemType.AUTH_INVALID.uri
        assert p.status == 403
        assert p.extensions["client_id"] == "client-123"

    def test_resource_exhaustion_422(self):
        exc = ResourceExhaustionError("DOM node limit exceeded")
        p = from_exception(exc)
        assert p.type == ProblemType.RESOURCE_EXHAUSTED.uri
        assert p.status == 422  # Not 413

    def test_timeout_page_context(self):
        exc = TimeoutError("Navigation timeout 30000ms")
        p = from_exception(exc, tool_context="get_page_map")
        assert p.type == ProblemType.PAGE_TIMEOUT.uri
        assert p.status == 504

    def test_timeout_action_context(self):
        exc = TimeoutError("Action timeout 5000ms")
        p = from_exception(exc, tool_context="execute_action")
        assert p.type == ProblemType.ACTION_TIMEOUT.uri
        assert p.status == 504

    def test_timeout_batch_context(self):
        exc = TimeoutError("Navigation timeout")
        p = from_exception(exc, tool_context="batch [https://example.com]")
        assert p.type == ProblemType.PAGE_TIMEOUT.uri

    def test_unknown_exception_about_blank(self):
        exc = ValueError("unexpected value")
        p = from_exception(exc)
        assert p.type == "about:blank"
        assert p.status == 500

    def test_non_pagemap_error_sanitized_detail(self):
        exc = RuntimeError("secret at /Users/admin/.ssh/key")
        p = from_exception(exc)
        assert "/Users/admin/.ssh/key" not in p.detail
        assert "<path>" in p.detail

    def test_unicode_exception_messages(self):
        exc = Exception("페이지 로딩 타임아웃")
        p = from_exception(exc, tool_context="get_page_map")
        assert "페이지 로딩 타임아웃" in p.to_mcp_text()

    def test_sanitization_error_mapping(self):
        exc = SanitizationError("sanitization failed")
        p = from_exception(exc)
        assert p.type == ProblemType.ACTION_FAILED.uri
        assert p.status == 500

    def test_page_map_build_error_mapping(self):
        exc = PageMapBuildError("build failed")
        p = from_exception(exc)
        assert p.type == ProblemType.ACTION_FAILED.uri
        assert p.status == 500

    def test_generic_pagemap_error(self):
        """PageMapError subclass not in the explicit map → about:blank."""
        exc = PageMapError("generic failure")
        p = from_exception(exc)
        assert p.type == "about:blank"
        assert p.status == 500
        assert "generic failure" in p.detail

    def test_tool_context_preserved(self):
        exc = Exception("oops")
        p = from_exception(exc, tool_context="fill_form")
        assert p._tool_context == "fill_form"

    def test_instance_preserved(self):
        exc = Exception("oops")
        p = from_exception(exc, instance="/tools/get_page_map/123")
        assert p.instance == "/tools/get_page_map/123"

    def test_custom_extensions_merged(self):
        exc = SSRFError("blocked")
        p = from_exception(exc, extensions={"url": "http://evil.com"})
        assert p.extensions["url"] == "http://evil.com"


class TestFromRateLimit:
    def test_429_status(self):
        result = RateLimitResult(allowed=False, limit=30, remaining=0, reset=15.0, retry_after=5.0, scope="client")
        p = from_rate_limit(result, tool_name="get_page_map")
        assert p.status == 429
        assert p.type == ProblemType.RATE_LIMIT_EXCEEDED.uri

    def test_extensions_populated(self):
        result = RateLimitResult(allowed=False, limit=30, remaining=0, reset=15.0, retry_after=5.0, scope="client")
        p = from_rate_limit(result, client_id="client-1", tool_name="get_page_map")
        assert p.extensions["retry_after"] == 5.0
        assert p.extensions["limit"] == 30
        assert p.extensions["remaining"] == 0
        assert p.extensions["client_id"] == "client-1"

    def test_detail_includes_tool_name(self):
        result = RateLimitResult(allowed=False, limit=30, remaining=0, reset=15.0, retry_after=5.0, scope="client")
        p = from_rate_limit(result, tool_name="get_page_map")
        assert "get_page_map" in p.detail


class TestFromAuth:
    def test_missing_401(self):
        p = from_auth_missing()
        assert p.status == 401
        assert p.type == ProblemType.AUTH_REQUIRED.uri
        assert "API key required" in p.detail

    def test_invalid_403(self):
        p = from_auth_invalid(reason="expired", client_id="client-1")
        assert p.status == 403
        assert p.type == ProblemType.AUTH_INVALID.uri
        assert "expired" in p.detail
        assert p.extensions["client_id"] == "client-1"
        assert p.extensions["reason"] == "expired"


class TestFromValidation:
    def test_422_with_field(self):
        p = from_validation("URL is required", field_name="url", tool_context="get_page_map")
        assert p.status == 422
        assert p.type == ProblemType.VALIDATION_ERROR.uri
        assert p.extensions["field"] == "url"
        assert "URL is required" in p.detail


class TestFromSsrf:
    def test_403_with_url(self):
        p = from_ssrf("http://169.254.169.254", "Blocked: internal IP")
        assert p.status == 403
        assert p.type == ProblemType.SSRF_BLOCKED.uri
        assert p.extensions["url"] == "http://169.254.169.254"


class TestFromRobots:
    def test_403_with_origin(self):
        p = from_robots("https://example.com/admin", origin="https://example.com")
        assert p.status == 403
        assert p.type == ProblemType.ROBOTS_BLOCKED.uri
        assert p.extensions["url"] == "https://example.com/admin"
        assert p.extensions["origin"] == "https://example.com"


class TestFromBrowserDead:
    def test_503(self):
        p = from_browser_dead(tool_context="execute_action")
        assert p.status == 503
        assert p.type == ProblemType.BROWSER_UNAVAILABLE.uri
        assert "Browser connection lost" in p.detail


class TestFromServerBusy:
    def test_503(self):
        p = from_server_busy(tool_context="get_page_map")
        assert p.status == 503
        assert p.type == ProblemType.SERVER_BUSY.uri
        assert "Another tool call is in progress" in p.detail


class TestFromInsufficientCredits:
    def test_402_status(self):
        p = from_insufficient_credits(balance=5, required=10)
        assert p.status == 402
        assert p.type == ProblemType.INSUFFICIENT_CREDITS.uri

    def test_extensions_populated(self):
        p = from_insufficient_credits(balance=5, required=10, client_id="client-1")
        assert p.extensions["balance"] == 5
        assert p.extensions["required"] == 10
        assert p.extensions["client_id"] == "client-1"

    def test_detail_message(self):
        p = from_insufficient_credits(balance=2, required=3)
        assert "2 available" in p.detail
        assert "3 required" in p.detail

    def test_no_client_id(self):
        p = from_insufficient_credits(balance=0, required=3)
        assert "client_id" not in p.extensions

    def test_response_content_type(self):
        p = from_insufficient_credits(balance=0, required=3)
        resp = p.to_response()
        assert resp.status_code == 402
        assert resp.media_type == "application/problem+json"


# ── 3e. Taxonomy Completeness Tests ─────────────────────────────────


class TestProblemType:
    def test_all_types_have_metadata(self):
        for pt in ProblemType:
            assert pt in _TYPE_METADATA, f"Missing metadata for {pt}"

    def test_all_uris_start_with_base(self):
        for pt in ProblemType:
            assert pt.uri.startswith(_ERROR_BASE), f"Bad URI for {pt}: {pt.uri}"

    def test_no_duplicate_slugs(self):
        slugs = [pt.value for pt in ProblemType]
        assert len(slugs) == len(set(slugs))

    def test_all_status_codes_valid(self):
        for pt, (status, _, _) in _TYPE_METADATA.items():
            assert 100 <= status <= 599, f"Invalid status {status} for {pt}"

    def test_exactly_16_types(self):
        assert len(ProblemType) == 16


class TestRecoveryHints:
    def test_all_server_tools_have_hints(self):
        """All tool contexts referenced in the plan have hints."""
        expected_tools = {
            "get_page_map",
            "get_page_state",
            "take_screenshot",
            "navigate_back",
            "scroll_page",
            "fill_form",
            "wait_for",
            "batch",
            "execute_action",
        }
        assert expected_tools == set(_RECOVERY_HINTS.keys())

    def test_prefix_matching_works(self):
        """Prefix-based hint lookup (e.g. 'batch [url]' → 'batch' hint)."""
        p = ProblemDetail(detail="test", _tool_context="batch [https://example.com]")
        result = p.to_mcp_text()
        assert _RECOVERY_HINTS["batch"] in result

    def test_exact_match_preferred_over_prefix(self):
        """Exact match takes precedence over prefix match."""
        p = ProblemDetail(detail="test", _tool_context="batch")
        result = p.to_mcp_text()
        assert _RECOVERY_HINTS["batch"] in result
