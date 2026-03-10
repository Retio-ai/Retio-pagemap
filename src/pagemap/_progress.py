"""Backward-compat shim — import from pagemap.core._progress instead."""

from pagemap.core._progress import print_step, status_spinner  # noqa: F401

__all__ = ["print_step", "status_spinner"]
