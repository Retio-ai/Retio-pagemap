"""Backward-compat shim — import from pagemap.core.pipeline_timer instead."""

from pagemap.core.pipeline_timer import PipelineTimer, StageRecord  # noqa: F401

__all__ = ["PipelineTimer", "StageRecord"]
