# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared test configuration and fixtures."""

try:
    import pagemap  # noqa: F401
except ImportError:
    raise ImportError("pagemap is not installed. Run: pip install -e '.[dev]'") from None

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip snapshot-marked tests when data/snapshots/ is absent."""
    from pathlib import Path

    snapshots_dir = Path(__file__).parent.parent / "data" / "snapshots"
    if snapshots_dir.exists():
        return
    skip_marker = pytest.mark.skip(reason="data/snapshots/ not found")
    for item in items:
        if "snapshot" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _block_real_browser(request, monkeypatch):
    """Safety net: prevent real browser sessions in unit tests.

    Any test that needs a mock session should patch
    ``pagemap.server._get_session`` explicitly — that patch takes
    priority over this fixture.  Tests that forget to patch will get
    a clear error instead of silently trying to launch Chromium.

    Tests that test ``_get_session`` itself can opt out with::

        @pytest.mark.allow_real_get_session
    """
    if "allow_real_get_session" in request.keywords:
        return

    async def _no_real_session():
        raise RuntimeError(
            "Test tried to create a real browser session. Patch 'pagemap.server._get_session' in your test."
        )

    monkeypatch.setattr("pagemap.server._get_session", _no_real_session)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset server cache state before and after each test."""
    import pagemap.server as srv

    srv._state.cache.invalidate_all()
    old_robots = srv._robots_checker
    old_api_key_store = srv._api_key_store
    old_rate_limiter = srv._rate_limiter
    old_transport_mode = srv._transport_mode
    old_draining = srv._draining
    srv._robots_checker = None
    srv._api_key_store = None
    srv._rate_limiter = None
    srv._transport_mode = "stdio"
    srv._draining = False
    yield
    srv._state.cache.invalidate_all()
    srv._robots_checker = old_robots
    srv._api_key_store = old_api_key_store
    srv._rate_limiter = old_rate_limiter
    srv._transport_mode = old_transport_mode
    srv._draining = old_draining
