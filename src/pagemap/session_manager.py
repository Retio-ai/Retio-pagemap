"""Backward-compat shim — import from pagemap.server.session_manager instead."""

from pagemap.server.session_manager import (  # noqa: F401
    DEFAULT_SESSION_TTL,
    MAX_NAVIGATIONS,
    MAX_SESSION_AGE,
    MAX_SESSIONS_PER_TENANT,
    MAX_TABS_PER_SESSION,
    STDIO_SESSION_ID,
    HttpSessionManager,
    SessionEntry,
    SessionManagerProtocol,
    SessionNotFoundError,
    StdioSessionManager,
)

__all__ = [
    "DEFAULT_SESSION_TTL",
    "MAX_NAVIGATIONS",
    "MAX_SESSION_AGE",
    "MAX_SESSIONS_PER_TENANT",
    "MAX_TABS_PER_SESSION",
    "STDIO_SESSION_ID",
    "HttpSessionManager",
    "SessionEntry",
    "SessionManagerProtocol",
    "SessionNotFoundError",
    "StdioSessionManager",
]
