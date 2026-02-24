# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Repository abstraction — protocol-based data access layer.

Defines ``RepositoryProtocol`` for database operations (API keys, audit
events, usage records) and ``InMemoryRepository`` for backward compatibility
with STDIO mode and tests.

Pattern follows ``SessionManagerProtocol`` in ``session_manager.py``:
runtime-checkable Protocol + concrete implementations.

Dependencies: api_key.py (KeyRecord, ApiKeyStore).
No server.py import (acyclic).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .api_key import ApiKeyStore, KeyRecord

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Immutable record for an audit log entry."""

    event_type: str
    timestamp: float = field(default_factory=time.time)
    key_hash: str = ""
    client_ip: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Immutable record for a single API usage entry."""

    key_hash: str
    tool: str
    cost: int
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RepositoryProtocol(Protocol):
    """Interface for persistent storage — in-memory, SQLite, or PostgreSQL."""

    async def store_key(self, record: KeyRecord) -> None: ...

    async def get_key(self, key_hash: str) -> KeyRecord | None: ...

    async def revoke_key(self, key_hash: str) -> bool: ...

    async def list_keys(self) -> list[KeyRecord]: ...

    async def log_event(self, event: AuditEvent) -> None: ...

    async def record_usage(self, record: UsageRecord) -> None: ...

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryRepository:
    """In-memory repository wrapping ``ApiKeyStore`` for backward compatibility.

    Suitable for STDIO mode and tests where persistence is not required.
    Audit events and usage records are stored in plain lists.
    """

    def __init__(self, *, max_keys: int = 1000) -> None:
        self._store = ApiKeyStore(max_keys=max_keys)
        self._audit_log: list[AuditEvent] = []
        self._usage_records: list[UsageRecord] = []

    async def store_key(self, record: KeyRecord) -> None:
        """Store a key record directly (bypasses key generation)."""
        self._store._keys[record.key_hash] = record

    async def get_key(self, key_hash: str) -> KeyRecord | None:
        """Look up a key record by its hash."""
        return self._store._keys.get(key_hash)

    async def revoke_key(self, key_hash: str) -> bool:
        """Revoke a key by its hash. Returns True if found and revoked."""
        return self._store.revoke(key_hash)

    async def list_keys(self) -> list[KeyRecord]:
        """Return all key records (including revoked)."""
        return self._store.list_keys()

    async def log_event(self, event: AuditEvent) -> None:
        """Append an audit event to the in-memory log."""
        self._audit_log.append(event)

    async def record_usage(self, record: UsageRecord) -> None:
        """Append a usage record to the in-memory list."""
        self._usage_records.append(record)

    async def close(self) -> None:
        """No-op for in-memory repository."""

    # ── Convenience accessors (not part of Protocol) ──────────────

    @property
    def key_store(self) -> ApiKeyStore:
        """Direct access to the underlying ``ApiKeyStore`` for verification."""
        return self._store

    @property
    def audit_log(self) -> list[AuditEvent]:
        """Read-only access to audit events (testing/debugging)."""
        return self._audit_log

    @property
    def usage_records(self) -> list[UsageRecord]:
        """Read-only access to usage records (testing/debugging)."""
        return self._usage_records
