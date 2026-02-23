# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.telemetry.writer — FileWriter, NullWriter, ListWriter."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

from pagemap.telemetry.writer import FileWriter, ListWriter, NullWriter


def _make_envelope(event: str = "test.event", n: int = 0) -> dict:
    """Create a minimal OTLP-like envelope for testing."""
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": []},
                "scopeLogs": [
                    {
                        "scope": {"name": "test"},
                        "logRecords": [
                            {
                                "body": {"stringValue": event},
                                "attributes": [{"key": "n", "value": {"intValue": str(n)}}],
                            }
                        ],
                    }
                ],
            }
        ]
    }


class TestFileWriter:
    def test_creates_jsonl_file(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path))
        writer = FileWriter(config)
        writer.write_sync([_make_envelope()])

        files = list(tmp_path.glob("events-*.jsonl"))
        assert len(files) == 1

    def test_writes_valid_jsonl(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path))
        writer = FileWriter(config)
        envelopes = [_make_envelope(n=i) for i in range(5)]
        writer.write_sync(envelopes)

        files = list(tmp_path.glob("events-*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 5
        for line in lines:
            parsed = json.loads(line)
            assert "resourceLogs" in parsed

    def test_file_naming_convention(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path))
        writer = FileWriter(config)
        writer.write_sync([_make_envelope()])

        files = list(tmp_path.glob("events-*.jsonl"))
        name = files[0].name
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert name.startswith(f"events-{today}-")
        assert name.endswith(".jsonl")

    def test_size_rotation(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path), max_file_size_mb=0)  # 0 MB = rotate immediately
        writer = FileWriter(config)

        # First write creates file
        writer.write_sync([_make_envelope(n=1)])
        # Second write should rotate (file size > 0)
        writer.write_sync([_make_envelope(n=2)])

        files = sorted(tmp_path.glob("events-*.jsonl"))
        # After rotation we should have 2 files
        assert len(files) >= 2

    def test_retention_cleanup(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path), max_retention_days=0)
        writer = FileWriter(config)

        # Create a fake old file
        old_file = tmp_path / "events-2020-01-01-001.jsonl"
        old_file.write_text('{"old": true}\n')
        # Set mtime to the past
        import os

        os.utime(old_file, (0, 0))

        # Write new event (triggers retention cleanup)
        writer.write_sync([_make_envelope()])

        # Old file should be cleaned up
        assert not old_file.exists()

    def test_total_size_cap(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path), max_total_size_mb=0)
        writer = FileWriter(config)

        # Create a file that exceeds total size cap
        big_file = tmp_path / "events-2025-01-01-001.jsonl"
        big_file.write_text("x" * 1000)

        # Write triggers enforcement
        writer.write_sync([_make_envelope()])

        # Big file should be removed
        assert not big_file.exists()

    def test_gzip_compression_previous_day(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path))
        writer = FileWriter(config)

        # Create a fake "yesterday" file
        yesterday_file = tmp_path / "events-2020-01-01-001.jsonl"
        yesterday_file.write_text('{"test": true}\n')

        # Write today triggers compression of previous day files
        writer.write_sync([_make_envelope()])

        gz_files = list(tmp_path.glob("events-2020-01-01-*.jsonl.gz"))
        assert len(gz_files) == 1
        assert not yesterday_file.exists()

        # Verify gzip content is valid
        with gzip.open(gz_files[0], "rt") as f:
            content = f.read()
            assert '{"test": true}' in content

    def test_empty_batch_noop(self, tmp_path):
        config = _FakeConfig(export_path=str(tmp_path))
        writer = FileWriter(config)
        writer.write_sync([])

        files = list(tmp_path.glob("events-*"))
        assert len(files) == 0

    def test_creates_export_directory(self, tmp_path):
        export_dir = tmp_path / "sub" / "telemetry"
        config = _FakeConfig(export_path=str(export_dir))
        writer = FileWriter(config)
        writer.write_sync([_make_envelope()])

        assert export_dir.exists()


class TestNullWriter:
    def test_noop(self):
        writer = NullWriter()
        writer.write_sync([_make_envelope()])  # Should not raise


class TestListWriter:
    def test_captures_events(self):
        writer = ListWriter()
        envelopes = [_make_envelope(n=i) for i in range(3)]
        writer.write_sync(envelopes)

        assert len(writer.events) == 3
        assert (
            writer.events[0]["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"] == "test.event"
        )

    def test_accumulates_across_batches(self):
        writer = ListWriter()
        writer.write_sync([_make_envelope(n=1)])
        writer.write_sync([_make_envelope(n=2), _make_envelope(n=3)])

        assert len(writer.events) == 3


class _FakeConfig:
    """Minimal config for FileWriter testing."""

    def __init__(
        self,
        export_path: str = "/tmp/test",
        max_file_size_mb: int = 50,
        max_retention_days: int = 7,
        max_total_size_mb: int = 500,
    ):
        self.export_path = export_path
        self.max_file_size_mb = max_file_size_mb
        self.max_retention_days = max_retention_days
        self.max_total_size_mb = max_total_size_mb
