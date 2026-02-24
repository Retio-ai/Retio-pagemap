# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.logging_config — structlog + stdlib bridge."""

from __future__ import annotations

import logging
import sys

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_logging():
    """Ensure clean logging state before/after each test."""
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    yield
    root.handlers = old_handlers
    root.setLevel(old_level)
    # Reset structlog config
    structlog.reset_defaults()


class TestConsoleRenderer:
    """STDIO mode: ConsoleRenderer (human-readable)."""

    def test_configure_console_mode(self):
        from pagemap.logging_config import configure

        configure(json_output=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert handler.stream is sys.stderr

    def test_console_output_is_human_readable(self, capsys):
        from pagemap.logging_config import configure

        configure(json_output=False)
        test_logger = logging.getLogger("test.console")
        test_logger.info("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.err
        # Should NOT be valid JSON
        assert not captured.err.strip().startswith("{")

    def test_console_includes_log_level(self, capsys):
        from pagemap.logging_config import configure

        configure(json_output=False)
        test_logger = logging.getLogger("test.level")
        test_logger.warning("test warn")
        captured = capsys.readouterr()
        assert "warn" in captured.err.lower()


class TestJSONRenderer:
    """HTTP mode: JSONRenderer (machine-parseable)."""

    def test_configure_json_mode(self):
        from pagemap.logging_config import configure

        configure(json_output=True)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert handler.stream is sys.stderr

    def test_json_output_is_valid_json(self, capsys):
        import json

        from pagemap.logging_config import configure

        configure(json_output=True)
        test_logger = logging.getLogger("test.json")
        test_logger.info("json test")
        captured = capsys.readouterr()
        # Should be valid JSON
        parsed = json.loads(captured.err.strip())
        assert parsed["event"] == "json test"

    def test_json_includes_logger_name(self, capsys):
        import json

        from pagemap.logging_config import configure

        configure(json_output=True)
        test_logger = logging.getLogger("my.module")
        test_logger.info("name test")
        captured = capsys.readouterr()
        parsed = json.loads(captured.err.strip())
        assert parsed["logger"] == "my.module"

    def test_json_includes_timestamp(self, capsys):
        import json

        from pagemap.logging_config import configure

        configure(json_output=True)
        test_logger = logging.getLogger("test.ts")
        test_logger.info("ts test")
        captured = capsys.readouterr()
        parsed = json.loads(captured.err.strip())
        assert "timestamp" in parsed


class TestStdlibBridge:
    """stdlib logger calls pass through structlog processors."""

    def test_stdlib_logger_uses_structlog_formatter(self, capsys):
        import json

        from pagemap.logging_config import configure

        configure(json_output=True)
        # Use a stdlib logger directly
        stdlib_logger = logging.getLogger("stdlib.bridge.test")
        stdlib_logger.info("bridge msg")
        captured = capsys.readouterr()
        parsed = json.loads(captured.err.strip())
        assert parsed["event"] == "bridge msg"
        assert parsed["logger"] == "stdlib.bridge.test"


class TestContextVars:
    """Bound contextvars appear in output."""

    def test_contextvars_in_json_output(self, capsys):
        import json

        from pagemap.logging_config import configure

        configure(json_output=True)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id="req123", session_id="sess456")
        try:
            slog = structlog.get_logger("test.ctx")
            slog.info("ctx test")
            captured = capsys.readouterr()
            parsed = json.loads(captured.err.strip())
            assert parsed["request_id"] == "req123"
            assert parsed["session_id"] == "sess456"
        finally:
            structlog.contextvars.clear_contextvars()


class TestLogLevel:
    """Log level configuration."""

    def test_default_level_is_info(self):
        from pagemap.logging_config import configure

        configure()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_custom_level(self):
        from pagemap.logging_config import configure

        configure(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self):
        from pagemap.logging_config import configure

        configure(level="NONEXISTENT")
        root = logging.getLogger()
        assert root.level == logging.INFO


class TestMultipleConfigure:
    """Multiple configure() calls don't stack handlers."""

    def test_no_handler_stacking(self):
        from pagemap.logging_config import configure

        configure(json_output=False)
        configure(json_output=True)
        configure(json_output=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
