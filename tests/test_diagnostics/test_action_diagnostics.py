# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 action failure diagnostics."""

from __future__ import annotations

from pagemap.diagnostics import ActionFailureType
from pagemap.diagnostics.action_diagnostics import diagnose_action_failure


class TestTimeoutDetection:
    def test_timeout_error_class(self):
        result = diagnose_action_failure(
            error=TimeoutError("Operation timed out"),
            action="click",
            ref=5,
            timed_out=True,
        )
        assert result.failure_type == ActionFailureType.TIMEOUT_EXCEEDED
        assert result.confidence >= 0.90
        assert result.ref == 5
        assert result.action == "click"

    def test_timeout_message_pattern(self):
        result = diagnose_action_failure(
            error=Exception("Timeout 30000ms exceeded"),
            action="click",
            ref=3,
        )
        assert result.failure_type == ActionFailureType.TIMEOUT_EXCEEDED


class TestElementHidden:
    def test_not_visible(self):
        result = diagnose_action_failure(
            error=Exception("Element is not visible"),
            action="click",
            ref=1,
        )
        assert result.failure_type == ActionFailureType.ELEMENT_HIDDEN
        assert result.confidence >= 0.85

    def test_hidden_element(self):
        result = diagnose_action_failure(
            error=Exception("Element is hidden and cannot be clicked"),
            action="click",
            ref=2,
        )
        assert result.failure_type == ActionFailureType.ELEMENT_HIDDEN


class TestElementBlocked:
    def test_intercept(self):
        result = diagnose_action_failure(
            error=Exception("Element click intercepted by overlay"),
            action="click",
            ref=4,
        )
        assert result.failure_type == ActionFailureType.ELEMENT_BLOCKED

    def test_covered_by_another(self):
        result = diagnose_action_failure(
            error=Exception("Element covered by another element"),
            action="click",
            ref=4,
        )
        assert result.failure_type == ActionFailureType.ELEMENT_BLOCKED


class TestStateChanged:
    def test_detached(self):
        result = diagnose_action_failure(
            error=Exception("Element is not attached to the DOM"),
            action="click",
            ref=7,
        )
        assert result.failure_type == ActionFailureType.STATE_CHANGED

    def test_removed(self):
        result = diagnose_action_failure(
            error=Exception("Node was removed from the document"),
            action="click",
            ref=7,
        )
        assert result.failure_type == ActionFailureType.STATE_CHANGED


class TestNavigationUnexpected:
    def test_url_changed(self):
        result = diagnose_action_failure(
            error=Exception("Something went wrong"),
            action="click",
            ref=10,
            pre_url="https://example.com/page1",
            post_url="https://example.com/page2",
        )
        assert result.failure_type == ActionFailureType.NAVIGATION_UNEXPECTED
        assert result.confidence >= 0.80


class TestNeverRaises:
    def test_unknown_error(self):
        result = diagnose_action_failure(
            error=Exception("Some completely unknown error"),
            action="click",
            ref=1,
        )
        # Should return STATE_CHANGED as default
        assert result.failure_type == ActionFailureType.STATE_CHANGED
        assert result.confidence > 0

    def test_empty_error(self):
        result = diagnose_action_failure(
            error=Exception(""),
            action="click",
            ref=1,
        )
        assert result is not None
        assert result.failure_type is not None
