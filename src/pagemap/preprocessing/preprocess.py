"""Backward-compat shim — import from pagemap.core.preprocessing.preprocess instead."""

from pagemap.core.preprocessing.preprocess import (  # noqa: F401
    count_tokens,
    count_tokens_approx,
    semantic_html,
    strip_html,
)

__all__ = ["count_tokens", "count_tokens_approx", "semantic_html", "strip_html"]
