"""Backward-compat shim — import from pagemap.core.diagnostics.i18n_patterns instead."""

from pagemap.core.diagnostics.i18n_patterns import (  # noqa: F401
    AGE_VERIFICATION_RE,
    BOT_BLOCKED_RE,
    EMPTY_RESULTS_RE,
    ERROR_PAGE_RE,
    LOGIN_REQUIRED_RE,
    OUT_OF_STOCK_RE,
    REGION_RESTRICTED_RE,
)

__all__ = [
    "AGE_VERIFICATION_RE",
    "BOT_BLOCKED_RE",
    "EMPTY_RESULTS_RE",
    "ERROR_PAGE_RE",
    "LOGIN_REQUIRED_RE",
    "OUT_OF_STOCK_RE",
    "REGION_RESTRICTED_RE",
]
