# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Telemetry writers: FileWriter (JSONL + gzip rotation), NullWriter, ListWriter."""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

_MAX_SEQ = 999  # Upper bound for sequence numbers to prevent runaway loops


class Writer(Protocol):
    """Writer protocol for telemetry output."""

    def write_sync(self, batch: list[dict]) -> None: ...


class FileWriter:
    """Write telemetry events as JSONL with date-based rotation and gzip compression.

    File naming: events-YYYY-MM-DD-NNN.jsonl (NNN = 3-digit sequence).
    Previous day's .jsonl files are compressed to .jsonl.gz on flush.
    Retention: configurable days + total size cap.
    """

    def __init__(self, config: object) -> None:
        # Duck-type config to avoid circular import of TelemetryConfig
        self._export_path = Path(getattr(config, "export_path", ""))
        self._max_file_size = getattr(config, "max_file_size_mb", 50) * 1024 * 1024
        self._max_retention_days = getattr(config, "max_retention_days", 7)
        self._max_total_size = getattr(config, "max_total_size_mb", 500) * 1024 * 1024
        self._current_file: Path | None = None
        self._current_date: str = ""
        self._current_seq: int = 0
        self._last_compressed_date: str = ""  # Skip redundant compression checks

    def write_sync(self, batch: list[dict]) -> None:
        """Write a batch of OTLP envelopes as JSONL lines."""
        if not batch:
            return

        try:
            self._export_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            return  # Can't write if dir creation fails

        today = datetime.now(UTC).strftime("%Y-%m-%d")

        # Rotate file if needed
        if self._current_file is None or self._current_date != today:
            self._current_date = today
            self._current_seq = self._find_next_seq(today)
            self._current_file = self._make_path(today, self._current_seq)

        # Check size rotation
        if self._current_file.exists() and self._current_file.stat().st_size >= self._max_file_size:
            self._current_seq += 1
            self._current_file = self._make_path(today, self._current_seq)

        # Write batch
        try:
            with open(self._current_file, "a", encoding="utf-8") as f:
                for envelope in batch:
                    f.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
                    f.write("\n")
        except Exception:
            return  # Best-effort

        # Compress previous day's files (only once per date change) + enforce retention
        try:
            if self._last_compressed_date != today:
                self._compress_previous_day(today)
                self._last_compressed_date = today
            self._enforce_retention()
        except Exception:  # nosec B110
            pass  # Best-effort housekeeping

    def _make_path(self, date: str, seq: int) -> Path:
        return self._export_path / f"events-{date}-{seq:03d}.jsonl"

    def _find_next_seq(self, date: str) -> int:
        """Find the next available sequence number for a given date."""
        seq = 0
        while seq < _MAX_SEQ:
            path = self._make_path(date, seq + 1)
            if not path.exists():
                # Use the last existing file if it's under size limit
                candidate = self._make_path(date, seq) if seq > 0 else self._make_path(date, 1)
                if seq == 0:
                    return 1
                if candidate.exists() and candidate.stat().st_size < self._max_file_size:
                    return seq
                return seq + 1
            seq += 1
        return _MAX_SEQ

    def _compress_previous_day(self, today: str) -> None:
        """Compress any .jsonl files from previous days to .jsonl.gz."""
        for jsonl_file in self._export_path.glob("events-*.jsonl"):
            # Extract date from filename: events-YYYY-MM-DD-NNN.jsonl
            name = jsonl_file.stem  # events-YYYY-MM-DD-NNN
            parts = name.split("-")
            if len(parts) >= 5:  # events, YYYY, MM, DD, NNN
                file_date = f"{parts[1]}-{parts[2]}-{parts[3]}"
                if file_date < today:
                    gz_path = jsonl_file.with_suffix(".jsonl.gz")
                    try:
                        with open(jsonl_file, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                            f_out.write(f_in.read())
                        jsonl_file.unlink()
                    except Exception:  # nosec B110
                        pass  # Skip files that fail

    def _enforce_retention(self) -> None:
        """Remove files older than retention period and enforce total size cap."""
        now = time.time()
        max_age = self._max_retention_days * 86400

        all_files = sorted(self._export_path.glob("events-*"), key=lambda p: p.name)

        # Remove expired files
        remaining = []
        for f in all_files:
            try:
                age = now - f.stat().st_mtime
                if age > max_age:
                    f.unlink()
                else:
                    remaining.append(f)
            except Exception:
                remaining.append(f)

        # Enforce total size cap (remove oldest first)
        total_size = 0
        for f in remaining:
            with contextlib.suppress(Exception):
                total_size += f.stat().st_size
        while total_size > self._max_total_size and remaining:
            oldest = remaining.pop(0)
            try:
                total_size -= oldest.stat().st_size
                oldest.unlink()
            except Exception:  # nosec B110
                pass


class NullWriter:
    """No-op writer for disabled telemetry."""

    def write_sync(self, batch: list[dict]) -> None:
        pass


class ListWriter:
    """In-memory writer for testing. Captures all written events."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def write_sync(self, batch: list[dict]) -> None:
        self.events.extend(batch)
