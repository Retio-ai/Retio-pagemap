# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""QR-06: Captcha/block page detection — integration tests."""

from __future__ import annotations

import pytest

from pagemap.browser_session import NavigationResult
from pagemap.page_map_builder import _check_blocked_page, build_page_map_offline


class TestBlockedPageWarning:
    def test_adds_warning_and_metadata(self):
        warnings: list[str] = []
        metadata: dict = {}
        _check_blocked_page("blocked", warnings, metadata, url="https://example.com", http_status=403)
        assert len(warnings) == 1
        assert "anti-bot" in warnings[0]
        assert metadata["blocked_info"]["detected"] is True
        assert metadata["blocked_info"]["http_status"] == 403

    @pytest.mark.parametrize(
        "ptype",
        [
            "product_detail",
            "search_results",
            "article",
            "error",
            "unknown",
        ],
    )
    def test_noop_for_other_types(self, ptype):
        warnings: list[str] = []
        metadata: dict = {}
        _check_blocked_page(ptype, warnings, metadata)
        assert warnings == []
        assert "blocked_info" not in metadata

    def test_no_http_status_omits_key(self):
        warnings: list[str] = []
        metadata: dict = {}
        _check_blocked_page("blocked", warnings, metadata)
        assert metadata["blocked_info"]["detected"] is True
        assert "http_status" not in metadata["blocked_info"]


class TestBlockedPageOffline:
    def test_cloudflare_challenge(self):
        html = (
            "<html><head><title>Just a moment...</title></head>"
            '<body><div class="cf-browser-verification"></div></body></html>'
        )
        pm = build_page_map_offline(html, url="https://google.com/search?q=test")
        assert pm.page_type == "blocked"
        assert any("anti-bot" in w for w in pm.warnings)
        assert pm.metadata.get("blocked_info", {}).get("detected") is True

    def test_normal_page_not_blocked(self):
        html = f"<html><head><title>Search</title></head><body>{'Result ' * 200}</body></html>"
        pm = build_page_map_offline(html, url="https://google.com/search?q=test")
        assert pm.page_type != "blocked"


class TestNavigationResultHttpStatus:
    def test_navigation_result_has_http_status(self):
        nr = NavigationResult(strategy="load", settle_metrics=None, http_status=403)
        assert nr.http_status == 403

    def test_navigation_result_none_status(self):
        nr = NavigationResult(strategy="load", settle_metrics=None)
        assert nr.http_status is None
