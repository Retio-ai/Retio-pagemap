# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.telemetry.privacy."""

from __future__ import annotations

from pagemap.telemetry.privacy import (
    _BLOCKED_FIELDS,
    get_installation_id,
    sanitize_payload,
    sanitize_url,
)

# ── sanitize_url ─────────────────────────────────────────────────


class TestSanitizeUrl:
    def test_removes_query_and_fragment(self):
        url = "https://example.com/page?q=secret&token=abc#section"
        result = sanitize_url(url)
        assert result == "https://example.com/page"

    def test_preserves_scheme_and_host(self):
        url = "https://sub.example.com/path/to/page"
        result = sanitize_url(url)
        assert result == "https://sub.example.com/path/to/page"

    def test_empty_url(self):
        assert sanitize_url("") == ""

    def test_invalid_url(self):
        # Should not raise, returns best-effort
        result = sanitize_url("not a url at all")
        assert isinstance(result, str)

    def test_url_with_port(self):
        url = "http://localhost:8080/api/v1?key=val"
        result = sanitize_url(url)
        assert result == "http://localhost:8080/api/v1"

    def test_hash_paths_false_preserves_path(self):
        url = "https://example.com/user/john/orders"
        result = sanitize_url(url, hash_paths=False)
        assert "/user/john/orders" in result

    def test_hash_paths_true_hashes_segments(self):
        url = "https://example.com/user/john/orders"
        result = sanitize_url(url, hash_paths=True)
        # Domain preserved
        assert "example.com" in result
        # Path segments are hashed (4-char hex each)
        parts = result.split("example.com")[1].split("/")
        # Filter out empty strings
        non_empty = [p for p in parts if p]
        assert len(non_empty) == 3  # user, john, orders → 3 hashes
        for seg in non_empty:
            assert len(seg) == 4  # SHA-256 truncated to 4 hex chars

    def test_hash_paths_deterministic(self):
        url = "https://example.com/user/john"
        r1 = sanitize_url(url, hash_paths=True)
        r2 = sanitize_url(url, hash_paths=True)
        assert r1 == r2

    def test_hash_paths_removes_query(self):
        url = "https://example.com/path?secret=val"
        result = sanitize_url(url, hash_paths=True)
        assert "secret" not in result
        assert "val" not in result


# ── sanitize_payload ─────────────────────────────────────────────


class TestSanitizePayload:
    def test_removes_blocked_fields(self):
        payload = {
            "tier": "C",
            "interactables": 12,
            "pruned_html": "<div>secret content</div>",
            "raw_html": "<html>full page</html>",
        }
        result = sanitize_payload(payload)
        assert "tier" in result
        assert "interactables" in result
        assert "pruned_html" not in result
        assert "raw_html" not in result

    def test_preserves_safe_fields(self):
        payload = {"tier": "A", "tokens": 500, "page_type": "article"}
        result = sanitize_payload(payload)
        assert result == payload

    def test_handles_nested_dict(self):
        payload = {
            "stage": "build",
            "details": {
                "time_ms": 150,
                "text": "should be blocked",
                "count": 5,
            },
        }
        result = sanitize_payload(payload)
        assert result["stage"] == "build"
        assert result["details"]["time_ms"] == 150
        assert result["details"]["count"] == 5
        assert "text" not in result["details"]

    def test_all_blocked_fields_defined(self):
        # Verify the blocked list contains expected dangerous fields
        assert "pruned_html" in _BLOCKED_FIELDS
        assert "raw_html" in _BLOCKED_FIELDS
        assert "text" in _BLOCKED_FIELDS
        assert "content" in _BLOCKED_FIELDS

    def test_empty_payload(self):
        assert sanitize_payload({}) == {}

    def test_returns_new_dict(self):
        payload = {"tier": "C"}
        result = sanitize_payload(payload)
        assert result is not payload


# ── get_installation_id ──────────────────────────────────────────


class TestGetInstallationId:
    def test_returns_hex_string(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_DIR", tmp_path)
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_ID_FILE", tmp_path / "installation_id")

        install_id = get_installation_id()
        assert isinstance(install_id, str)
        assert len(install_id) == 32  # uuid4().hex

    def test_persists_across_calls(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_DIR", tmp_path)
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_ID_FILE", tmp_path / "installation_id")

        id1 = get_installation_id()
        id2 = get_installation_id()
        assert id1 == id2

    def test_creates_directory(self, tmp_path, monkeypatch):
        new_dir = tmp_path / "subdir" / "pagemap"
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_DIR", new_dir)
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_ID_FILE", new_dir / "installation_id")

        get_installation_id()
        assert new_dir.exists()
        assert (new_dir / "installation_id").exists()

    def test_not_derived_from_hardware(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_DIR", tmp_path)
        monkeypatch.setattr("pagemap.telemetry.privacy._INSTALL_ID_FILE", tmp_path / "installation_id")

        install_id = get_installation_id()
        # Should be a valid hex string (UUID format)
        int(install_id, 16)  # Should not raise
