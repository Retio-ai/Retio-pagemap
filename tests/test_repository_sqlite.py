# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SqliteRepository — persistent storage backend."""

from __future__ import annotations

import asyncio
import time

import aiosqlite
import pytest

from pagemap.api_key import KeyRecord, KeyScope, KeyVersion
from pagemap.repository import AuditEvent, RepositoryProtocol, UsageRecord
from pagemap.repository_sqlite import (
    SqliteRepository,
    _deserialize_scopes,
    _serialize_scopes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path):
    """Create a SqliteRepository in a temp directory, yield, then close."""
    r = await SqliteRepository.create(tmp_path / "test.db")
    yield r
    await r.close()


@pytest.fixture
def sample_key() -> KeyRecord:
    """Deterministic KeyRecord for testing."""
    return KeyRecord(
        key_hash="abc123def456",
        label="test-key",
        version=KeyVersion.V1,
        created_at=1700000000.0,
        expires_at=None,
        revoked=False,
        scopes=frozenset({KeyScope.FULL}),
    )


# ---------------------------------------------------------------------------
# TestCreate
# ---------------------------------------------------------------------------


class TestCreate:
    async def test_db_file_created(self, tmp_path):
        db_path = tmp_path / "data" / "pagemap.db"
        repo = await SqliteRepository.create(db_path)
        try:
            assert db_path.exists()
        finally:
            await repo.close()

    async def test_parent_dirs_created(self, tmp_path):
        db_path = tmp_path / "deep" / "nested" / "dir" / "pagemap.db"
        repo = await SqliteRepository.create(db_path)
        try:
            assert db_path.exists()
        finally:
            await repo.close()

    async def test_wal_mode(self, tmp_path):
        db_path = tmp_path / "wal.db"
        repo = await SqliteRepository.create(db_path)
        try:
            cursor = await repo._db.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row[0] == "wal"
        finally:
            await repo.close()

    async def test_schema_version(self, tmp_path):
        db_path = tmp_path / "ver.db"
        repo = await SqliteRepository.create(db_path)
        try:
            cursor = await repo._db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            assert row[0] == 1
        finally:
            await repo.close()

    async def test_reopen_preserves_data(self, tmp_path, sample_key):
        db_path = tmp_path / "reopen.db"
        repo = await SqliteRepository.create(db_path)
        await repo.store_key(sample_key)
        await repo.close()

        repo2 = await SqliteRepository.create(db_path)
        try:
            record = await repo2.get_key(sample_key.key_hash)
            assert record is not None
            assert record.label == sample_key.label
        finally:
            await repo2.close()

    async def test_future_version_raises(self, tmp_path):
        db_path = tmp_path / "future.db"
        repo = await SqliteRepository.create(db_path)
        await repo._db.execute("PRAGMA user_version = 999")
        await repo._db.commit()
        await repo.close()

        with pytest.raises(ValueError, match="newer than supported"):
            await SqliteRepository.create(db_path)


# ---------------------------------------------------------------------------
# TestStoreAndGetKey
# ---------------------------------------------------------------------------


class TestStoreAndGetKey:
    async def test_roundtrip(self, repo, sample_key):
        await repo.store_key(sample_key)
        record = await repo.get_key(sample_key.key_hash)
        assert record is not None
        assert record.key_hash == sample_key.key_hash
        assert record.label == sample_key.label
        assert record.version == sample_key.version
        assert record.created_at == sample_key.created_at
        assert record.expires_at == sample_key.expires_at
        assert record.revoked == sample_key.revoked
        assert record.scopes == sample_key.scopes

    async def test_nonexistent_returns_none(self, repo):
        assert await repo.get_key("nonexistent") is None

    async def test_overwrite_via_insert_or_replace(self, repo, sample_key):
        await repo.store_key(sample_key)
        updated = KeyRecord(
            key_hash=sample_key.key_hash,
            label="updated-label",
            version=KeyVersion.V1,
            created_at=sample_key.created_at,
            expires_at=None,
            revoked=False,
            scopes=frozenset({KeyScope.FULL}),
        )
        await repo.store_key(updated)
        record = await repo.get_key(sample_key.key_hash)
        assert record is not None
        assert record.label == "updated-label"

    async def test_expires_at_none_roundtrips(self, repo):
        key = KeyRecord(
            key_hash="no-expiry",
            label="no-expiry",
            version=KeyVersion.V1,
            created_at=time.time(),
            expires_at=None,
        )
        await repo.store_key(key)
        record = await repo.get_key("no-expiry")
        assert record is not None
        assert record.expires_at is None

    async def test_scopes_full(self, repo):
        key = KeyRecord(
            key_hash="scope-full",
            label="full",
            version=KeyVersion.V1,
            created_at=time.time(),
            scopes=frozenset({KeyScope.FULL}),
        )
        await repo.store_key(key)
        record = await repo.get_key("scope-full")
        assert record is not None
        assert record.scopes == frozenset({KeyScope.FULL})

    async def test_scopes_read_only(self, repo):
        key = KeyRecord(
            key_hash="scope-ro",
            label="ro",
            version=KeyVersion.V1,
            created_at=time.time(),
            scopes=frozenset({KeyScope.READ_ONLY}),
        )
        await repo.store_key(key)
        record = await repo.get_key("scope-ro")
        assert record is not None
        assert record.scopes == frozenset({KeyScope.READ_ONLY})

    async def test_scopes_both(self, repo):
        key = KeyRecord(
            key_hash="scope-both",
            label="both",
            version=KeyVersion.V1,
            created_at=time.time(),
            scopes=frozenset({KeyScope.FULL, KeyScope.READ_ONLY}),
        )
        await repo.store_key(key)
        record = await repo.get_key("scope-both")
        assert record is not None
        assert record.scopes == frozenset({KeyScope.FULL, KeyScope.READ_ONLY})

    async def test_scopes_empty(self, repo):
        key = KeyRecord(
            key_hash="scope-empty",
            label="empty",
            version=KeyVersion.V1,
            created_at=time.time(),
            scopes=frozenset(),
        )
        await repo.store_key(key)
        record = await repo.get_key("scope-empty")
        assert record is not None
        assert record.scopes == frozenset()


# ---------------------------------------------------------------------------
# TestRevokeKey
# ---------------------------------------------------------------------------


class TestRevokeKey:
    async def test_revoke_existing(self, repo, sample_key):
        await repo.store_key(sample_key)
        assert await repo.revoke_key(sample_key.key_hash) is True
        record = await repo.get_key(sample_key.key_hash)
        assert record is not None
        assert record.revoked is True

    async def test_revoke_nonexistent(self, repo):
        assert await repo.revoke_key("nonexistent") is False

    async def test_revoke_already_revoked(self, repo, sample_key):
        await repo.store_key(sample_key)
        assert await repo.revoke_key(sample_key.key_hash) is True
        assert await repo.revoke_key(sample_key.key_hash) is False


# ---------------------------------------------------------------------------
# TestListKeys
# ---------------------------------------------------------------------------


class TestListKeys:
    async def test_empty(self, repo):
        assert await repo.list_keys() == []

    async def test_multiple(self, repo):
        for i in range(3):
            key = KeyRecord(
                key_hash=f"key-{i}",
                label=f"label-{i}",
                version=KeyVersion.V1,
                created_at=1700000000.0 + i,
            )
            await repo.store_key(key)
        keys = await repo.list_keys()
        assert len(keys) == 3

    async def test_includes_revoked(self, repo, sample_key):
        await repo.store_key(sample_key)
        await repo.revoke_key(sample_key.key_hash)
        keys = await repo.list_keys()
        assert len(keys) == 1
        assert keys[0].revoked is True

    async def test_ordered_by_created_at(self, repo):
        for i in [2, 0, 1]:
            key = KeyRecord(
                key_hash=f"key-{i}",
                label=f"label-{i}",
                version=KeyVersion.V1,
                created_at=1700000000.0 + i,
            )
            await repo.store_key(key)
        keys = await repo.list_keys()
        assert [k.key_hash for k in keys] == ["key-0", "key-1", "key-2"]


# ---------------------------------------------------------------------------
# TestLogEvent
# ---------------------------------------------------------------------------


class TestLogEvent:
    async def test_persists(self, repo):
        event = AuditEvent(
            event_type="auth_rejected",
            timestamp=1700000000.0,
            key_hash="abc123",
            client_ip="192.168.1.1",
            detail="expired",
        )
        await repo.log_event(event)

        cursor = await repo._db.execute("SELECT event_type, key_hash, client_ip, detail FROM audit_log")
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "auth_rejected"
        assert rows[0][1] == "abc123"
        assert rows[0][2] == "192.168.1.1"
        assert rows[0][3] == "expired"

    async def test_multiple_events(self, repo):
        for i in range(5):
            await repo.log_event(AuditEvent(event_type=f"event-{i}", timestamp=time.time()))
        cursor = await repo._db.execute("SELECT COUNT(*) FROM audit_log")
        row = await cursor.fetchone()
        assert row[0] == 5


# ---------------------------------------------------------------------------
# TestRecordUsage
# ---------------------------------------------------------------------------


class TestRecordUsage:
    async def test_persists(self, repo):
        record = UsageRecord(
            key_hash="abc123",
            tool="get_page_map",
            cost=10,
            timestamp=1700000000.0,
            session_id="sess-1",
        )
        await repo.record_usage(record)

        cursor = await repo._db.execute("SELECT key_hash, tool, cost, session_id FROM usage_records")
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "abc123"
        assert rows[0][1] == "get_page_map"
        assert rows[0][2] == 10
        assert rows[0][3] == "sess-1"

    async def test_multiple_records(self, repo):
        for i in range(5):
            await repo.record_usage(UsageRecord(key_hash="k", tool="t", cost=i, timestamp=time.time()))
        cursor = await repo._db.execute("SELECT COUNT(*) FROM usage_records")
        row = await cursor.fetchone()
        assert row[0] == 5


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------


class TestClose:
    async def test_operate_after_close_raises(self, tmp_path, sample_key):
        repo = await SqliteRepository.create(tmp_path / "close.db")
        await repo.close()
        with pytest.raises((ValueError, aiosqlite.ProgrammingError)):
            await repo.store_key(sample_key)

    async def test_double_close_safe(self, tmp_path):
        repo = await SqliteRepository.create(tmp_path / "double.db")
        await repo.close()
        await repo.close()  # Should not raise


# ---------------------------------------------------------------------------
# TestConcurrentAccess
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    async def test_concurrent_store_key(self, repo):
        async def store(i: int):
            key = KeyRecord(
                key_hash=f"concurrent-{i}",
                label=f"label-{i}",
                version=KeyVersion.V1,
                created_at=time.time(),
            )
            await repo.store_key(key)

        await asyncio.gather(*(store(i) for i in range(20)))
        keys = await repo.list_keys()
        assert len(keys) == 20

    async def test_concurrent_read_write_mix(self, repo, sample_key):
        await repo.store_key(sample_key)

        async def read():
            return await repo.get_key(sample_key.key_hash)

        async def write(i: int):
            await repo.log_event(AuditEvent(event_type=f"evt-{i}", timestamp=time.time()))

        tasks = [read() if i % 2 == 0 else write(i) for i in range(20)]
        results = await asyncio.gather(*tasks)
        # Even indices should return the key record
        for i, result in enumerate(results):
            if i % 2 == 0:
                assert result is not None
                assert result.key_hash == sample_key.key_hash


# ---------------------------------------------------------------------------
# TestFullCrudCycle
# ---------------------------------------------------------------------------


class TestFullCrudCycle:
    async def test_store_get_list_revoke_get(self, repo, sample_key):
        # Store
        await repo.store_key(sample_key)

        # Get
        record = await repo.get_key(sample_key.key_hash)
        assert record is not None
        assert record.revoked is False

        # List
        keys = await repo.list_keys()
        assert len(keys) == 1

        # Revoke
        assert await repo.revoke_key(sample_key.key_hash) is True

        # Get after revoke
        record = await repo.get_key(sample_key.key_hash)
        assert record is not None
        assert record.revoked is True


# ---------------------------------------------------------------------------
# TestProtocolCompliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    async def test_isinstance_check(self, repo):
        assert isinstance(repo, RepositoryProtocol)


# ---------------------------------------------------------------------------
# TestScopesSerialization
# ---------------------------------------------------------------------------


class TestScopesSerialization:
    def test_serialize_full(self):
        assert _serialize_scopes(frozenset({KeyScope.FULL})) == "full"

    def test_serialize_read_only(self):
        assert _serialize_scopes(frozenset({KeyScope.READ_ONLY})) == "read_only"

    def test_serialize_both_sorted(self):
        result = _serialize_scopes(frozenset({KeyScope.READ_ONLY, KeyScope.FULL}))
        assert result == "full,read_only"

    def test_serialize_empty(self):
        assert _serialize_scopes(frozenset()) == ""

    def test_deserialize_full(self):
        assert _deserialize_scopes("full") == frozenset({KeyScope.FULL})

    def test_deserialize_both(self):
        assert _deserialize_scopes("full,read_only") == frozenset({KeyScope.FULL, KeyScope.READ_ONLY})

    def test_deserialize_empty(self):
        assert _deserialize_scopes("") == frozenset()
