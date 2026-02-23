# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Telemetry module — fire-and-forget event collection.

Usage:
    from pagemap.telemetry import emit

    emit("pagemap.pipeline.completed", {"tier": "C", "interactables": 12})

Disabled by default. Enable via ``configure()`` or ``--telemetry`` CLI flag.
"""

from __future__ import annotations

import atexit
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .collector import TelemetryCollector, TelemetryConfig

_collector: TelemetryCollector | None = None


def configure(config: TelemetryConfig) -> TelemetryCollector:
    """Initialize the telemetry singleton.

    Registers atexit handler for graceful shutdown.
    Idempotent — subsequent calls return the existing collector.

    Note: SIGTERM is NOT handled here to avoid overwrite conflicts with
    the server's own SIGTERM handler.  The server's _sync_cleanup calls
    telemetry.shutdown() explicitly.
    """
    global _collector
    if _collector is not None:
        return _collector

    from .collector import TelemetryCollector

    _collector = TelemetryCollector(config)

    atexit.register(shutdown)

    return _collector


def emit(event_type: str, payload: dict, *, trace_id: str = "") -> None:
    """Emit a telemetry event. No-op if telemetry is not configured.

    Never raises — all exceptions are suppressed (fire-and-forget).
    """
    try:
        if _collector is not None:
            _collector.emit(event_type, payload, trace_id=trace_id)
    except Exception:  # nosec B110
        pass


def shutdown() -> None:
    """Flush remaining events and shut down the collector."""
    try:
        if _collector is not None:
            _collector.shutdown()
    except Exception:  # nosec B110
        pass


def _reset_for_testing() -> None:
    """Reset module state for test isolation."""
    global _collector
    if _collector is not None:
        with contextlib.suppress(Exception):
            _collector.shutdown()
    _collector = None

    # Also reset cached resource attributes
    from .collector import _reset_resource_attrs

    _reset_resource_attrs()
