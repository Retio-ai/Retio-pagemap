"""Page Map: structured intermediate representation for AI agent web tasks.

Converts web pages from ~100K tokens to 2-5K token structured maps containing:
- interactables: actionable UI elements with affordances
- pruned_context: compressed page content (prices, titles, key info)
"""

from __future__ import annotations

from .core import Interactable, PageMap  # noqa: F401

__all__ = ["Interactable", "PageMap"]
