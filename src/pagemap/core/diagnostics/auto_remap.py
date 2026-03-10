# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Auto-remap on action failure — rebuild PageMap and return enhanced response.

When an action fails due to element_hidden, element_blocked, or state_changed,
automatically rebuild the PageMap so the agent gets fresh refs in the error response.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import ActionDiagnosis, ActionFailureType
from .suggested_actions import suggest_action_recovery

logger = logging.getLogger(__name__)

_REMAPPABLE: frozenset[ActionFailureType] = frozenset(
    {ActionFailureType.ELEMENT_HIDDEN, ActionFailureType.ELEMENT_BLOCKED, ActionFailureType.STATE_CHANGED}
)
MAX_AUTO_REMAPS = 1


async def maybe_auto_remap(
    *,
    diagnosis: ActionDiagnosis,
    ctx: Any,
    original_error: str,
) -> str | None:
    """If remappable, rebuild PageMap. Returns enhanced JSON response or None. Never raises."""
    try:
        return await _auto_remap_impl(
            diagnosis=diagnosis,
            ctx=ctx,
            original_error=original_error,
        )
    except Exception as e:
        logger.debug("auto_remap failed: %s", e)
        return None


async def _auto_remap_impl(
    *,
    diagnosis: ActionDiagnosis,
    ctx: Any,
    original_error: str,
) -> str | None:
    if diagnosis.failure_type not in _REMAPPABLE:
        return None

    # Check remap counter on cache (ctx is frozen dataclass, cache is mutable)
    remap_count = getattr(ctx.cache, "_auto_remap_count", 0)
    if remap_count >= MAX_AUTO_REMAPS:
        logger.debug("auto_remap: max remaps reached (%d)", MAX_AUTO_REMAPS)
        return None

    # Attempt rebuild
    try:
        from ..page_map_builder import build_page_map_live
        from ..serializer import to_agent_prompt

        session = await ctx.get_session()

        page_map = await build_page_map_live(session)
        prompt = to_agent_prompt(page_map)

        # Store in cache if available
        if hasattr(ctx, "cache"):
            ctx.cache.store(page_map, None)

        # Increment counter on cache (mutable)
        ctx.cache._auto_remap_count = remap_count + 1

        # Emit telemetry
        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import DIAGNOSTIC_AUTO_REMAP

            emit(
                DIAGNOSTIC_AUTO_REMAP,
                {
                    "failure_type": diagnosis.failure_type.value,
                    "ref": diagnosis.ref,
                    "action": diagnosis.action,
                },
            )
        except Exception:  # nosec B110
            pass

        # Build suggestions
        suggestions = suggest_action_recovery(diagnosis)
        suggestions_list = [
            {
                "action": sa.action,
                "reason": sa.reason,
                "priority": sa.priority,
                **({"params": sa.params} if sa.params else {}),
            }
            for sa in suggestions
        ]

        data: dict[str, Any] = {
            "error": original_error[:200],
            "refs_expired": True,
            "diagnosis": {
                "failure_type": diagnosis.failure_type.value,
                "confidence": diagnosis.confidence,
            },
            "suggested_actions": suggestions_list,
            "auto_remap": {
                "status": "success",
                "page_map": prompt,
            },
        }
        return json.dumps(data, ensure_ascii=False)

    except Exception as e:
        logger.debug("auto_remap rebuild failed: %s", e)
        return None
