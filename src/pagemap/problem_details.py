# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""RFC 9457 Problem Details for HTTP APIs.

Provides a structured error taxonomy for PageMap, mapping internal
exceptions to standardised problem detail objects.  The module is a
near-leaf dependency (stdlib + errors.py + starlette lazy) so it can
be imported safely from any layer.

Key public API:

- ``ProblemType``   — 15-member StrEnum error taxonomy.
- ``ProblemDetail`` — frozen dataclass (→ JSON / Starlette response / MCP text).
- ``sanitize_detail()`` — scrub secrets & paths from error messages.
- Factory functions (``from_exception``, ``from_rate_limit``, …) — build
  ``ProblemDetail`` instances from specific error conditions.
- ``_RECOVERY_HINTS`` — per-tool recovery guidance (moved from server.py).

Type URI namespace: ``https://www.retio.ai/pagemap/errors/{slug}``
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

_ERROR_BASE = "https://www.retio.ai/pagemap/errors"

MAX_DETAIL_LENGTH = 200

# ── ProblemType taxonomy ─────────────────────────────────────────────


class ProblemType(StrEnum):
    """15-member error taxonomy for PageMap."""

    # Auth / security (HTTP middleware — Phase δ)
    AUTH_REQUIRED = "auth-required"
    AUTH_INVALID = "auth-invalid"
    SSRF_BLOCKED = "ssrf-blocked"
    ROBOTS_BLOCKED = "robots-blocked"
    RATE_LIMIT_EXCEEDED = "rate-limit-exceeded"

    # Browser / navigation (tool level)
    BROWSER_UNAVAILABLE = "browser-unavailable"
    PAGE_TIMEOUT = "page-timeout"
    SERVER_BUSY = "server-busy"

    # Action errors (tool level)
    REF_NOT_FOUND = "ref-not-found"
    INVALID_ACTION = "invalid-action"
    ACTION_TIMEOUT = "action-timeout"
    ACTION_FAILED = "action-failed"

    # Validation / resource (tool level)
    VALIDATION_ERROR = "validation-error"
    RESOURCE_EXHAUSTED = "resource-exhausted"
    DNS_RESOLUTION_FAILED = "dns-resolution-failed"

    @property
    def uri(self) -> str:
        """Full type URI for RFC 9457 ``type`` field."""
        return f"{_ERROR_BASE}/{self.value}"


# ── Per-type metadata: (status, title, recovery_hint) ────────────────

_TYPE_METADATA: dict[ProblemType, tuple[int, str, str]] = {
    ProblemType.AUTH_REQUIRED: (401, "Authentication Required", ""),
    ProblemType.AUTH_INVALID: (403, "Authentication Failed", ""),
    ProblemType.SSRF_BLOCKED: (403, "URL Blocked", "Provide a valid http:// or https:// URL."),
    ProblemType.ROBOTS_BLOCKED: (403, "Blocked by robots.txt", "Try a different URL on the same site."),
    ProblemType.RATE_LIMIT_EXCEEDED: (429, "Rate Limit Exceeded", ""),
    ProblemType.BROWSER_UNAVAILABLE: (503, "Browser Unavailable", "Call get_page_map to recover."),
    ProblemType.PAGE_TIMEOUT: (504, "Page Timed Out", ""),
    ProblemType.SERVER_BUSY: (503, "Server Busy", "Wait a moment, then retry."),
    ProblemType.REF_NOT_FOUND: (422, "Ref Not Found", "Call get_page_map to refresh refs."),
    ProblemType.INVALID_ACTION: (422, "Invalid Action", ""),
    ProblemType.ACTION_TIMEOUT: (504, "Action Timed Out", "Call get_page_map to refresh refs."),
    ProblemType.ACTION_FAILED: (500, "Action Failed", ""),
    ProblemType.VALIDATION_ERROR: (422, "Validation Error", ""),
    ProblemType.RESOURCE_EXHAUSTED: (422, "Resource Limit Exceeded", ""),
    ProblemType.DNS_RESOLUTION_FAILED: (502, "DNS Resolution Failed", ""),
}

# ── Per-tool recovery hints (moved from server.py) ───────────────────

_RECOVERY_HINTS: dict[str, str] = {
    "get_page_map": "Try again, or navigate to a different URL.",
    "get_page_state": "Call get_page_map to re-establish browser connection.",
    "take_screenshot": "Call get_page_map to verify page state, then retry.",
    "navigate_back": "Call get_page_map to check current page state.",
    "scroll_page": "Call get_page_map to refresh page state, then retry.",
    "fill_form": "Call get_page_map to refresh refs, then retry fill_form.",
    "wait_for": "Call get_page_map to check current page content.",
    "batch": "Check the URL and retry, or skip this URL.",
    "execute_action": "Call get_page_map to refresh refs and retry.",
}

# ── Secret sanitization patterns ─────────────────────────────────────

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Existing 5 patterns (extracted from server.py _safe_error)
    (re.compile(r"sk-[a-zA-Z0-9_-]{8,}"), "<redacted>"),
    (re.compile(r"Bearer\s+\S+"), "Bearer <redacted>"),
    (
        re.compile(
            r"(?:API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)\s*[=:]\s*\S+",
            re.IGNORECASE,
        ),
        "<redacted>",
    ),
    # New 5 patterns (2026 security hardening)
    (re.compile(r"Basic\s+[A-Za-z0-9+/=]{8,}"), "Basic <redacted>"),
    (re.compile(r"://[^@\s]+@"), "://<redacted>@"),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "<redacted>",
    ),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted>"),
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}"), "<redacted>"),
]

_PATH_PATTERN = re.compile(
    r"(/(?:Users|home|tmp|var|etc|opt|root|srv|proc|sys|usr|Library"
    r"|Applications|private|snap|mnt|media|nix)/[\w./-]+"
    r"|[A-Z]:\\[\w.\\-]+)"
)

# ── Chromium net::ERR_* classification ───────────────────────────────

_NET_ERR_RE = re.compile(r"net::ERR_(\w+)")

_DNS_CODES = {"NAME_NOT_RESOLVED"}
_CONNECTION_TIMED_OUT_CODES = {"CONNECTION_TIMED_OUT"}
_CONNECTION_CODES = {
    "CONNECTION_REFUSED",
    "CONNECTION_CLOSED",
    "CONNECTION_RESET",
    "EMPTY_RESPONSE",
    "ADDRESS_UNREACHABLE",
}

_HOSTNAME_RE = re.compile(r"https?://([^/:\s]+)")


def classify_network_error(exc_message: str) -> tuple[ProblemType, str] | None:
    """Classify a Playwright network error message into a ProblemType + human message.

    Returns ``None`` if *exc_message* does not contain a ``net::ERR_*`` code.
    """
    m = _NET_ERR_RE.search(exc_message)
    if m is None:
        return None
    code = m.group(1)

    # Extract hostname from URL in the message
    hm = _HOSTNAME_RE.search(exc_message)
    hostname = hm.group(1) if hm else ""

    if code in _DNS_CODES:
        host_part = f" '{hostname}'" if hostname else ""
        return ProblemType.DNS_RESOLUTION_FAILED, f"Could not resolve domain name{host_part}"

    if code in _CONNECTION_TIMED_OUT_CODES:
        host_part = f" to '{hostname}'" if hostname else ""
        return ProblemType.PAGE_TIMEOUT, f"Connection timed out{host_part}"

    if code in _CONNECTION_CODES:
        host_part = f" to '{hostname}'" if hostname else ""
        return ProblemType.ACTION_FAILED, f"Connection failed{host_part}"

    if "CERT" in code or "SSL" in code:
        host_part = f" for '{hostname}'" if hostname else ""
        return ProblemType.VALIDATION_ERROR, f"SSL/TLS error{host_part}"

    # Fallback: any other net::ERR_* code
    return ProblemType.ACTION_FAILED, f"Navigation failed (net::ERR_{code})"


# ── CLI-specific recovery hints ──────────────────────────────────────

_CLI_HINTS: dict[str, str] = {
    ProblemType.DNS_RESOLUTION_FAILED.uri: "Check the URL spelling and ensure the domain exists.",
    ProblemType.PAGE_TIMEOUT.uri: "The page took too long to load. Try again or check your connection.",
    ProblemType.BROWSER_UNAVAILABLE.uri: "Ensure Chromium is installed: playwright install chromium",
    ProblemType.VALIDATION_ERROR.uri: "Check the URL and try again with a valid https:// URL.",
    ProblemType.ACTION_FAILED.uri: "Try a different URL or check that the site is accessible.",
    ProblemType.SSRF_BLOCKED.uri: "Provide a valid public http:// or https:// URL.",
    ProblemType.RESOURCE_EXHAUSTED.uri: "The page is too large. Try a more specific URL.",
}


def sanitize_detail(text: str) -> str:
    """Scrub secrets and filesystem paths from *text*.

    Applies ``_SECRET_PATTERNS`` and ``_PATH_PATTERN``, then truncates
    to ``MAX_DETAIL_LENGTH`` characters.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = _PATH_PATTERN.sub("<path>", text)
    if len(text) > MAX_DETAIL_LENGTH:
        text = text[:MAX_DETAIL_LENGTH] + "..."
    return text


def _sanitize_extensions(extensions: dict[str, Any]) -> dict[str, Any]:
    """Sanitize string values in extensions dict."""
    result: dict[str, Any] = {}
    for key, value in extensions.items():
        if isinstance(value, str):
            result[key] = sanitize_detail(value)
        else:
            result[key] = value
    return result


# ── ProblemDetail dataclass ──────────────────────────────────────────

# Standard RFC 9457 fields that extensions must never shadow.
_STANDARD_FIELDS = frozenset({"type", "title", "status", "detail", "instance"})


@dataclass(frozen=True, slots=True)
class ProblemDetail:
    """RFC 9457 Problem Detail object.

    Immutable representation of a structured error.  Supports
    serialisation to JSON dict, JSON string, Starlette response,
    and legacy MCP text format.
    """

    type: str = "about:blank"
    title: str = ""
    status: int = 500
    detail: str = ""
    instance: str = ""
    extensions: dict[str, Any] = field(default_factory=dict)
    _tool_context: str = field(default="", repr=False)

    # -- Serialisation --

    def to_dict(self) -> dict[str, Any]:
        """RFC 9457 JSON dict.  Empty optional fields omitted, extensions merged at top level."""
        d: dict[str, Any] = {"type": self.type, "status": self.status}
        if self.title:
            d["title"] = self.title
        if self.detail:
            d["detail"] = self.detail
        if self.instance:
            d["instance"] = self.instance
        # Merge extensions at top level, but never shadow standard fields
        for k, v in self.extensions.items():
            if k not in _STANDARD_FIELDS:
                d[k] = v
        return d

    def to_json(self) -> str:
        """JSON string (``ensure_ascii=False``)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_response(self):
        """Starlette ``JSONResponse`` with RFC 9457 headers.

        - ``Content-Type: application/problem+json``
        - ``Cache-Control: no-store``
        - ``Content-Language: en``
        - ``Retry-After`` / ``RateLimit-*`` headers when present in extensions.
        """
        from starlette.responses import JSONResponse

        headers: dict[str, str] = {
            "Cache-Control": "no-store",
            "Content-Language": "en",
        }
        # Rate-limit headers
        if "retry_after" in self.extensions:
            headers["Retry-After"] = str(math.ceil(self.extensions["retry_after"]))
        if "limit" in self.extensions:
            headers["RateLimit-Limit"] = str(self.extensions["limit"])
        if "remaining" in self.extensions:
            headers["RateLimit-Remaining"] = str(self.extensions["remaining"])

        return JSONResponse(
            content=self.to_dict(),
            status_code=self.status,
            media_type="application/problem+json",
            headers=headers,
        )

    def to_mcp_text(self) -> str:
        """Legacy MCP text format (matches old ``_safe_error()`` output exactly).

        Format: ``"Error (<context>): <detail>. <hint>"``
        """
        context = self._tool_context
        detail = self.detail

        # Look up per-tool recovery hint: exact match → prefix fallback
        hint = _RECOVERY_HINTS.get(context, "")
        if not hint:
            for prefix in _RECOVERY_HINTS:
                if context.startswith(prefix):
                    hint = _RECOVERY_HINTS[prefix]
                    break
        if hint:
            return f"Error ({context}): {detail}. {hint}"
        return f"Error ({context}): {detail}"

    def to_cli_text(self) -> str:
        """Human-friendly CLI error message.

        Format::

            Error: <detail>
            Hint: <hint>
        """
        hint = _CLI_HINTS.get(self.type, "")
        lines = [f"Error: {self.detail}"]
        if hint:
            lines.append(f"Hint: {hint}")
        return "\n".join(lines)


# ── Exception → ProblemType mapping ──────────────────────────────────


def _exception_type_map() -> dict[type, ProblemType]:
    """Lazy-build mapping from exception classes to ProblemType.

    Uses lazy import to avoid circular dependency with errors.py.
    """
    from .errors import (
        ApiKeyError,
        BrowserError,
        PageMapBuildError,
        RateLimitError,
        ResourceExhaustionError,
        SanitizationError,
        SSRFError,
    )

    return {
        SSRFError: ProblemType.SSRF_BLOCKED,
        ApiKeyError: ProblemType.AUTH_INVALID,
        RateLimitError: ProblemType.RATE_LIMIT_EXCEEDED,
        BrowserError: ProblemType.BROWSER_UNAVAILABLE,
        ResourceExhaustionError: ProblemType.RESOURCE_EXHAUSTED,
        SanitizationError: ProblemType.ACTION_FAILED,
        PageMapBuildError: ProblemType.ACTION_FAILED,
    }


# ── Factory functions ────────────────────────────────────────────────


def from_exception(
    exc: Exception,
    *,
    tool_context: str = "",
    instance: str = "",
    extensions: dict[str, Any] | None = None,
) -> ProblemDetail:
    """Build a ProblemDetail from an exception.

    Maps known PageMap exception types to specific ProblemType values.
    Non-PageMapError exceptions produce a generic ``about:blank`` detail
    with a safe message to prevent internal state leakage.
    """
    from .errors import ApiKeyError, PageMapError, RateLimitError

    ext = dict(extensions) if extensions else {}
    exc_type = type(exc)
    type_map = _exception_type_map()

    # TimeoutError: PAGE_TIMEOUT for navigation contexts, ACTION_TIMEOUT otherwise
    if isinstance(exc, TimeoutError):
        nav_contexts = {"get_page_map", "batch"}
        is_nav = any(tool_context.startswith(p) for p in nav_contexts)
        problem_type = ProblemType.PAGE_TIMEOUT if is_nav else ProblemType.ACTION_TIMEOUT
        status, title, _ = _TYPE_METADATA[problem_type]
        return ProblemDetail(
            type=problem_type.uri,
            title=title,
            status=status,
            detail=sanitize_detail(str(exc)),
            instance=instance,
            extensions=_sanitize_extensions(ext),
            _tool_context=tool_context,
        )

    # Known PageMap exception types
    problem_type = type_map.get(exc_type)
    if problem_type is not None:
        status, title, _ = _TYPE_METADATA[problem_type]
        # Enrich extensions for specific error types
        if isinstance(exc, ApiKeyError) and exc.client_id:
            ext.setdefault("client_id", exc.client_id)
        if isinstance(exc, RateLimitError):
            if exc.retry_after:
                ext.setdefault("retry_after", exc.retry_after)
            if exc.limit:
                ext.setdefault("limit", exc.limit)
            ext.setdefault("remaining", exc.remaining)
        return ProblemDetail(
            type=problem_type.uri,
            title=title,
            status=status,
            detail=sanitize_detail(str(exc)),
            instance=instance,
            extensions=_sanitize_extensions(ext),
            _tool_context=tool_context,
        )

    # Playwright net::ERR_* network errors
    net_result = classify_network_error(str(exc))
    if net_result is not None:
        problem_type, human_msg = net_result
        status, title, _ = _TYPE_METADATA[problem_type]
        return ProblemDetail(
            type=problem_type.uri,
            title=title,
            status=status,
            detail=sanitize_detail(human_msg),
            instance=instance,
            extensions=_sanitize_extensions(ext),
            _tool_context=tool_context,
        )

    # Other PageMapError subclasses: use sanitized message
    if isinstance(exc, PageMapError):
        return ProblemDetail(
            type="about:blank",
            title="",
            status=500,
            detail=sanitize_detail(str(exc)),
            instance=instance,
            extensions=_sanitize_extensions(ext),
            _tool_context=tool_context,
        )

    # Non-PageMapError: generic detail to prevent internal state leakage
    return ProblemDetail(
        type="about:blank",
        title="",
        status=500,
        detail=sanitize_detail(str(exc)),
        instance=instance,
        extensions=_sanitize_extensions(ext),
        _tool_context=tool_context,
    )


def from_rate_limit(
    result: Any,
    *,
    client_id: str = "",
    tool_name: str = "",
    instance: str = "",
) -> ProblemDetail:
    """Build a 429 ProblemDetail from a RateLimitResult."""
    status, title, _ = _TYPE_METADATA[ProblemType.RATE_LIMIT_EXCEEDED]
    ext: dict[str, Any] = {
        "retry_after": result.retry_after,
        "limit": result.limit,
        "remaining": result.remaining,
    }
    if client_id:
        ext["client_id"] = client_id
    return ProblemDetail(
        type=ProblemType.RATE_LIMIT_EXCEEDED.uri,
        title=title,
        status=status,
        detail=f"Rate limit exceeded for {tool_name}." if tool_name else "Rate limit exceeded.",
        instance=instance,
        extensions=ext,
        _tool_context=tool_name,
    )


def from_auth_missing(*, instance: str = "") -> ProblemDetail:
    """Build a 401 ProblemDetail for missing authentication."""
    status, title, _ = _TYPE_METADATA[ProblemType.AUTH_REQUIRED]
    return ProblemDetail(
        type=ProblemType.AUTH_REQUIRED.uri,
        title=title,
        status=status,
        detail="API key required.",
        instance=instance,
    )


def from_auth_invalid(
    *,
    reason: str = "invalid",
    client_id: str = "",
    instance: str = "",
) -> ProblemDetail:
    """Build a 403 ProblemDetail for invalid authentication."""
    status, title, _ = _TYPE_METADATA[ProblemType.AUTH_INVALID]
    ext: dict[str, Any] = {"reason": reason}
    if client_id:
        ext["client_id"] = client_id
    return ProblemDetail(
        type=ProblemType.AUTH_INVALID.uri,
        title=title,
        status=status,
        detail=f"API key {reason}.",
        instance=instance,
        extensions=ext,
    )


def from_validation(
    detail: str,
    *,
    field_name: str = "",
    tool_context: str = "",
    instance: str = "",
) -> ProblemDetail:
    """Build a 422 ProblemDetail for validation errors."""
    status, title, _ = _TYPE_METADATA[ProblemType.VALIDATION_ERROR]
    ext: dict[str, Any] = {}
    if field_name:
        ext["field"] = field_name
    return ProblemDetail(
        type=ProblemType.VALIDATION_ERROR.uri,
        title=title,
        status=status,
        detail=sanitize_detail(detail),
        instance=instance,
        extensions=ext,
        _tool_context=tool_context,
    )


def from_ssrf(
    url: str,
    reason: str,
    *,
    instance: str = "",
    tool_context: str = "",
) -> ProblemDetail:
    """Build a 403 ProblemDetail for SSRF blocks."""
    status, title, _ = _TYPE_METADATA[ProblemType.SSRF_BLOCKED]
    return ProblemDetail(
        type=ProblemType.SSRF_BLOCKED.uri,
        title=title,
        status=status,
        detail=sanitize_detail(reason),
        instance=instance,
        extensions={"url": url},
        _tool_context=tool_context,
    )


def from_robots(
    url: str,
    *,
    origin: str = "",
    instance: str = "",
    tool_context: str = "",
) -> ProblemDetail:
    """Build a 403 ProblemDetail for robots.txt blocks."""
    status, title, _ = _TYPE_METADATA[ProblemType.ROBOTS_BLOCKED]
    ext: dict[str, Any] = {"url": url}
    if origin:
        ext["origin"] = origin
    return ProblemDetail(
        type=ProblemType.ROBOTS_BLOCKED.uri,
        title=title,
        status=status,
        detail="Access blocked by robots.txt.",
        instance=instance,
        extensions=ext,
        _tool_context=tool_context,
    )


def from_browser_dead(
    *,
    tool_context: str = "",
    instance: str = "",
) -> ProblemDetail:
    """Build a 503 ProblemDetail for browser connection loss."""
    status, title, _ = _TYPE_METADATA[ProblemType.BROWSER_UNAVAILABLE]
    return ProblemDetail(
        type=ProblemType.BROWSER_UNAVAILABLE.uri,
        title=title,
        status=status,
        detail="Browser connection lost.",
        instance=instance,
        _tool_context=tool_context,
    )


def from_server_busy(
    *,
    tool_context: str = "",
    instance: str = "",
) -> ProblemDetail:
    """Build a 503 ProblemDetail for tool lock contention."""
    status, title, _ = _TYPE_METADATA[ProblemType.SERVER_BUSY]
    return ProblemDetail(
        type=ProblemType.SERVER_BUSY.uri,
        title=title,
        status=status,
        detail="Another tool call is in progress.",
        instance=instance,
        _tool_context=tool_context,
    )
