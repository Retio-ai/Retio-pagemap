# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""RequestContext — leaf module with minimal dependencies.

Extracted from server.py to break circular import chains.
Dependency graph: context.py <- server.py, context.py <- session_manager.py (acyclic).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable

from .cache import PageMapCache
from .template_cache import InMemoryTemplateCache

# Avoid importing BrowserSession at module level to keep this a leaf module.
# The type annotation uses a string forward reference instead.


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
