# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: SPA framework detection and hydration status.

Parses SPA signals collected from the extended _DOM_FINGERPRINT_JS
(batched into a single page.evaluate() call).
Does NOT perform its own page.evaluate() — receives signals from
dom_change_detector.
"""

from __future__ import annotations

from . import SpaFramework, SpaStatus

# Framework detection priority (order matters — more specific first)
_FRAMEWORK_KEYS: tuple[tuple[str, SpaFramework], ...] = (
    ("nextjs", SpaFramework.NEXTJS),
    ("nuxt", SpaFramework.NUXT),
    ("react", SpaFramework.REACT),
    ("vue", SpaFramework.VUE),
    ("angular", SpaFramework.ANGULAR),
    ("svelte", SpaFramework.SVELTE),
)


def parse_spa_signals(signals: dict | None) -> SpaStatus | None:
    """Convert JS-side SPA signals to SpaStatus. Never raises.

    Args:
        signals: Dict from _DOM_FINGERPRINT_JS spaSignals block, e.g.:
            {"react": True, "nextjs": False, "vue": False, ...
             "skeletonCount": 0, "contentLength": 5432}

    Returns:
        SpaStatus or None if no SPA signals or no signals dict.
    """
    try:
        return _parse_impl(signals)
    except Exception:
        return None


def _parse_impl(signals: dict | None) -> SpaStatus | None:
    if signals is None:
        return None

    # Detect framework
    detected_framework: SpaFramework | None = None
    detection_signals: list[str] = []

    for key, framework in _FRAMEWORK_KEYS:
        if signals.get(key):
            detected_framework = framework
            detection_signals.append(f"framework={key}")
            break

    if detected_framework is None:
        return None  # No SPA detected

    # Check hydration status
    skeleton_count = signals.get("skeletonCount", 0)
    content_length = signals.get("contentLength", 0)
    has_skeleton = skeleton_count > 0

    # Hydration heuristic: skeleton present + very short content = not hydrated
    hydrated = True
    if has_skeleton and content_length < 100:
        hydrated = False
        detection_signals.append(f"skeleton_count={skeleton_count}")
        detection_signals.append(f"content_length={content_length}")
    elif has_skeleton:
        detection_signals.append(f"skeleton_count={skeleton_count}")

    # Confidence based on detection quality
    confidence = 0.85 if detected_framework in (SpaFramework.NEXTJS, SpaFramework.NUXT) else 0.75

    return SpaStatus(
        framework=detected_framework,
        hydrated=hydrated,
        has_skeleton=has_skeleton,
        confidence=confidence,
        signals=tuple(detection_signals),
    )
