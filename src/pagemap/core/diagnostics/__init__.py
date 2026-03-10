# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Agent Self-Healing Diagnostics — core types, feature flag, and registry router.

Detects page failure states (bot blocks, out-of-stock, empty results, etc.),
diagnoses action failures, and provides recovery hints for AI agents.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .. import Interactable
    from ..ecommerce import BarrierResult

logger = logging.getLogger(__name__)

# ── Feature flag (cached at module load) ───────────────────────────
DIAGNOSTICS_ENABLED: bool = os.environ.get("ENABLE_DIAGNOSTICS", "1").lower() in (
    "1",
    "true",
    "yes",
)

# ── Enums ──────────────────────────────────────────────────────────


class PageFailureState(StrEnum):
    LOGIN_REQUIRED = "login_required"
    OUT_OF_STOCK = "out_of_stock"
    EMPTY_RESULTS = "empty_results"
    BOT_BLOCKED = "bot_blocked"
    ERROR_PAGE = "error_page"
    AGE_VERIFICATION = "age_verification"
    REGION_RESTRICTED = "region_restricted"


class ActionFailureType(StrEnum):
    ELEMENT_HIDDEN = "element_hidden"
    ELEMENT_BLOCKED = "element_blocked"
    STATE_CHANGED = "state_changed"
    NAVIGATION_UNEXPECTED = "navigation_unexpected"
    TIMEOUT_EXCEEDED = "timeout_exceeded"


class AntibotProvider(StrEnum):
    TURNSTILE = "turnstile"
    RECAPTCHA = "recaptcha"
    HCAPTCHA = "hcaptcha"
    CLOUDFLARE = "cloudflare"
    AKAMAI = "akamai"
    GENERIC = "generic"


class SpaFramework(StrEnum):
    REACT = "react"
    NEXTJS = "nextjs"
    VUE = "vue"
    NUXT = "nuxt"
    ANGULAR = "angular"
    SVELTE = "svelte"
    UNKNOWN = "unknown"


# ── Dataclasses ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PageStateDiagnosis:
    state: PageFailureState
    confidence: float  # 0.0-1.0
    signals: tuple[str, ...]
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AntibotDetection:
    provider: AntibotProvider
    confidence: float
    signals: tuple[str, ...]
    challenge_visible: bool = False
    stealth_tips: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpaStatus:
    framework: SpaFramework
    hydrated: bool
    has_skeleton: bool = False
    confidence: float = 0.0
    signals: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PruningConfidence:
    overall_confidence: float
    removal_rate: float
    chunk_selection_ratio: float
    has_main_content: bool
    potentially_missed_regions: tuple[str, ...] = ()
    token_reduction_pct: float = 0.0
    signals: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionDiagnosis:
    failure_type: ActionFailureType
    confidence: float
    signals: tuple[str, ...]
    original_error: str = ""
    ref: int = 0
    action: str = ""


@dataclass(frozen=True, slots=True)
class SuggestedAction:
    action: str  # "get_page_map" | "scroll_page" | "wait_for" | "navigate" | "execute_action"
    reason: str
    priority: int = 1
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """Top-level container. Stored on PageMap.diagnostics."""

    page_state: PageStateDiagnosis | None = None
    antibot: AntibotDetection | None = None
    spa_status: SpaStatus | None = None
    pruning_confidence: PruningConfidence | None = None
    suggested_actions: tuple[SuggestedAction, ...] = ()

    def has_issues(self) -> bool:
        """True if any diagnostic detected an issue."""
        if self.page_state is not None:
            return True
        if self.antibot is not None and self.antibot.challenge_visible:
            return True
        return bool(self.spa_status is not None and not self.spa_status.hydrated)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, omitting empty fields."""
        d: dict[str, Any] = {}
        if self.page_state is not None:
            d["page_state"] = {
                "state": self.page_state.state.value,
                "confidence": self.page_state.confidence,
                "signals": list(self.page_state.signals),
                **({"detail": self.page_state.detail} if self.page_state.detail else {}),
            }
        if self.antibot is not None:
            d["antibot"] = {
                "provider": self.antibot.provider.value,
                "confidence": self.antibot.confidence,
                "signals": list(self.antibot.signals),
                "challenge_visible": self.antibot.challenge_visible,
            }
        if self.spa_status is not None:
            d["spa_status"] = {
                "framework": self.spa_status.framework.value,
                "hydrated": self.spa_status.hydrated,
                **({"has_skeleton": True} if self.spa_status.has_skeleton else {}),
            }
        if self.pruning_confidence is not None:
            d["pruning_confidence"] = {
                "overall_confidence": round(self.pruning_confidence.overall_confidence, 2),
                "removal_rate": round(self.pruning_confidence.removal_rate, 2),
                "has_main_content": self.pruning_confidence.has_main_content,
            }
        if self.suggested_actions:
            d["suggested_actions"] = [
                {
                    "action": sa.action,
                    "reason": sa.reason,
                    "priority": sa.priority,
                    **({"params": sa.params} if sa.params else {}),
                }
                for sa in self.suggested_actions
            ]
        return d

    def warning_message(self) -> str | None:
        """Agent-facing 1-line summary. None if no issues."""
        if self.page_state is not None:
            return f"Page issue: {self.page_state.state.value}" + (
                f" — {self.page_state.detail}" if self.page_state.detail else ""
            )
        if self.antibot is not None and self.antibot.challenge_visible:
            return f"Anti-bot challenge visible ({self.antibot.provider.value})"
        if self.spa_status is not None and not self.spa_status.hydrated:
            return f"SPA not hydrated ({self.spa_status.framework.value})"
        return None


@dataclass(slots=True)
class ScrollMergeState:
    """Session-level mutable state for infinite scroll dedup."""

    accumulated_keys: set[str] = field(default_factory=set)
    total_seen: int = 0
    scroll_count: int = 0
    last_new_count: int = 0
    url: str = ""

    def reset(self) -> None:
        self.accumulated_keys.clear()
        self.total_seen = 0
        self.scroll_count = 0
        self.last_new_count = 0
        self.url = ""


@dataclass(slots=True)
class AntibotSessionState:
    """Session-level mutable state for antibot tracking."""

    detection_count: int = 0
    last_provider: str = ""
    consecutive_blocks: int = 0
    first_detected_at: str = ""
    resolved: bool = False

    def reset(self) -> None:
        self.detection_count = 0
        self.last_provider = ""
        self.consecutive_blocks = 0
        self.first_detected_at = ""
        self.resolved = False


# ── Registry-based detector pipeline ──────────────────────────────


def run_page_diagnostics(
    *,
    raw_html: str,
    html_lower: str,
    page_url: str,
    page_type: str,
    interactables: list[Interactable],
    barrier: BarrierResult | None = None,
    warnings: list[str],
    metadata: dict[str, Any],
    http_status: int | None = None,
    pruning_result: Any | None = None,
    pruned_regions: set[str] | None = None,
    spa_signals: dict | None = None,
) -> DiagnosticResult | None:
    """Run all page diagnostic detectors. Never raises.

    Returns DiagnosticResult or None if no issues detected.
    """
    page_state = None
    antibot = None
    pruning_conf = None
    spa_status = None
    suggested: list[SuggestedAction] = []

    # 1. Page state detection
    try:
        from .page_state_detector import detect_page_state

        page_state = detect_page_state(
            raw_html=raw_html,
            html_lower=html_lower,
            page_type=page_type,
            barrier=barrier,
            interactables=interactables,
            metadata=metadata,
            url=page_url,
            http_status=http_status,
        )
    except Exception:  # nosec B110
        pass

    # 2. Antibot detection
    try:
        from .antibot_detector import detect_antibot

        antibot = detect_antibot(raw_html=raw_html, html_lower=html_lower)
    except Exception:  # nosec B110
        pass

    # 3. Pruning confidence
    try:
        from .pruning_confidence import assess_pruning_confidence

        pruning_conf = assess_pruning_confidence(
            pruning_result=pruning_result,
            page_type=page_type,
            pruned_regions=pruned_regions or set(),
            interactable_count=len(interactables),
        )
    except Exception:  # nosec B110
        pass

    # 4. SPA status
    try:
        from .spa_loader import parse_spa_signals

        spa_status = parse_spa_signals(spa_signals)
    except Exception:  # nosec B110
        pass

    # 5. Suggested recovery actions
    try:
        from .suggested_actions import suggest_page_recovery

        if page_state is not None:
            suggested.extend(suggest_page_recovery(page_state))
    except Exception:  # nosec B110
        pass

    # Apply side effects: blocked page warning + telemetry
    _BLOCKED_WARNING = (
        "Page is blocked by anti-bot protection (captcha/WAF). "
        "Content shown is from the block page, not the intended page. "
        "Try: (1) a different URL on the same site, (2) a less protected page, "
        "or (3) inform the user the site requires manual verification."
    )
    if page_state is not None and page_state.state == PageFailureState.BOT_BLOCKED:
        if _BLOCKED_WARNING not in warnings:
            warnings.append(_BLOCKED_WARNING)
        blocked_info: dict[str, Any] = {"detected": True}
        if http_status is not None:
            blocked_info["http_status"] = http_status
        metadata["blocked_info"] = blocked_info
        # Force cache eviction for bot-blocked pages
        metadata["_force_cache_evict"] = True
        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import CAPTCHA_DETECTED

            emit(CAPTCHA_DETECTED, {"url": page_url, "http_status": http_status})
        except Exception:  # nosec B110
            pass

    # Force cache eviction for error pages too
    if page_state is not None and page_state.state == PageFailureState.ERROR_PAGE:
        metadata["_force_cache_evict"] = True

    # Emit page state telemetry
    if page_state is not None:
        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import DIAGNOSTIC_PAGE_STATE

            emit(
                DIAGNOSTIC_PAGE_STATE,
                {
                    "state": page_state.state.value,
                    "confidence": page_state.confidence,
                    "url": page_url,
                },
            )
        except Exception:  # nosec B110
            pass

    # Emit antibot telemetry
    if antibot is not None:
        try:
            from pagemap.telemetry import emit
            from pagemap.telemetry.events import DIAGNOSTIC_ANTIBOT

            emit(
                DIAGNOSTIC_ANTIBOT,
                {
                    "provider": antibot.provider.value,
                    "confidence": antibot.confidence,
                    "challenge_visible": antibot.challenge_visible,
                    "url": page_url,
                },
            )
        except Exception:  # nosec B110
            pass

    result = DiagnosticResult(
        page_state=page_state,
        antibot=antibot,
        spa_status=spa_status,
        pruning_confidence=pruning_conf,
        suggested_actions=tuple(suggested),
    )

    return result if (page_state or antibot or spa_status or pruning_conf) else None
