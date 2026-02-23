# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Telemetry collector with OTLP LogsData envelope, config, and meta tracking."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import queue
import time
from dataclasses import dataclass, field

from .privacy import get_installation_id, sanitize_payload, sanitize_url

logger = logging.getLogger(__name__)

# ── Version — read once at import ────────────────────────────────
try:
    from importlib.metadata import version as _pkg_version

    _PAGEMAP_VERSION = _pkg_version("retio-pagemap")
except Exception:
    _PAGEMAP_VERSION = "unknown"


# ── Config ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TelemetryConfig:
    """Immutable telemetry configuration."""

    enabled: bool = False
    export_path: str = field(default_factory=lambda: os.path.join(os.path.expanduser("~"), ".pagemap", "telemetry"))
    flush_interval_s: float = 30.0
    max_queue_size: int = 10_000
    max_file_size_mb: int = 50
    max_retention_days: int = 7
    max_total_size_mb: int = 500
    hash_url_paths: bool = False


# ── Meta (internal counters) ─────────────────────────────────────


class TelemetryMeta:
    """Approximate telemetry counters.

    Note: plain int increments are safe under CPython's GIL but are NOT
    atomic under free-threaded Python (3.13t+).  These counters are
    best-effort diagnostics, not accounting-grade — slight inaccuracy
    under extreme concurrency is acceptable.
    """

    __slots__ = ("emitted", "dropped", "exported")

    def __init__(self) -> None:
        self.emitted: int = 0
        self.dropped: int = 0
        self.exported: int = 0

    def snapshot(self) -> dict:
        return {"emitted": self.emitted, "dropped": self.dropped, "exported": self.exported}


# ── OTLP helpers ─────────────────────────────────────────────────

_RESOURCE_ATTRS: list[dict] | None = None


def _build_resource_attrs() -> list[dict]:
    """Build OTLP resource attributes (cached after first call).

    Returns a shallow copy so callers cannot mutate the cached template.
    """
    global _RESOURCE_ATTRS
    if _RESOURCE_ATTRS is None:
        _RESOURCE_ATTRS = [
            {"key": "service.name", "value": {"stringValue": "pagemap"}},
            {"key": "service.version", "value": {"stringValue": _PAGEMAP_VERSION}},
            {"key": "os.type", "value": {"stringValue": platform.system().lower()}},
            {"key": "installation.id", "value": {"stringValue": get_installation_id()}},
        ]
    return list(_RESOURCE_ATTRS)


def _reset_resource_attrs() -> None:
    """Reset cached resource attributes (for test isolation)."""
    global _RESOURCE_ATTRS
    _RESOURCE_ATTRS = None


def _to_otlp_attr_value(v: object) -> dict:
    """Convert a Python value to an OTLP attribute value."""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, dict):
        return {"stringValue": json.dumps(v, ensure_ascii=False)}
    if isinstance(v, (list, tuple)):
        return {"stringValue": json.dumps(v, ensure_ascii=False)}
    return {"stringValue": str(v)}


def _payload_to_otlp_attributes(payload: dict) -> list[dict]:
    """Convert a flat payload dict to OTLP attributes array."""
    return [{"key": k, "value": _to_otlp_attr_value(v)} for k, v in payload.items()]


def wrap_otlp(
    event_type: str,
    payload: dict,
    *,
    trace_id: str = "",
    timestamp_ns: int | None = None,
) -> dict:
    """Wrap an event into OTLP LogsData JSON format.

    Each call produces one complete resourceLogs envelope with a single logRecord.
    """
    if timestamp_ns is None:
        timestamp_ns = int(time.time() * 1_000_000_000)

    # trace_id: pad to 32 hex chars (OTLP requirement)
    otlp_trace_id = trace_id.ljust(32, "0")[:32] if trace_id else "0" * 32
    # span_id: derive 16 hex chars from trace_id
    otlp_span_id = otlp_trace_id[:16]

    log_record: dict = {
        "timeUnixNano": str(timestamp_ns),
        "severityNumber": 9,  # INFO
        "severityText": "INFO",
        "body": {"stringValue": event_type},
        "attributes": _payload_to_otlp_attributes(payload),
        "traceId": otlp_trace_id,
        "spanId": otlp_span_id,
    }

    return {
        "resourceLogs": [
            {
                "resource": {"attributes": _build_resource_attrs()},
                "scopeLogs": [
                    {
                        "scope": {"name": "pagemap.telemetry", "version": "1"},
                        "logRecords": [log_record],
                    }
                ],
            }
        ]
    }


# ── Collector ────────────────────────────────────────────────────


class TelemetryCollector:
    """Fire-and-forget telemetry collector with background flush.

    Thread-safe: emit() can be called from any thread (pruning workers).
    All exceptions are suppressed — telemetry never affects tool execution.
    """

    def __init__(self, config: TelemetryConfig, writer: object | None = None) -> None:
        self.config = config
        self.meta = TelemetryMeta()
        self._queue: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._flush_task: asyncio.Task | None = None
        self._flush_started = False
        self._shutdown = False

        # Lazy import to avoid circular deps at module level
        if writer is not None:
            self._writer = writer
        else:
            from .writer import FileWriter

            self._writer = FileWriter(config)

    def emit(self, event_type: str, payload: dict, *, trace_id: str = "") -> None:
        """Enqueue a telemetry event. Never raises."""
        try:
            if self._shutdown:
                return

            # Sanitize
            sanitized = sanitize_payload(payload)
            # Sanitize URL fields
            for key in ("url",):
                if key in sanitized and isinstance(sanitized[key], str):
                    sanitized[key] = sanitize_url(sanitized[key], hash_paths=self.config.hash_url_paths)

            envelope = wrap_otlp(event_type, sanitized, trace_id=trace_id)

            # Check queue size (approximate — qsize() is not exact under concurrency)
            if self._queue.qsize() >= self.config.max_queue_size:
                self.meta.dropped += 1
                return

            self._queue.put(envelope)
            self.meta.emitted += 1

            # Lazy-start periodic flush on first emit
            if not self._flush_started:
                self._start_periodic_flush()
        except Exception:  # nosec B110
            pass  # Fire-and-forget — never propagate

    def _start_periodic_flush(self) -> None:
        """Start background flush task (lazy, on first emit)."""
        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.create_task(self._periodic_flush_loop())
            self._flush_started = True
        except RuntimeError:
            # No running event loop (e.g., called from sync thread context)
            # flush_sync will handle these events at shutdown
            pass

    async def _periodic_flush_loop(self) -> None:
        """Background task that flushes every flush_interval_s."""
        try:
            while not self._shutdown:
                await asyncio.sleep(self.config.flush_interval_s)
                await self.flush_async()
        except asyncio.CancelledError:
            pass
        except Exception:  # nosec B110
            pass  # Never crash the flush loop

    def flush_sync(self) -> None:
        """Synchronous flush — drain queue and write batch. For atexit/SIGTERM."""
        try:
            batch: list[dict] = []
            while True:
                try:
                    item = self._queue.get_nowait()
                    batch.append(item)
                except queue.Empty:
                    break

            if batch:
                self._writer.write_sync(batch)  # type: ignore[union-attr]
                self.meta.exported += len(batch)
        except Exception:  # nosec B110
            pass  # Best-effort

    async def flush_async(self) -> None:
        """Non-blocking async flush — offloads I/O to a thread."""
        await asyncio.to_thread(self.flush_sync)

    def shutdown(self) -> None:
        """Final flush + cancel background task."""
        self._shutdown = True
        if self._flush_task is not None:
            self._flush_task.cancel()
        self.flush_sync()
