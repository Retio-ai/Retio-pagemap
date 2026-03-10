# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for run_page_diagnostics router — detector exception resilience."""

from __future__ import annotations

from unittest.mock import patch

from pagemap.diagnostics import run_page_diagnostics


def _base_kwargs(**overrides):
    defaults = {
        "raw_html": "<html><body>Normal page</body></html>",
        "html_lower": "<html><body>normal page</body></html>",
        "page_url": "https://example.com",
        "page_type": "unknown",
        "interactables": [],
        "warnings": [],
        "metadata": {},
    }
    defaults.update(overrides)
    return defaults


class TestRunPageDiagnosticsResilience:
    def test_returns_none_for_healthy_page(self):
        result = run_page_diagnostics(**_base_kwargs())
        assert result is None

    @patch("pagemap.core.diagnostics.page_state_detector.detect_page_state", side_effect=RuntimeError("boom"))
    def test_continues_when_page_state_raises(self, _mock):
        result = run_page_diagnostics(**_base_kwargs())
        # Should not propagate exception
        assert result is None or result.page_state is None

    @patch("pagemap.core.diagnostics.antibot_detector.detect_antibot", side_effect=RuntimeError("boom"))
    def test_continues_when_antibot_raises(self, _mock):
        result = run_page_diagnostics(**_base_kwargs())
        assert result is None or result.antibot is None

    def test_blocked_page_adds_warning(self):
        warnings: list[str] = []
        metadata: dict = {}
        result = run_page_diagnostics(
            **_base_kwargs(
                raw_html="<html><body>Access Denied - Please verify you are human</body></html>",
                html_lower="<html><body>access denied - please verify you are human</body></html>",
                page_type="blocked",
                warnings=warnings,
                metadata=metadata,
            )
        )
        assert result is not None
        assert result.page_state is not None
        assert any("anti-bot" in w for w in warnings)
        assert metadata.get("_force_cache_evict") is True
