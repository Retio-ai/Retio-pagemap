# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Telemetry event types, TypedDict payload definitions, and builder functions."""

from __future__ import annotations

from collections import Counter
from typing import TypedDict

# ── Event type constants (OTel naming) ───────────────────────────

# Server: get_page_map
NAVIGATION_START = "pagemap.navigation.start"
CACHE_HIT = "pagemap.cache.hit"
CACHE_REFRESH = "pagemap.cache.refresh"
FULL_BUILD = "pagemap.cache.full_build"
PIPELINE_COMPLETED = "pagemap.pipeline.completed"
PIPELINE_TIMEOUT = "pagemap.pipeline.timeout"

# Server: execute_action
ACTION_START = "pagemap.action.start"
ACTION_DOM_CHANGE = "pagemap.action.dom_change"
ACTION_RESULT = "pagemap.action.result"

# Server: fill_form
FILL_FORM_DOM_CHANGE = "pagemap.fill_form.dom_change"

# Server: scroll
SCROLL = "pagemap.scroll"

# Server: wait_for
WAIT_FOR_RESULT = "pagemap.wait_for.result"

# Server: batch
BATCH_START = "pagemap.batch.start"
BATCH_URL_RESULT = "pagemap.batch.url_result"
BATCH_COMPLETE = "pagemap.batch.complete"

# Pruning pipeline
PREPROCESS_COMPLETE = "pagemap.prune.preprocess_complete"
CHUNK_DECOMPOSE = "pagemap.prune.chunk_decompose"
AOM_FILTER_COMPLETE = "pagemap.prune.aom_filter_complete"
PRUNE_DECISIONS = "pagemap.prune.decisions"
COMPRESSION_COMPLETE = "pagemap.prune.compression_complete"
PRUNED_CONTEXT_COMPLETE = "pagemap.prune.context_complete"

# Guards & errors
RESOURCE_GUARD_TRIGGERED = "pagemap.guard.resource_triggered"
RESPONSE_SIZE_EXCEEDED = "pagemap.guard.response_size_exceeded"
HIDDEN_CONTENT_REMOVED = "pagemap.guard.hidden_removed"
TOOL_ERROR = "pagemap.tool.error"


# ── TypedDict payload definitions ────────────────────────────────


class NavigationStartPayload(TypedDict):
    url: str


class CacheHitPayload(TypedDict):
    tier: str


class CacheRefreshPayload(TypedDict):
    tier: str


class FullBuildPayload(TypedDict):
    tier: str


class PipelineCompletedPayload(TypedDict):
    tier: str
    interactables: int
    pruned_tokens: int
    stage_timings: dict[str, float]
    page_type: str


class PipelineTimeoutPayload(TypedDict):
    timed_out_at: str
    hint: str


class ActionStartPayload(TypedDict):
    ref: int
    action: str
    role: str
    affordance: str


class ActionDomChangePayload(TypedDict):
    severity: str
    reasons: list[str]


class ActionResultPayload(TypedDict):
    change: str
    refs_expired: bool


class FillFormDomChangePayload(TypedDict):
    severity: str
    reasons: list[str]


class ScrollPayload(TypedDict):
    direction: str
    pixels: int
    scroll_percent: int


class WaitForResultPayload(TypedDict):
    elapsed: float
    success: bool
    mode: str


class BatchStartPayload(TypedDict):
    urls_count: int
    valid_count: int


class BatchUrlResultPayload(TypedDict):
    url: str
    success: bool


class BatchCompletePayload(TypedDict):
    elapsed_ms: int
    success: int
    failed: int


class PreprocessCompletePayload(TypedDict):
    json_ld_count: int
    og_count: int
    rsc_count: int


class ChunkDecomposePayload(TypedDict):
    chunk_count: int
    has_main: bool


class AomFilterCompletePayload(TypedDict):
    total_nodes: int
    removed_nodes: int
    removal_reasons: dict[str, int]


class PruneDecisionsPayload(TypedDict):
    kept: int
    removed: int
    schema_name: str
    kept_reasons: dict[str, int]
    removed_reasons: dict[str, int]


class CompressionCompletePayload(TypedDict):
    before_len: int
    after_len: int


class PrunedContextCompletePayload(TypedDict):
    tokens: int
    budget: int
    prune_ms: float
    meta_ms: float
    compress_ms: float
    template_status: str
    page_type: str


class ResourceGuardTriggeredPayload(TypedDict):
    guard: str
    value: int
    limit: int


class ResponseSizeExceededPayload(TypedDict):
    tool: str
    size: int
    limit: int


class HiddenContentRemovedPayload(TypedDict):
    hidden_removed: int


class ToolErrorPayload(TypedDict):
    context: str
    error_type: str


# ── Payload builder functions ────────────────────────────────────


def navigation_start(*, url: str) -> NavigationStartPayload:
    return NavigationStartPayload(url=url)


def cache_hit(*, tier: str) -> CacheHitPayload:
    return CacheHitPayload(tier=tier)


def cache_refresh(*, tier: str) -> CacheRefreshPayload:
    return CacheRefreshPayload(tier=tier)


def full_build(*, tier: str) -> FullBuildPayload:
    return FullBuildPayload(tier=tier)


def pipeline_completed(
    *,
    tier: str,
    interactables: int,
    pruned_tokens: int,
    stage_timings: dict[str, float],
    page_type: str,
) -> PipelineCompletedPayload:
    return PipelineCompletedPayload(
        tier=tier,
        interactables=interactables,
        pruned_tokens=pruned_tokens,
        stage_timings=stage_timings,
        page_type=page_type,
    )


def pipeline_timeout(*, timed_out_at: str, hint: str) -> PipelineTimeoutPayload:
    return PipelineTimeoutPayload(timed_out_at=timed_out_at, hint=hint)


def action_start(*, ref: int, action: str, role: str, affordance: str) -> ActionStartPayload:
    return ActionStartPayload(ref=ref, action=action, role=role, affordance=affordance)


def action_dom_change(*, severity: str, reasons: list[str]) -> ActionDomChangePayload:
    return ActionDomChangePayload(severity=severity, reasons=reasons)


def action_result(*, change: str, refs_expired: bool) -> ActionResultPayload:
    return ActionResultPayload(change=change, refs_expired=refs_expired)


def fill_form_dom_change(*, severity: str, reasons: list[str]) -> FillFormDomChangePayload:
    return FillFormDomChangePayload(severity=severity, reasons=reasons)


def scroll(*, direction: str, pixels: int, scroll_percent: int) -> ScrollPayload:
    return ScrollPayload(direction=direction, pixels=pixels, scroll_percent=scroll_percent)


def wait_for_result(*, elapsed: float, success: bool, mode: str) -> WaitForResultPayload:
    return WaitForResultPayload(elapsed=elapsed, success=success, mode=mode)


def batch_start(*, urls_count: int, valid_count: int) -> BatchStartPayload:
    return BatchStartPayload(urls_count=urls_count, valid_count=valid_count)


def batch_url_result(*, url: str, success: bool) -> BatchUrlResultPayload:
    return BatchUrlResultPayload(url=url, success=success)


def batch_complete(*, elapsed_ms: int, success: int, failed: int) -> BatchCompletePayload:
    return BatchCompletePayload(elapsed_ms=elapsed_ms, success=success, failed=failed)


def preprocess_complete(*, json_ld_count: int, og_count: int, rsc_count: int) -> PreprocessCompletePayload:
    return PreprocessCompletePayload(json_ld_count=json_ld_count, og_count=og_count, rsc_count=rsc_count)


def chunk_decompose(*, chunk_count: int, has_main: bool) -> ChunkDecomposePayload:
    return ChunkDecomposePayload(chunk_count=chunk_count, has_main=has_main)


def aom_filter_complete(
    *, total_nodes: int, removed_nodes: int, removal_reasons: Counter | dict
) -> AomFilterCompletePayload:
    reasons = dict(removal_reasons) if isinstance(removal_reasons, Counter) else removal_reasons
    return AomFilterCompletePayload(total_nodes=total_nodes, removed_nodes=removed_nodes, removal_reasons=reasons)


def prune_decisions(
    *,
    kept: int,
    removed: int,
    schema_name: str,
    kept_reasons: Counter | dict,
    removed_reasons: Counter | dict,
) -> PruneDecisionsPayload:
    kr = dict(kept_reasons) if isinstance(kept_reasons, Counter) else kept_reasons
    rr = dict(removed_reasons) if isinstance(removed_reasons, Counter) else removed_reasons
    return PruneDecisionsPayload(
        kept=kept,
        removed=removed,
        schema_name=schema_name,
        kept_reasons=kr,
        removed_reasons=rr,
    )


def compression_complete(*, before_len: int, after_len: int) -> CompressionCompletePayload:
    return CompressionCompletePayload(before_len=before_len, after_len=after_len)


def pruned_context_complete(
    *,
    tokens: int,
    budget: int,
    prune_ms: float,
    meta_ms: float,
    compress_ms: float,
    template_status: str,
    page_type: str,
) -> PrunedContextCompletePayload:
    return PrunedContextCompletePayload(
        tokens=tokens,
        budget=budget,
        prune_ms=prune_ms,
        meta_ms=meta_ms,
        compress_ms=compress_ms,
        template_status=template_status,
        page_type=page_type,
    )


def resource_guard_triggered(*, guard: str, value: int, limit: int) -> ResourceGuardTriggeredPayload:
    return ResourceGuardTriggeredPayload(guard=guard, value=value, limit=limit)


def hidden_content_removed(*, hidden_removed: int) -> HiddenContentRemovedPayload:
    return HiddenContentRemovedPayload(hidden_removed=hidden_removed)


def tool_error(*, context: str, error_type: str) -> ToolErrorPayload:
    return ToolErrorPayload(context=context, error_type=error_type)
