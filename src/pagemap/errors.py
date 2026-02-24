# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""PageMap exception hierarchy.

All PageMap-specific errors inherit from PageMapError, allowing callers
to catch the base class for any PageMap failure or specific subclasses
for targeted handling.
"""

from __future__ import annotations


class PageMapError(Exception):
    """Base exception for all PageMap errors."""


class BrowserError(PageMapError):
    """Browser session launch, navigation, or interaction failure."""


class SSRFError(PageMapError):
    """URL validation blocked a server-side request forgery attempt."""


class SanitizationError(PageMapError):
    """Content sanitization failure (should not reach users)."""


class PageMapBuildError(PageMapError):
    """PageMap construction failed (orchestrator-level)."""


class ResourceExhaustionError(PageMapError):
    """Page exceeds resource limits (DOM nodes, HTML size, etc.)."""


class ApiKeyError(PageMapError):
    """API key validation failure (invalid, expired, or revoked)."""

    def __init__(self, message: str, *, client_id: str = "") -> None:
        super().__init__(message)
        self.client_id = client_id


class RateLimitError(PageMapError):
    """Request rate limit exceeded."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float = 0.0,
        limit: int = 0,
        remaining: int = 0,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.limit = limit
        self.remaining = remaining
