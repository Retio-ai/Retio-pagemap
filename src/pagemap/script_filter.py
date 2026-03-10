"""Backward-compat shim — import from pagemap.core.script_filter instead."""

from pagemap.core.script_filter import (  # noqa: F401
    FilterResult,
    Script,
    ScriptProfile,
    classify_char,
    detect_page_script,
    filter_lines,
    profile_text,
)

__all__ = [
    "FilterResult",
    "Script",
    "ScriptProfile",
    "classify_char",
    "detect_page_script",
    "filter_lines",
    "profile_text",
]
