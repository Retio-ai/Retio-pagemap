# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Progress indicators for CLI output.

Uses ``rich`` for interactive terminals when available,
falls back to simple stderr prints when ``rich`` is not installed
or output is piped.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Generator

try:
    from rich.console import Console

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


@contextlib.contextmanager
def status_spinner(msg: str) -> Generator[None, None, None]:
    """Context manager showing a spinner with *msg* while active.

    Silent when stderr is not a TTY (piped output).
    Falls back to a simple print when ``rich`` is unavailable.
    """
    if not sys.stderr.isatty():
        yield
        return

    if _HAS_RICH:
        console = Console(stderr=True)
        with console.status(msg):
            yield
    else:
        print(msg, file=sys.stderr)
        yield


def print_step(msg: str) -> None:
    """Print a step message to stderr (only when interactive)."""
    if sys.stderr.isatty():
        print(msg, file=sys.stderr)
