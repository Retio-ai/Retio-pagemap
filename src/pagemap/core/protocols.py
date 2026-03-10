# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Structural protocols for dependency inversion.

Allows core modules (page_map_builder, interactive_detector, etc.) to depend
on abstract interfaces rather than concrete browser_session implementations.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BrowserSessionProtocol(Protocol):
    """Minimal interface consumed by page_map_builder.

    BrowserSession satisfies this via structural subtyping — no explicit
    inheritance required.
    """

    @property
    def page(self) -> Any: ...

    async def navigate(self, url: str) -> Any: ...

    async def get_page_url(self) -> str: ...

    async def get_page_title(self) -> str: ...

    async def get_page_html(self) -> str: ...

    async def load_html(self, html: str, base_url: str = "about:blank") -> None: ...
