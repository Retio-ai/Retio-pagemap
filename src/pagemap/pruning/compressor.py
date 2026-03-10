"""Backward-compat shim — import from pagemap.core.pruning.compressor instead."""

from pagemap.core.pruning.compressor import (  # noqa: F401
    _EMPTY_TAG_RE,
    _extract_section_label,
    _extract_wrapper_tag,
    _xpath_sort_key,
    compress_html,
    remerge_chunks,
)

__all__ = ["compress_html", "remerge_chunks"]
