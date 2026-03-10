"""Backward-compat shim — import from pagemap.core.pruning.preprocessor instead."""

from pagemap.core.pruning.preprocessor import (  # noqa: F401
    _REMOVE_TAGS,
    _SIBLING_GROUP_MAX_CHARS,
    _SIBLING_SINGLE_MAX_CHARS,
    _clean_html_pass1,
    _decompose_element,
    _extract_json_ld,
    _extract_og_meta,
    _extract_rsc_data,
    _group_small_siblings,
    preprocess,
)

__all__ = ["preprocess"]
