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


class PruningError(PageMapError):
    """HTML pruning pipeline failure."""


class PageMapBuildError(PageMapError):
    """PageMap construction failed (orchestrator-level)."""
