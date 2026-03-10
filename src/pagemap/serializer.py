"""Backward-compat shim — import from pagemap.core.serializer instead."""

from pagemap.core.serializer import (  # noqa: F401
    _render_ecommerce_section,
    _render_interactable_line,
    estimate_prompt_tokens,
    to_agent_prompt,
    to_agent_prompt_diff,
    to_agent_prompt_secure,
    to_dict,
    to_json,
)

__all__ = [
    "estimate_prompt_tokens",
    "to_agent_prompt",
    "to_agent_prompt_diff",
    "to_agent_prompt_secure",
    "to_dict",
    "to_json",
]
