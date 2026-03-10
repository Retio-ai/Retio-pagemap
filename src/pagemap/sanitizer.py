"""Backward-compat shim — import from pagemap.core.sanitizer instead."""

from pagemap.core.sanitizer import (  # noqa: F401
    _escape_attr,
    _unescape_entities,
    add_content_boundary,
    sanitize_content_block,
    sanitize_text,
)

__all__ = ["add_content_boundary", "sanitize_content_block", "sanitize_text"]
