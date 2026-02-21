# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Pipeline stage timer for latency tracking and timeout diagnostics.

Created outside asyncio.wait_for so it survives cancellation and can
produce a meaningful timeout_report even when the pipeline is interrupted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class StageRecord:
    name: str
    start_ns: int
    end_ns: int = 0


class PipelineTimer:
    """Track pipeline stage transitions for latency reporting."""

    __slots__ = ("_stages", "_current", "_start_ns")

    def __init__(self) -> None:
        self._stages: list[StageRecord] = []
        self._current: StageRecord | None = None
        self._start_ns: int = time.monotonic_ns()

    def stage(self, name: str) -> None:
        """End previous stage + start new stage."""
        now = time.monotonic_ns()
        if self._current is not None:
            self._current.end_ns = now
            self._stages.append(self._current)
        self._current = StageRecord(name=name, start_ns=now)

    def finalize(self) -> None:
        """End current stage. Call on success or error."""
        if self._current is not None:
            self._current.end_ns = time.monotonic_ns()
            self._stages.append(self._current)
            self._current = None

    @property
    def current_stage(self) -> str | None:
        return self._current.name if self._current else None

    def elapsed_per_stage(self) -> dict[str, float]:
        """Return {stage_name: elapsed_ms} for all stages (including current)."""
        now = time.monotonic_ns()
        result: dict[str, float] = {}
        for s in self._stages:
            result[s.name] = round((s.end_ns - s.start_ns) / 1e6, 1)
        if self._current is not None:
            result[self._current.name] = round((now - self._current.start_ns) / 1e6, 1)
        return result

    def timeout_report(self) -> dict:
        """Structured diagnostic for timeout errors."""
        now = time.monotonic_ns()
        completed = [{"stage": s.name, "ms": round((s.end_ns - s.start_ns) / 1e6, 1)} for s in self._stages]
        current = self.current_stage or "unknown"
        current_ms = round((now - self._current.start_ns) / 1e6, 1) if self._current else 0
        return {
            "error": "timeout",
            "completed_stages": completed,
            "timed_out_at": current,
            "timed_out_stage_ms": current_ms,
            "total_ms": round((now - self._start_ns) / 1e6, 1),
            "hint": self.hint_for_stage(current),
        }

    @staticmethod
    def hint_for_stage(stage: str) -> str:
        hints = {
            "navigation": "Page may be slow to load or have long-polling connections.",
            "detection": "DOM is very complex. Try scrolling to a specific section first.",
            "pruning": "Page has very large HTML. Consider targeting a more specific URL.",
            "fingerprint": "DOM fingerprint capture is stalling.",
            "content_refresh": "Content-only rebuild is slow. Full rebuild may be needed.",
        }
        return hints.get(stage, f"Timed out during '{stage}' stage.")

    def success_metadata(self) -> dict[str, float]:
        """Return stage timing for successful builds (metadata enrichment)."""
        return self.elapsed_per_stage()
