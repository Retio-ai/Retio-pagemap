# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for per-tenant session limits (S3 Item 8).

Covers:
- Tenant session counter tracking
- MAX_SESSIONS_PER_TENANT enforcement
- ResourceExhaustionError on limit exceeded
- Counter not incremented for existing sessions
- Unlimited when MAX_SESSIONS_PER_TENANT=0
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap.errors import ResourceExhaustionError
from pagemap.session_manager import HttpSessionManager


@pytest.fixture()
def mock_pool():
    pool = MagicMock()
    pool.health.return_value = MagicMock(active=0, max_contexts=5, browser_connected=True)
    pool.acquire = AsyncMock()
    pool.release = AsyncMock()
    return pool


@pytest.fixture()
def session_manager(mock_pool):
    return HttpSessionManager(mock_pool)


class TestTenantSessionLimits:
    @pytest.mark.asyncio
    async def test_tenant_counter_increments(self, session_manager):
        """New session for a tenant increments the counter."""
        await session_manager.get_context("session-1", tenant_id="tenant-a")
        assert session_manager._tenant_session_counts.get("tenant-a") == 1

    @pytest.mark.asyncio
    async def test_multiple_tenants_tracked_separately(self, session_manager):
        """Different tenants have independent counters."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        await session_manager.get_context("s2", tenant_id="tenant-b")
        assert session_manager._tenant_session_counts.get("tenant-a") == 1
        assert session_manager._tenant_session_counts.get("tenant-b") == 1

    @pytest.mark.asyncio
    async def test_existing_session_no_double_count(self, session_manager):
        """Re-accessing existing session doesn't increment counter."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        await session_manager.get_context("s1", tenant_id="tenant-a")
        assert session_manager._tenant_session_counts.get("tenant-a") == 1

    @pytest.mark.asyncio
    @patch("pagemap.server.session_manager.MAX_SESSIONS_PER_TENANT", 2)
    async def test_limit_exceeded_raises(self, session_manager):
        """Exceeding tenant session limit raises ResourceExhaustionError."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        await session_manager.get_context("s2", tenant_id="tenant-a")
        with pytest.raises(ResourceExhaustionError, match="Tenant session limit exceeded"):
            await session_manager.get_context("s3", tenant_id="tenant-a")

    @pytest.mark.asyncio
    @patch("pagemap.server.session_manager.MAX_SESSIONS_PER_TENANT", 2)
    async def test_limit_not_exceeded_within_limit(self, session_manager):
        """Sessions within limit succeed."""
        ctx1 = await session_manager.get_context("s1", tenant_id="tenant-a")
        ctx2 = await session_manager.get_context("s2", tenant_id="tenant-a")
        assert ctx1 is not None
        assert ctx2 is not None

    @pytest.mark.asyncio
    @patch("pagemap.server.session_manager.MAX_SESSIONS_PER_TENANT", 0)
    async def test_zero_means_unlimited(self, session_manager):
        """MAX_SESSIONS_PER_TENANT=0 means no limit."""
        for i in range(10):
            await session_manager.get_context(f"s{i}", tenant_id="tenant-a")
        assert session_manager._tenant_session_counts.get("tenant-a") == 10

    @pytest.mark.asyncio
    async def test_no_tenant_id_no_tracking(self, session_manager):
        """Sessions without tenant_id are not tracked."""
        await session_manager.get_context("s1")
        assert len(session_manager._tenant_session_counts) == 0

    @pytest.mark.asyncio
    async def test_context_includes_tenant_id(self, session_manager):
        """RequestContext includes tenant_id field."""
        ctx = await session_manager.get_context("s1", tenant_id="tenant-a")
        assert ctx.tenant_id == "tenant-a"

    @pytest.mark.asyncio
    async def test_context_empty_tenant_id_default(self, session_manager):
        """RequestContext defaults to empty tenant_id."""
        ctx = await session_manager.get_context("s1")
        assert ctx.tenant_id == ""


# ── P0-1/P0-2 counter lifecycle tests ────────────────────────────


class TestTenantCounterLifecycle:
    @pytest.mark.asyncio
    async def test_counter_decrements_on_remove(self, session_manager):
        """remove_session decrements the tenant counter to 0."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        assert session_manager._tenant_session_counts.get("tenant-a") == 1
        await session_manager.remove_session("s1")
        assert session_manager._tenant_session_counts.get("tenant-a") is None

    @pytest.mark.asyncio
    async def test_counter_decrements_on_ttl_expiry(self, session_manager):
        """TTL-expired session decrements the tenant counter on re-access."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        assert session_manager._tenant_session_counts.get("tenant-a") == 1
        # Force TTL expiry by setting created_at far in the past
        entry = session_manager._sessions["s1"]
        entry.created_at = 0.0  # long ago
        # Re-access same session triggers TTL cleanup + re-creation
        await session_manager.get_context("s1", tenant_id="tenant-a")
        # s1 expired, cleaned up (counter -1), then re-created (counter +1) → still 1
        assert session_manager._tenant_session_counts.get("tenant-a") == 1

    @pytest.mark.asyncio
    async def test_shutdown_clears_counters(self, session_manager):
        """shutdown() empties the tenant counter dict."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        await session_manager.get_context("s2", tenant_id="tenant-b")
        await session_manager.shutdown()
        assert session_manager._tenant_session_counts == {}

    @pytest.mark.asyncio
    @patch("pagemap.server.session_manager.MAX_SESSIONS_PER_TENANT", 3)
    async def test_concurrent_creation_respects_limit(self, session_manager):
        """asyncio.gather concurrent creations respect the tenant limit."""
        results = await asyncio.gather(
            *[session_manager.get_context(f"s{i}", tenant_id="tenant-a") for i in range(5)],
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, ResourceExhaustionError)]
        assert len(successes) == 3
        assert len(failures) == 2

    @pytest.mark.asyncio
    async def test_counter_never_negative(self, session_manager):
        """Removing an already-removed session doesn't make counter negative."""
        await session_manager.get_context("s1", tenant_id="tenant-a")
        await session_manager.remove_session("s1")
        # Second remove is a no-op (entry already gone)
        await session_manager.remove_session("s1")
        assert session_manager._tenant_session_counts.get("tenant-a") is None
