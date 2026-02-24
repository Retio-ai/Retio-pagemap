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

# Interactive detection
NOISE_FILTER_APPLIED = "pagemap.detect.noise_filter_applied"

# Image filtering
IMAGE_FILTER_APPLIED = "pagemap.detect.image_filter_applied"

# Language filtering
LANG_FILTER_APPLIED = "pagemap.detect.lang_filter_applied"

# Content extraction quality
MCG_ACTIVATED = "pagemap.prune.mcg_activated"
GRID_WHITELIST_APPLIED = "pagemap.prune.grid_whitelist_applied"
CONTENT_RESCUE = "pagemap.prune.content_rescue"

# Guards & errors
RESOURCE_GUARD_TRIGGERED = "pagemap.guard.resource_triggered"
RESPONSE_SIZE_EXCEEDED = "pagemap.guard.response_size_exceeded"
HIDDEN_CONTENT_REMOVED = "pagemap.guard.hidden_removed"
TOOL_ERROR = "pagemap.tool.error"

# Robots
ROBOTS_BLOCKED = "pagemap.robots.blocked"

# Captcha / WAF block detection
CAPTCHA_DETECTED = "pagemap.captcha.detected"

# Auth
AUTH_REJECTED = "pagemap.auth.rejected"

# Rate limiting
RATE_LIMIT_EXCEEDED = "pagemap.rate_limit.exceeded"
RATE_LIMIT_WARNING = "pagemap.rate_limit.warning"

# Security events (Soft Gate taxonomy)
SSRF_BLOCKED = "pagemap.security.ssrf_blocked"
DNS_REBINDING_BLOCKED = "pagemap.security.dns_rebinding_blocked"
BROWSER_DEAD = "pagemap.security.browser_dead"
PROMPT_INJECTION_SANITIZED = "pagemap.security.prompt_injection_sanitized"


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
    grid_whitelist_count: int
    content_rescue_count: int


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
    schema_name: str
    extraction_quality: float
    mcg_activated: bool
    grid_whitelist_count: int


class McgActivatedPayload(TypedDict):
    original_tokens: int
    rescued_tokens: int
    page_type: str


class GridWhitelistAppliedPayload(TypedDict):
    container_count: int


class ContentRescuePayload(TypedDict):
    rescued_count: int


class NoiseFilterAppliedPayload(TypedDict):
    total_interactables: int
    noise_demoted: int
    noise_roles: dict[str, int]


class ImageFilterAppliedPayload(TypedDict):
    total_candidates: int
    after_decorative_filter: int
    after_size_attrs_filter: int
    after_all_filters: int
    after_picture_merge: int
    after_dedup: int
    final_count: int
    structured_image_merged: bool


class LangFilterAppliedPayload(TypedDict):
    page_script: str
    removed_count: int
    tagged_count: int
    total_lines: int


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


class CaptchaDetectedPayload(TypedDict):
    url: str
    http_status: int | None


class RobotsBlockedPayload(TypedDict):
    url: str
    origin: str


class AuthRejectedPayload(TypedDict):
    client_id: str
    reason: str


class RateLimitExceededPayload(TypedDict):
    client_id: str
    tool: str
    cost: int
    remaining: int
    retry_after: float


class RateLimitWarningPayload(TypedDict):
    client_id: str
    remaining: int
    limit: int


class SsrfBlockedPayload(TypedDict):
    url: str
    reason: str
    client_ip: str


class DnsRebindingBlockedPayload(TypedDict):
    url: str
    resolved_ip: str
    client_ip: str


class BrowserDeadPayload(TypedDict):
    session_id: str
    error: str


class PromptInjectionSanitizedPayload(TypedDict):
    field: str
    pattern: str


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
    schema_name: str,
    extraction_quality: float = 0.0,
    mcg_activated: bool = False,
    grid_whitelist_count: int = 0,
) -> PrunedContextCompletePayload:
    return PrunedContextCompletePayload(
        tokens=tokens,
        budget=budget,
        prune_ms=prune_ms,
        meta_ms=meta_ms,
        compress_ms=compress_ms,
        template_status=template_status,
        page_type=page_type,
        schema_name=schema_name,
        extraction_quality=extraction_quality,
        mcg_activated=mcg_activated,
        grid_whitelist_count=grid_whitelist_count,
    )


def mcg_activated(*, original_tokens: int, rescued_tokens: int, page_type: str) -> McgActivatedPayload:
    return McgActivatedPayload(original_tokens=original_tokens, rescued_tokens=rescued_tokens, page_type=page_type)


def grid_whitelist_applied(*, container_count: int) -> GridWhitelistAppliedPayload:
    return GridWhitelistAppliedPayload(container_count=container_count)


def content_rescue(*, rescued_count: int) -> ContentRescuePayload:
    return ContentRescuePayload(rescued_count=rescued_count)


def noise_filter_applied(
    *, total_interactables: int, noise_demoted: int, noise_roles: dict[str, int]
) -> NoiseFilterAppliedPayload:
    return NoiseFilterAppliedPayload(
        total_interactables=total_interactables,
        noise_demoted=noise_demoted,
        noise_roles=noise_roles,
    )


def image_filter_applied(
    *,
    total_candidates: int,
    after_decorative_filter: int,
    after_size_attrs_filter: int,
    after_all_filters: int,
    after_picture_merge: int,
    after_dedup: int,
    final_count: int,
    structured_image_merged: bool,
) -> ImageFilterAppliedPayload:
    return ImageFilterAppliedPayload(
        total_candidates=total_candidates,
        after_decorative_filter=after_decorative_filter,
        after_size_attrs_filter=after_size_attrs_filter,
        after_all_filters=after_all_filters,
        after_picture_merge=after_picture_merge,
        after_dedup=after_dedup,
        final_count=final_count,
        structured_image_merged=structured_image_merged,
    )


def lang_filter_applied(
    *, page_script: str, removed_count: int, tagged_count: int, total_lines: int
) -> LangFilterAppliedPayload:
    return LangFilterAppliedPayload(
        page_script=page_script,
        removed_count=removed_count,
        tagged_count=tagged_count,
        total_lines=total_lines,
    )


def resource_guard_triggered(*, guard: str, value: int, limit: int) -> ResourceGuardTriggeredPayload:
    return ResourceGuardTriggeredPayload(guard=guard, value=value, limit=limit)


def hidden_content_removed(*, hidden_removed: int) -> HiddenContentRemovedPayload:
    return HiddenContentRemovedPayload(hidden_removed=hidden_removed)


def tool_error(*, context: str, error_type: str) -> ToolErrorPayload:
    return ToolErrorPayload(context=context, error_type=error_type)


def robots_blocked(*, url: str, origin: str) -> RobotsBlockedPayload:
    return RobotsBlockedPayload(url=url, origin=origin)


def auth_rejected(*, client_id: str, reason: str) -> AuthRejectedPayload:
    return AuthRejectedPayload(client_id=client_id, reason=reason)


def rate_limit_exceeded(
    *, client_id: str, tool: str, cost: int, remaining: int, retry_after: float
) -> RateLimitExceededPayload:
    return RateLimitExceededPayload(
        client_id=client_id, tool=tool, cost=cost, remaining=remaining, retry_after=retry_after
    )


def rate_limit_warning(*, client_id: str, remaining: int, limit: int) -> RateLimitWarningPayload:
    return RateLimitWarningPayload(client_id=client_id, remaining=remaining, limit=limit)


def ssrf_blocked(*, url: str, reason: str, client_ip: str) -> SsrfBlockedPayload:
    return SsrfBlockedPayload(url=url, reason=reason, client_ip=client_ip)


def dns_rebinding_blocked(*, url: str, resolved_ip: str, client_ip: str) -> DnsRebindingBlockedPayload:
    return DnsRebindingBlockedPayload(url=url, resolved_ip=resolved_ip, client_ip=client_ip)


def browser_dead(*, session_id: str, error: str) -> BrowserDeadPayload:
    return BrowserDeadPayload(session_id=session_id, error=error)


def prompt_injection_sanitized(*, field: str, pattern: str) -> PromptInjectionSanitizedPayload:
    return PromptInjectionSanitizedPayload(field=field, pattern=pattern)
