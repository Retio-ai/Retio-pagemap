# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for auto-dismiss barrier execution.

Covers JS API dismiss, button click, DOM stability, feature flags,
cookie policy, and safety guards.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap import Interactable  # noqa: F401
from pagemap.core.ecommerce import BarrierResult, BarrierType
from pagemap.server.barrier_dismisser import (
    _AUTO_DISMISS_TYPES,
    AutoDismissResult,
    try_auto_dismiss,
)


@pytest.fixture
def make_interactable():
    def _make(
        ref: int = 1,
        role: str = "button",
        name: str = "Click me",
        affordance: str = "click",
        region: str = "main",
        tier: int = 1,
        value: str = "",
        options: list[str] | None = None,
    ) -> Interactable:
        return Interactable(
            ref=ref,
            role=role,
            name=name,
            affordance=affordance,
            region=region,
            tier=tier,
            value=value,
            options=options or [],
        )

    return _make


@pytest.fixture
def mock_session():
    session = MagicMock()
    page = AsyncMock()
    page.url = "https://example.com"
    page.evaluate = AsyncMock(return_value=None)
    session.page = page
    return session


@pytest.fixture
def cookie_barrier():
    return BarrierResult(
        barrier_type=BarrierType.COOKIE_CONSENT,
        provider="cookiebot",
        auto_dismissible=True,
        accept_ref=1,
        confidence=0.95,
        signals=("cmp:cookiebot",),
        accept_terms=("accept all",),
        reject_terms=("reject all",),
        match_tier="reject",
        js_dismiss_call="Cookiebot.dialog && Cookiebot.dialog.submitDecline()",
    )


@pytest.fixture
def popup_barrier():
    return BarrierResult(
        barrier_type=BarrierType.POPUP_OVERLAY,
        provider="newsletter",
        auto_dismissible=True,
        accept_ref=2,
        confidence=0.80,
        signals=("html_popup:newsletter",),
        dismiss_terms=("close", "dismiss"),
        match_tier="dismiss",
    )


@pytest.fixture
def login_barrier():
    return BarrierResult(
        barrier_type=BarrierType.LOGIN_REQUIRED,
        provider="generic",
        auto_dismissible=False,
        accept_ref=None,
        confidence=0.8,
    )


def _make_page_map(barrier, interactables=None):
    """Create a minimal PageMap-like object with barrier and interactables."""
    pm = MagicMock()
    pm.barrier = barrier
    pm.interactables = interactables or []
    pm.metadata = {}
    return pm


class TestAutoDismissTypes:
    def test_allowed_types(self):
        assert "cookie_consent" in _AUTO_DISMISS_TYPES
        assert "age_verification" in _AUTO_DISMISS_TYPES
        assert "popup_overlay" in _AUTO_DISMISS_TYPES

    def test_blocked_types(self):
        assert "login_required" not in _AUTO_DISMISS_TYPES
        assert "region_restricted" not in _AUTO_DISMISS_TYPES


class TestJSAPIDismiss:
    @pytest.mark.asyncio
    async def test_js_dismiss_success(self, mock_session, cookie_barrier, make_interactable):
        """JS API dismiss should succeed for cookie barriers."""
        interactables = [make_interactable(ref=1, role="button", name="Reject All")]
        page_map = _make_page_map(cookie_barrier, interactables)

        with patch("pagemap.server.barrier_dismisser._wait_for_dom_stability", new_callable=AsyncMock):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "reject")

        assert result.success is True
        assert result.method == "js_api"
        assert result.barrier_type == "cookie_consent"
        mock_session.page.evaluate.assert_called_once()

    @pytest.mark.asyncio
    async def test_js_dismiss_accept_policy(self, mock_session, make_interactable):
        """cookie_policy=accept should use accept JS API."""
        barrier = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="cookiebot",
            auto_dismissible=True,
            accept_ref=1,
            confidence=0.95,
            js_dismiss_call="Cookiebot.dialog && Cookiebot.dialog.submitDecline()",
        )
        interactables = [make_interactable(ref=1, role="button", name="Accept All")]
        page_map = _make_page_map(barrier, interactables)

        with patch("pagemap.server.barrier_dismisser._wait_for_dom_stability", new_callable=AsyncMock):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "accept")

        assert result.success is True
        assert result.method == "js_api"
        # Should have used the accept JS call from _CMP_JS_ACCEPT
        call_args = mock_session.page.evaluate.call_args[0][0]
        assert "submitConsent" in call_args or "submitDecline" in call_args

    @pytest.mark.asyncio
    async def test_js_dismiss_failure_falls_through(self, mock_session, cookie_barrier, make_interactable):
        """If JS API fails, should fall through to button click."""
        mock_session.page.evaluate = AsyncMock(side_effect=Exception("JS error"))
        interactables = [make_interactable(ref=1, role="button", name="Reject All")]
        page_map = _make_page_map(cookie_barrier, interactables)

        with (
            patch("pagemap.server.barrier_dismisser._wait_for_dom_stability", new_callable=AsyncMock),
            patch("pagemap.server.barrier_dismisser._execute_click_dismiss", new_callable=AsyncMock, return_value=True),
        ):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "reject")

        assert result.success is True
        assert result.method == "reject"  # Fell through to button click


class TestButtonClickDismiss:
    @pytest.mark.asyncio
    async def test_click_dismiss_success(self, mock_session, popup_barrier, make_interactable):
        """Button click dismiss for popup overlay."""
        interactables = [make_interactable(ref=2, role="button", name="Close")]
        page_map = _make_page_map(popup_barrier, interactables)

        with patch(
            "pagemap.server.barrier_dismisser._execute_click_dismiss", new_callable=AsyncMock, return_value=True
        ):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "reject")

        assert result.success is True
        assert result.method == "dismiss"
        assert result.click_ref == 2

    @pytest.mark.asyncio
    async def test_click_dismiss_failure(self, mock_session, popup_barrier, make_interactable):
        """Failed click should return success=False."""
        interactables = [make_interactable(ref=2, role="button", name="Close")]
        page_map = _make_page_map(popup_barrier, interactables)

        with patch(
            "pagemap.server.barrier_dismisser._execute_click_dismiss", new_callable=AsyncMock, return_value=False
        ):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "reject")

        assert result.success is False
        assert result.error == "click dismiss failed"


class TestSafetyGuards:
    @pytest.mark.asyncio
    async def test_login_barrier_skipped(self, mock_session, login_barrier):
        """LOGIN_REQUIRED should never be auto-dismissed."""
        page_map = _make_page_map(login_barrier)
        result = await try_auto_dismiss(mock_session, page_map, [], "reject")
        assert result.success is False
        assert "not auto-dismissible" in result.error

    @pytest.mark.asyncio
    async def test_region_barrier_skipped(self, mock_session):
        """REGION_RESTRICTED should never be auto-dismissed."""
        barrier = BarrierResult(
            barrier_type=BarrierType.REGION_RESTRICTED,
            provider="generic",
            auto_dismissible=False,
            accept_ref=None,
            confidence=0.8,
        )
        page_map = _make_page_map(barrier)
        result = await try_auto_dismiss(mock_session, page_map, [], "reject")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_barrier(self, mock_session):
        """No barrier → no dismiss attempt."""
        page_map = _make_page_map(None)
        result = await try_auto_dismiss(mock_session, page_map, [], "reject")
        assert result.success is False
        assert result.error == "no barrier"

    @pytest.mark.asyncio
    async def test_no_dismiss_target(self, mock_session, make_interactable):
        """Barrier with no accept_ref and no js_dismiss_call → no dismiss."""
        barrier = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="generic",
            auto_dismissible=True,
            accept_ref=None,
            confidence=0.7,
        )
        page_map = _make_page_map(barrier)
        result = await try_auto_dismiss(mock_session, page_map, [], "reject")
        assert result.success is False
        assert result.error == "no dismiss target"


class TestCookiePolicy:
    @pytest.mark.asyncio
    async def test_policy_none_skips_cookie(self, mock_session, cookie_barrier, make_interactable):
        """cookie_policy=none should skip cookie banner dismiss."""
        interactables = [make_interactable(ref=1, role="button", name="Accept")]
        page_map = _make_page_map(cookie_barrier, interactables)

        result = await try_auto_dismiss(mock_session, page_map, interactables, "none")
        assert result.success is False
        assert "cookie policy is none" in result.error

    @pytest.mark.asyncio
    async def test_policy_none_allows_popup(self, mock_session, popup_barrier, make_interactable):
        """cookie_policy=none should still allow popup dismiss."""
        interactables = [make_interactable(ref=2, role="button", name="Close")]
        page_map = _make_page_map(popup_barrier, interactables)

        with patch(
            "pagemap.server.barrier_dismisser._execute_click_dismiss", new_callable=AsyncMock, return_value=True
        ):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "none")

        assert result.success is True


class TestNeverRaises:
    @pytest.mark.asyncio
    async def test_exception_in_dismiss(self, mock_session, cookie_barrier, make_interactable):
        """try_auto_dismiss should never raise, even on internal errors."""
        interactables = [make_interactable(ref=1, role="button", name="Accept")]
        page_map = _make_page_map(cookie_barrier, interactables)

        # Force an exception by making session.page.evaluate raise
        mock_session.page.evaluate = AsyncMock(side_effect=Exception("boom"))

        with patch(
            "pagemap.server.barrier_dismisser._execute_click_dismiss",
            new_callable=AsyncMock,
            side_effect=Exception("also boom"),
        ):
            result = await try_auto_dismiss(mock_session, page_map, interactables, "reject")

        assert isinstance(result, AutoDismissResult)
        assert result.success is False


class TestAutoResult:
    def test_result_fields(self):
        r = AutoDismissResult(
            success=True,
            method="js_api",
            barrier_type="cookie_consent",
            click_ref=None,
            elapsed_ms=42.5,
        )
        assert r.success is True
        assert r.method == "js_api"
        assert r.elapsed_ms == 42.5
        assert r.error == ""
