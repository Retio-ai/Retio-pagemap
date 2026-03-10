"""Backward-compat shim — import from pagemap.core.diagnostics.antibot_detector instead."""

from pagemap.core.diagnostics.antibot_detector import (  # noqa: F401
    _stealth_recommendations,
    detect_antibot,
    update_session_state,
)

__all__ = ["detect_antibot", "update_session_state"]
