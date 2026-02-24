# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite-backed repository — persistent storage for API keys, audit, and usage.

Uses ``aiosqlite`` (>=0.22.0, futures-based) with a single long-lived
connection.  WAL journal mode enables concurrent reads with serialized
writes.  Schema versioned via ``PRAGMA user_version``.

Dependencies: api_key.py (KeyRecord, KeyScope, KeyVersion),
              repository.py (AuditEvent, UsageRecord, RepositoryProtocol).
No server.py import (acyclic).
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import aiosqlite

from .api_key import KeyRecord, KeyScope, KeyVersion
from .repository import AuditEvent, UsageRecord

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Scope serialization helpers
# ---------------------------------------------------------------------------


def _serialize_scopes(scopes: frozenset[KeyScope]) -> str:
    """Comma-separated sorted StrEnum values: ``"full,read_only"``."""
    return ",".join(sorted(s.value for s in scopes))


def _deserialize_scopes(raw: str) -> frozenset[KeyScope]:
    """Split on ``,`` and wrap each part in ``KeyScope``."""
    if not raw:
        return frozenset()
    return frozenset(KeyScope(s) for s in raw.split(","))


def _row_to_key_record(row: aiosqlite.Row) -> KeyRecord:
    """Convert a positional row to a ``KeyRecord``."""
    return KeyRecord(
        key_hash=row[0],
        label=row[1],
        version=KeyVersion(row[2]),
        created_at=row[3],
        expires_at=row[4],
        revoked=bool(row[5]),
        scopes=_deserialize_scopes(row[6]),
    )


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CREATE_API_KEYS = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash   TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    version    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    scopes     TEXT NOT NULL DEFAULT 'full'
)
"""

_CREATE_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    timestamp  REAL NOT NULL,
    key_hash   TEXT NOT NULL DEFAULT '',
    client_ip  TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_USAGE_RECORDS = """
CREATE TABLE IF NOT EXISTS usage_records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash   TEXT NOT NULL,
    tool       TEXT NOT NULL,
    cost       INTEGER NOT NULL,
    timestamp  REAL NOT NULL,
    session_id TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_key_hash ON audit_log(key_hash)",
    "CREATE INDEX IF NOT EXISTS idx_usage_records_key_hash ON usage_records(key_hash)",
    "CREATE INDEX IF NOT EXISTS idx_usage_records_timestamp ON usage_records(timestamp)",
]


# ---------------------------------------------------------------------------
# SqliteRepository
# ---------------------------------------------------------------------------


class SqliteRepository:
    """SQLite-backed repository implementing ``RepositoryProtocol``.

    Use the ``create()`` async classmethod factory — never instantiate directly.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    @classmethod
    async def create(cls, db_path: str | Path) -> SqliteRepository:
        """Open (or create) a SQLite database and initialise the schema.

        Resolves ``~`` and creates parent directories automatically.

        Raises:
            ValueError: If the existing database has a newer schema version.
        """
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        db = await aiosqlite.connect(str(path))
        try:
            # Enable WAL + foreign keys
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")

            # Check schema version
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            current_version = row[0] if row else 0

            if current_version > _SCHEMA_VERSION:
                raise ValueError(
                    f"Database schema version {current_version} is newer than supported version {_SCHEMA_VERSION}"
                )

            if current_version < _SCHEMA_VERSION:
                # Create tables within an explicit transaction
                await db.execute(_CREATE_API_KEYS)
                await db.execute(_CREATE_AUDIT_LOG)
                await db.execute(_CREATE_USAGE_RECORDS)
                for idx_sql in _CREATE_INDEXES:
                    await db.execute(idx_sql)
                await db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                await db.commit()
        except BaseException:
            await db.close()
            raise

        return cls(db)

    # ── RepositoryProtocol methods ────────────────────────────────

    async def store_key(self, record: KeyRecord) -> None:
        """Store or replace an API key record."""
        await self._db.execute(
            "INSERT OR REPLACE INTO api_keys "
            "(key_hash, label, version, created_at, expires_at, revoked, scopes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.key_hash,
                record.label,
                record.version.value,
                record.created_at,
                record.expires_at,
                int(record.revoked),
                _serialize_scopes(record.scopes),
            ),
        )
        await self._db.commit()

    async def get_key(self, key_hash: str) -> KeyRecord | None:
        """Look up a key record by its hash. Returns ``None`` if not found."""
        cursor = await self._db.execute(
            "SELECT key_hash, label, version, created_at, expires_at, revoked, scopes FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_key_record(row)

    async def revoke_key(self, key_hash: str) -> bool:
        """Revoke a key. Returns ``True`` if found and not already revoked."""
        cursor = await self._db.execute(
            "UPDATE api_keys SET revoked = 1 WHERE key_hash = ? AND revoked = 0",
            (key_hash,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_keys(self) -> list[KeyRecord]:
        """Return all key records ordered by ``created_at``."""
        cursor = await self._db.execute(
            "SELECT key_hash, label, version, created_at, expires_at, revoked, scopes FROM api_keys ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [_row_to_key_record(r) for r in rows]

    async def log_event(self, event: AuditEvent) -> None:
        """Persist an audit event."""
        await self._db.execute(
            "INSERT INTO audit_log (event_type, timestamp, key_hash, client_ip, detail) VALUES (?, ?, ?, ?, ?)",
            (event.event_type, event.timestamp, event.key_hash, event.client_ip, event.detail),
        )
        await self._db.commit()

    async def record_usage(self, record: UsageRecord) -> None:
        """Persist a usage record."""
        await self._db.execute(
            "INSERT INTO usage_records (key_hash, tool, cost, timestamp, session_id) VALUES (?, ?, ?, ?, ?)",
            (record.key_hash, record.tool, record.cost, record.timestamp, record.session_id),
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection. Idempotent."""
        with suppress(Exception):
            await self._db.close()
