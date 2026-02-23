# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.telemetry.collector — TelemetryCollector, OTLP envelope, Meta."""

from __future__ import annotations

import json
import threading

from pagemap.telemetry.collector import (
    TelemetryCollector,
    TelemetryConfig,
    TelemetryMeta,
    _payload_to_otlp_attributes,
    wrap_otlp,
)
from pagemap.telemetry.writer import ListWriter

# ── OTLP Envelope ────────────────────────────────────────────────


class TestOtlpEnvelope:
    def test_basic_structure(self):
        envelope = wrap_otlp("pagemap.test.event", {"tier": "C", "count": 42})

        assert "resourceLogs" in envelope
        rl = envelope["resourceLogs"]
        assert len(rl) == 1

        resource = rl[0]["resource"]
        assert "attributes" in resource

        scope_logs = rl[0]["scopeLogs"]
        assert len(scope_logs) == 1
        assert scope_logs[0]["scope"]["name"] == "pagemap.telemetry"

        log_records = scope_logs[0]["logRecords"]
        assert len(log_records) == 1

    def test_log_record_fields(self):
        envelope = wrap_otlp("pagemap.pipeline.completed", {"tier": "C"}, trace_id="abc123")

        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["body"]["stringValue"] == "pagemap.pipeline.completed"
        assert record["severityNumber"] == 9
        assert record["severityText"] == "INFO"
        assert "timeUnixNano" in record
        assert isinstance(record["timeUnixNano"], str)
        # timeUnixNano should be a large integer as string
        int(record["timeUnixNano"])  # Should not raise

    def test_trace_id_padding(self):
        envelope = wrap_otlp("test", {}, trace_id="abc123")
        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["traceId"] == "abc123" + "0" * 26  # padded to 32
        assert len(record["traceId"]) == 32

    def test_trace_id_empty(self):
        envelope = wrap_otlp("test", {})
        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["traceId"] == "0" * 32

    def test_span_id_derived(self):
        envelope = wrap_otlp("test", {}, trace_id="abcdef1234567890")
        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert len(record["spanId"]) == 16
        assert record["spanId"] == "abcdef1234567890"

    def test_attributes_conversion(self):
        envelope = wrap_otlp(
            "test",
            {
                "tier": "C",
                "count": 42,
                "ratio": 0.95,
                "active": True,
            },
        )
        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        attrs = {a["key"]: a["value"] for a in record["attributes"]}

        assert attrs["tier"] == {"stringValue": "C"}
        assert attrs["count"] == {"intValue": "42"}
        assert attrs["ratio"] == {"doubleValue": 0.95}
        assert attrs["active"] == {"boolValue": True}

    def test_resource_attributes_present(self):
        envelope = wrap_otlp("test", {})
        resource_attrs = envelope["resourceLogs"][0]["resource"]["attributes"]
        keys = {a["key"] for a in resource_attrs}
        assert "service.name" in keys
        assert "service.version" in keys
        assert "os.type" in keys
        assert "installation.id" in keys

    def test_json_serializable(self):
        envelope = wrap_otlp("pagemap.test", {"key": "value", "nested": {"a": 1}})
        serialized = json.dumps(envelope)
        assert isinstance(serialized, str)

    def test_custom_timestamp(self):
        ts = 1_700_000_000_000_000_000  # fixed timestamp
        envelope = wrap_otlp("test", {}, timestamp_ns=ts)
        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["timeUnixNano"] == str(ts)

    def test_dict_payload_as_json_string(self):
        envelope = wrap_otlp("test", {"details": {"a": 1, "b": 2}})
        record = envelope["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        attrs = {a["key"]: a["value"] for a in record["attributes"]}
        assert "stringValue" in attrs["details"]  # dict → JSON string


# ── payload_to_otlp_attributes ───────────────────────────────────


class TestPayloadToOtlpAttributes:
    def test_string_value(self):
        result = _payload_to_otlp_attributes({"key": "val"})
        assert result == [{"key": "key", "value": {"stringValue": "val"}}]

    def test_int_value(self):
        result = _payload_to_otlp_attributes({"count": 42})
        assert result == [{"key": "count", "value": {"intValue": "42"}}]

    def test_float_value(self):
        result = _payload_to_otlp_attributes({"ratio": 3.14})
        assert result == [{"key": "ratio", "value": {"doubleValue": 3.14}}]

    def test_bool_value(self):
        result = _payload_to_otlp_attributes({"flag": True})
        assert result == [{"key": "flag", "value": {"boolValue": True}}]

    def test_list_as_json_string(self):
        result = _payload_to_otlp_attributes({"items": [1, 2, 3]})
        assert result[0]["value"]["stringValue"] == "[1, 2, 3]"


# ── TelemetryMeta ────────────────────────────────────────────────


class TestTelemetryMeta:
    def test_initial_values(self):
        meta = TelemetryMeta()
        assert meta.emitted == 0
        assert meta.dropped == 0
        assert meta.exported == 0

    def test_snapshot(self):
        meta = TelemetryMeta()
        meta.emitted = 10
        meta.dropped = 2
        meta.exported = 8
        snap = meta.snapshot()
        assert snap == {"emitted": 10, "dropped": 2, "exported": 8}


# ── TelemetryCollector ───────────────────────────────────────────


class TestTelemetryCollector:
    def test_emit_disabled_is_noop(self):
        """Collector with NullWriter-equivalent should not crash."""
        config = TelemetryConfig(enabled=False)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)
        collector.emit("test.event", {"key": "val"})
        collector.flush_sync()
        assert len(writer.events) == 1  # Events still collected when collector exists

    def test_emit_and_flush(self):
        config = TelemetryConfig(enabled=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.emit("pagemap.test.event", {"tier": "C"})
        collector.emit("pagemap.test.event2", {"count": 5})
        collector.flush_sync()

        assert len(writer.events) == 2
        # Verify OTLP structure
        for event in writer.events:
            assert "resourceLogs" in event

    def test_emit_never_raises(self):
        """Even with a broken writer, emit should not raise."""
        config = TelemetryConfig(enabled=True)

        class BrokenWriter:
            def write_sync(self, batch):
                raise RuntimeError("boom")

        collector = TelemetryCollector(config, writer=BrokenWriter())
        collector.emit("test", {"a": 1})
        collector.flush_sync()  # Should not raise

    def test_queue_overflow_drops(self):
        config = TelemetryConfig(enabled=True, max_queue_size=3)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        for i in range(10):
            collector.emit("test", {"n": i})

        assert collector.meta.emitted == 3
        assert collector.meta.dropped == 7

        collector.flush_sync()
        assert len(writer.events) == 3

    def test_meta_counters(self):
        config = TelemetryConfig(enabled=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.emit("test1", {})
        collector.emit("test2", {})
        assert collector.meta.emitted == 2

        collector.flush_sync()
        assert collector.meta.exported == 2

    def test_thread_safety(self):
        """10 threads × 100 events should all be collected without errors."""
        config = TelemetryConfig(enabled=True, max_queue_size=10_000)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        errors = []

        def emit_events():
            try:
                for i in range(100):
                    collector.emit("thread.test", {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emit_events) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert collector.meta.emitted == 1000
        collector.flush_sync()
        assert len(writer.events) == 1000

    def test_shutdown_flushes_remaining(self):
        config = TelemetryConfig(enabled=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.emit("test", {"n": 1})
        collector.emit("test", {"n": 2})
        collector.shutdown()

        assert len(writer.events) == 2

    def test_shutdown_prevents_further_emit(self):
        config = TelemetryConfig(enabled=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.shutdown()
        collector.emit("test", {"n": 1})
        collector.flush_sync()

        assert len(writer.events) == 0

    def test_sanitizes_url_in_payload(self):
        config = TelemetryConfig(enabled=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.emit("test", {"url": "https://example.com/page?secret=abc#top"})
        collector.flush_sync()

        # URL should have query/fragment removed
        event = writer.events[0]
        record = event["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        url_attr = None
        for attr in record["attributes"]:
            if attr["key"] == "url":
                url_attr = attr["value"]["stringValue"]
        assert url_attr is not None
        assert "secret" not in url_attr
        assert "#top" not in url_attr

    def test_sanitizes_blocked_content_fields(self):
        config = TelemetryConfig(enabled=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.emit("test", {"tier": "C", "pruned_html": "<div>secret</div>"})
        collector.flush_sync()

        event = writer.events[0]
        record = event["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        keys = {a["key"] for a in record["attributes"]}
        assert "tier" in keys
        assert "pruned_html" not in keys

    def test_hash_url_paths_config(self):
        config = TelemetryConfig(enabled=True, hash_url_paths=True)
        writer = ListWriter()
        collector = TelemetryCollector(config, writer=writer)

        collector.emit("test", {"url": "https://example.com/user/john"})
        collector.flush_sync()

        event = writer.events[0]
        record = event["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        url_attr = None
        for attr in record["attributes"]:
            if attr["key"] == "url":
                url_attr = attr["value"]["stringValue"]
        assert url_attr is not None
        assert "john" not in url_attr  # Path should be hashed
        assert "example.com" in url_attr  # Domain preserved


# ── Module-level API ─────────────────────────────────────────────


class TestModuleApi:
    def test_emit_noop_when_not_configured(self):
        """emit() should be a no-op when telemetry isn't configured."""
        import pagemap.telemetry as telem

        old_collector = telem._collector
        try:
            telem._collector = None
            # Should not raise
            telem.emit("test", {"key": "value"})
        finally:
            telem._collector = old_collector

    def test_configure_and_emit(self):
        import pagemap.telemetry as telem

        telem._reset_for_testing()
        try:
            config = TelemetryConfig(enabled=True)
            collector = telem.configure(config)
            assert collector is not None
            assert telem._collector is collector

            # emit should work
            telem.emit("test.configure", {"ok": True})
        finally:
            telem._reset_for_testing()

    def test_configure_idempotent(self):
        import pagemap.telemetry as telem

        telem._reset_for_testing()
        try:
            config = TelemetryConfig(enabled=True)
            c1 = telem.configure(config)
            c2 = telem.configure(config)
            assert c1 is c2
        finally:
            telem._reset_for_testing()

    def test_reset_for_testing(self):
        import pagemap.telemetry as telem

        telem._reset_for_testing()
        assert telem._collector is None

        config = TelemetryConfig(enabled=True)
        telem.configure(config)
        assert telem._collector is not None

        telem._reset_for_testing()
        assert telem._collector is None
