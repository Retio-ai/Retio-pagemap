# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for PipelineTimer (Phase D)."""

from __future__ import annotations

from pagemap.pipeline_timer import PipelineTimer


class TestPipelineTimer:
    def test_stage_tracking(self):
        timer = PipelineTimer()
        timer.stage("navigation")
        timer.stage("detection")
        timer.stage("pruning")
        timer.finalize()

        stages = timer.elapsed_per_stage()
        assert list(stages.keys()) == ["navigation", "detection", "pruning"]
        assert all(isinstance(v, float) for v in stages.values())

    def test_current_stage(self):
        timer = PipelineTimer()
        assert timer.current_stage is None

        timer.stage("navigation")
        assert timer.current_stage == "navigation"

        timer.stage("detection")
        assert timer.current_stage == "detection"

        timer.finalize()
        assert timer.current_stage is None

    def test_timeout_report_structure(self):
        timer = PipelineTimer()
        timer.stage("navigation")
        timer.stage("detection")  # navigation complete, detection starts

        report = timer.timeout_report()
        assert report["error"] == "timeout"
        assert report["timed_out_at"] == "detection"
        assert len(report["completed_stages"]) == 1
        assert report["completed_stages"][0]["stage"] == "navigation"
        assert isinstance(report["total_ms"], float)
        assert "hint" in report

    def test_timeout_report_no_stages(self):
        timer = PipelineTimer()
        report = timer.timeout_report()
        assert report["timed_out_at"] == "unknown"
        assert report["completed_stages"] == []

    def test_hint_for_known_stages(self):
        assert "slow to load" in PipelineTimer.hint_for_stage("navigation")
        assert "complex" in PipelineTimer.hint_for_stage("detection")
        assert "large HTML" in PipelineTimer.hint_for_stage("pruning")

    def test_hint_for_unknown_stage(self):
        hint = PipelineTimer.hint_for_stage("custom_stage")
        assert "custom_stage" in hint

    def test_success_metadata(self):
        timer = PipelineTimer()
        timer.stage("a")
        timer.stage("b")
        timer.finalize()
        meta = timer.success_metadata()
        assert "a" in meta
        assert "b" in meta

    def test_elapsed_includes_current_stage(self):
        timer = PipelineTimer()
        timer.stage("running")
        # Don't finalize — should still show up
        stages = timer.elapsed_per_stage()
        assert "running" in stages
        assert stages["running"] >= 0

    def test_finalize_idempotent(self):
        timer = PipelineTimer()
        timer.stage("a")
        timer.finalize()
        timer.finalize()  # second call should be no-op
        stages = timer.elapsed_per_stage()
        assert len(stages) == 1
