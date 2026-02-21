# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared test configuration and fixtures."""

try:
    import pagemap  # noqa: F401
except ImportError:
    raise ImportError("pagemap is not installed. Run: pip install -e '.[dev]'") from None

import pytest


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
