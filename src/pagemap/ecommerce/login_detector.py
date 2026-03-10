"""Backward-compat shim — import from pagemap.core.ecommerce.login_detector instead."""

from pagemap.core.ecommerce.login_detector import (  # noqa: F401
    AgeGateInfo,
    LoginFormInfo,
    detect_age_gate,
    detect_age_gate_extended,
    detect_login_wall,
    detect_region_block,
)

__all__ = [
    "AgeGateInfo",
    "LoginFormInfo",
    "detect_age_gate",
    "detect_age_gate_extended",
    "detect_login_wall",
    "detect_region_block",
]
