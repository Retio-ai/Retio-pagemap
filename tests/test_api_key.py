# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.api_key — leaf module, no server.py imports."""

from __future__ import annotations

import re
import time

import pytest

from pagemap.api_key import (
    KEY_DETECTION_REGEX,
    ApiKeyStore,
    KeyRecord,
    KeyScope,
    KeyVersion,
    display_prefix,
    generate_api_key,
    hash_key,
    parse_key_version,
    validate_key_format,
)

# ── Key generation ──────────────────────────────────────────────


class TestGenerateApiKey:
    def test_format_matches_regex(self):
        key, _ = generate_api_key()
        assert re.fullmatch(KEY_DETECTION_REGEX, key), f"Key {key!r} doesn't match detection regex"

    def test_key_length(self):
        key, _ = generate_api_key()
        # "sk-pm-" (6) + "v1-" (3) + 43 base64url chars = 52
        assert len(key) == 52

    def test_prefix(self):
        key, _ = generate_api_key()
        assert key.startswith("sk-pm-v1-")

    def test_uniqueness(self):
        keys = {generate_api_key()[0] for _ in range(50)}
        assert len(keys) == 50

    def test_returns_hash(self):
        key, key_hash = generate_api_key()
        assert key_hash == hash_key(key)

    def test_version_v1(self):
        key, _ = generate_api_key(KeyVersion.V1)
        assert key.startswith("sk-pm-v1-")


# ── Hashing ─────────────────────────────────────────────────────


class TestHashKey:
    def test_deterministic(self):
        key = "sk-pm-v1-" + "A" * 43
        assert hash_key(key) == hash_key(key)

    def test_hex_digest_format(self):
        h = hash_key("test-key")
        assert re.fullmatch(r"[0-9a-f]{64}", h), f"Not a valid SHA3-256 hex digest: {h!r}"

    def test_different_keys_different_hashes(self):
        h1 = hash_key("key-1")
        h2 = hash_key("key-2")
        assert h1 != h2


# ── Constant-time comparison ────────────────────────────────────


class TestConstantTimeComparison:
    def test_verify_uses_hmac_compare_digest(self):
        """ApiKeyStore.verify must use hmac.compare_digest (checked via source inspection)."""
        import inspect

        source = inspect.getsource(ApiKeyStore.verify)
        assert "hmac.compare_digest" in source


# ── Format validation ───────────────────────────────────────────


class TestValidateKeyFormat:
    def test_valid_key(self):
        key, _ = generate_api_key()
        assert validate_key_format(key) is None

    def test_wrong_prefix(self):
        err = validate_key_format("wrong-prefix-" + "A" * 43)
        assert err is not None
        assert "sk-pm-" in err

    def test_wrong_version(self):
        err = validate_key_format("sk-pm-v99-" + "A" * 43)
        assert err is not None
        assert "unknown key version" in err

    def test_bad_length_short(self):
        err = validate_key_format("sk-pm-v1-ABC")
        assert err is not None

    def test_bad_chars(self):
        err = validate_key_format("sk-pm-v1-" + "!" * 43)
        assert err is not None

    def test_empty_string(self):
        err = validate_key_format("")
        assert err is not None


# ── Version parsing ─────────────────────────────────────────────


class TestParseKeyVersion:
    def test_v1_extraction(self):
        key, _ = generate_api_key(KeyVersion.V1)
        assert parse_key_version(key) == KeyVersion.V1

    def test_unknown_version_returns_none(self):
        assert parse_key_version("sk-pm-v99-" + "A" * 43) is None

    def test_invalid_format_returns_none(self):
        assert parse_key_version("garbage") is None


# ── Display prefix ──────────────────────────────────────────────


class TestDisplayPrefix:
    def test_truncation(self):
        key, _ = generate_api_key()
        dp = display_prefix(key)
        assert dp.startswith("sk-pm-v1-")
        assert dp.endswith("...")
        assert len(dp) < len(key)

    def test_invalid_key_fallback(self):
        dp = display_prefix("short")
        assert "..." in dp


# ── ApiKeyStore.create_key ──────────────────────────────────────


class TestCreateKey:
    def test_returns_display_key_and_record(self):
        store = ApiKeyStore()
        key, record = store.create_key("test-label")
        assert isinstance(key, str)
        assert isinstance(record, KeyRecord)
        assert record.label == "test-label"
        assert record.version == KeyVersion.V1

    def test_display_key_verifies(self):
        store = ApiKeyStore()
        key, _ = store.create_key("test")
        assert store.verify(key) is not None

    def test_max_keys_limit(self):
        store = ApiKeyStore(max_keys=2)
        store.create_key("key-1")
        store.create_key("key-2")
        with pytest.raises(ValueError, match="maximum key count"):
            store.create_key("key-3")

    def test_created_at_set(self):
        store = ApiKeyStore()
        before = time.time()
        _, record = store.create_key("ts-test")
        after = time.time()
        assert before <= record.created_at <= after


# ── ApiKeyStore.verify ──────────────────────────────────────────


class TestVerify:
    def test_valid_key(self):
        store = ApiKeyStore()
        key, _ = store.create_key("test")
        record = store.verify(key)
        assert record is not None
        assert record.label == "test"

    def test_invalid_key(self):
        store = ApiKeyStore()
        store.create_key("test")
        assert store.verify("sk-pm-v1-" + "X" * 43) is None

    def test_expired_key(self):
        store = ApiKeyStore()
        key, _ = store.create_key("exp-test", expires_at=time.time() - 1)
        assert store.verify(key) is None

    def test_revoked_key(self):
        store = ApiKeyStore()
        key, record = store.create_key("rev-test")
        store.revoke(record.key_hash)
        assert store.verify(key) is None

    def test_bad_format_rejected(self):
        store = ApiKeyStore()
        assert store.verify("not-a-key") is None

    def test_empty_store(self):
        store = ApiKeyStore()
        assert store.verify("sk-pm-v1-" + "A" * 43) is None


# ── ApiKeyStore.revoke ──────────────────────────────────────────


class TestRevoke:
    def test_revoke_existing(self):
        store = ApiKeyStore()
        _, record = store.create_key("rev")
        assert store.revoke(record.key_hash) is True

    def test_revoke_nonexistent(self):
        store = ApiKeyStore()
        assert store.revoke("nonexistent-hash") is False

    def test_revoke_already_revoked(self):
        store = ApiKeyStore()
        _, record = store.create_key("rev")
        store.revoke(record.key_hash)
        assert store.revoke(record.key_hash) is False


# ── ApiKeyStore.list_keys / invalidate ──────────────────────────


class TestListAndInvalidate:
    def test_list_keys(self):
        store = ApiKeyStore()
        store.create_key("a")
        store.create_key("b")
        keys = store.list_keys()
        assert len(keys) == 2
        labels = {k.label for k in keys}
        assert labels == {"a", "b"}

    def test_invalidate(self):
        store = ApiKeyStore()
        store.create_key("x")
        store.invalidate()
        assert store.total_key_count == 0

    def test_empty_list(self):
        store = ApiKeyStore()
        assert store.list_keys() == []


# ── Scopes ──────────────────────────────────────────────────────


class TestScopes:
    def test_default_full(self):
        store = ApiKeyStore()
        _, record = store.create_key("default-scope")
        assert record.scopes == frozenset({KeyScope.FULL})

    def test_custom_scopes(self):
        store = ApiKeyStore()
        scopes = frozenset({KeyScope.READ_ONLY})
        _, record = store.create_key("ro", scopes=scopes)
        assert record.scopes == scopes


# ── Properties ──────────────────────────────────────────────────


class TestProperties:
    def test_active_key_count(self):
        store = ApiKeyStore()
        store.create_key("a")
        _, rec = store.create_key("b")
        store.revoke(rec.key_hash)
        assert store.active_key_count == 1

    def test_total_key_count(self):
        store = ApiKeyStore()
        store.create_key("a")
        store.create_key("b")
        assert store.total_key_count == 2

    def test_active_excludes_expired(self):
        store = ApiKeyStore()
        store.create_key("alive")
        store.create_key("dead", expires_at=time.time() - 100)
        assert store.active_key_count == 1
        assert store.total_key_count == 2


# ── Edge cases ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_duplicate_labels_allowed(self):
        store = ApiKeyStore()
        store.create_key("dup")
        store.create_key("dup")
        assert store.total_key_count == 2
