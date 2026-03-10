"""Backward-compat shim — import from pagemap.server.robots_checker instead."""

from pagemap.server.robots_checker import (  # noqa: F401
    _DEFAULT_TTL,
    _ERROR_TTL,
    _ROBOTS_FETCH_TIMEOUT,
    ROBOT_USER_AGENT,
    RobotsChecker,
    _CacheEntry,
)

__all__ = [
    "ROBOT_USER_AGENT",
    "RobotsChecker",
    "_CacheEntry",
    "_DEFAULT_TTL",
    "_ERROR_TTL",
    "_ROBOTS_FETCH_TIMEOUT",
]
