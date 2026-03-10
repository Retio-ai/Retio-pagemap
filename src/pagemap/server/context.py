# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""RequestContext — leaf module with minimal dependencies.

Extracted from server.py to break circular import chains.
Dependency graph: context.py <- server.py, context.py <- session_manager.py (acyclic).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable

from pagemap.cache import PageMapCache
from pagemap.template_cache import InMemoryTemplateCache

# Avoid importing BrowserSession at module level to keep this a leaf module.
# The type annotation uses a string forward reference instead.


@dataclasses.dataclass(slots=True)
class BuildContext:
    """Request-scoped build tracking for S2 ground truth."""

    build_request_id: str = ""
    tier: str = ""
    interactable_count: int = 0
    total_dom_interactives: int = 0
    pruned_tokens: int = 0
    total_page_tokens: int = 0
    first_action_consumed: bool = False
    dom_change_delta: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RequestContext:
    """Per-request context passed to tool _impl functions.

    STDIO: created by _create_stdio_context() from module _state.
    HTTP (Phase β): created by SessionManager with per-session state.
    """

    request_id: str
    session_id: str
    client_id: str
    cache: PageMapCache
    template_cache: InMemoryTemplateCache
    get_session: Callable[[], Awaitable] = dataclasses.field(repr=False)
    client_ip: str = ""
    trace_id: str = ""  # S6: OTel trace ID (from middleware or request_id fallback)
    auth_method: str = ""  # S4: "api_key" | "jwt" | "" (STDIO)
    user_id: str = ""  # S4: Supabase user ID (from JWT sub claim)
    tenant_id: str = ""  # S3: Tenant ID for per-tenant session limits
    scroll_merge_state: object | None = None  # S9: ScrollMergeState (lazy)
    experiment_id: str = ""  # S5: CQP A/B experiment ID
    experiment_variant: str = ""  # S5: CQP A/B variant name
    build_context: BuildContext | None = None  # S2: ground truth tracking
    multi_tab: object | None = None  # Phase 1: per-session MultiTabSession
    get_or_create_multi_tab: Callable[[], Awaitable] | None = dataclasses.field(default=None, repr=False)
