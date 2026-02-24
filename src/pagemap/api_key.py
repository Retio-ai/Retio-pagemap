# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""API key management — generation, hashing, verification, and storage.

Standalone leaf module with zero dependency on server.py.
Uses stdlib only (hashlib, hmac, secrets, re, time).

Key format: ``sk-pm-v1-{base64url(32 bytes)}`` (52 chars total).

- SHA3-256 hashing (defense-in-depth; high-entropy keys don't need KDF)
- ``hmac.compare_digest`` constant-time comparison
- 256-bit entropy via ``secrets.token_urlsafe(32)``
- Versioned format for future algorithm migration
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum

KEY_PREFIX = "sk-pm-"
KEY_DETECTION_REGEX = r"sk-pm-v\d+-[A-Za-z0-9_-]{43}"  # for secret scanning tools

_KEY_FULL_RE = re.compile(r"^sk-pm-(v\d+)-([A-Za-z0-9_-]{43})$")


class KeyVersion(StrEnum):
    """Supported key hashing versions."""

    V1 = "v1"  # SHA3-256, 32-byte entropy


class KeyScope(StrEnum):
    """Permission scopes for API keys."""

    FULL = "full"
    READ_ONLY = "read_only"


@dataclass(frozen=True, slots=True)
class KeyRecord:
    """Immutable record for a stored API key (hash only — never stores raw key)."""

    key_hash: str
    label: str
    version: KeyVersion
    created_at: float  # time.time() — calendar deadline for expiration
    expires_at: float | None = None  # None = never
    revoked: bool = False
    scopes: frozenset[KeyScope] = frozenset({KeyScope.FULL})


# ---------------------------------------------------------------------------
# Standalone functions
# ---------------------------------------------------------------------------


def hash_key(raw_key: str) -> str:
    """Return SHA3-256 hex digest of *raw_key*."""
    return hashlib.sha3_256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(version: KeyVersion = KeyVersion.V1) -> tuple[str, str]:
    """Generate a new API key and its hash.

    Returns:
        (display_key, key_hash) — display_key is shown once, key_hash is stored.
    """
    entropy = secrets.token_urlsafe(32)  # 43 chars base64url
    display_key = f"{KEY_PREFIX}{version}-{entropy}"
    return display_key, hash_key(display_key)


def validate_key_format(raw_key: str) -> str | None:
    """Validate key format. Returns None if valid, error string if invalid."""
    if not raw_key.startswith(KEY_PREFIX):
        return f"key must start with '{KEY_PREFIX}'"
    m = _KEY_FULL_RE.match(raw_key)
    if m is None:
        return "key format invalid (expected sk-pm-v<N>-<43 base64url chars>)"
    ver = m.group(1)
    try:
        KeyVersion(ver)
    except ValueError:
        return f"unknown key version: {ver}"
    return None


def parse_key_version(raw_key: str) -> KeyVersion | None:
    """Extract key version from a raw key. Returns None if unparsable."""
    m = _KEY_FULL_RE.match(raw_key)
    if m is None:
        return None
    try:
        return KeyVersion(m.group(1))
    except ValueError:
        return None


def display_prefix(raw_key: str) -> str:
    """Return a safe-for-logging prefix: ``sk-pm-v1-Ab...``."""
    # Show prefix + first 2 chars of entropy
    m = _KEY_FULL_RE.match(raw_key)
    if m is None:
        return raw_key[:10] + "..."
    ver = m.group(1)
    entropy = m.group(2)
    return f"{KEY_PREFIX}{ver}-{entropy[:2]}..."


# ---------------------------------------------------------------------------
# ApiKeyStore
# ---------------------------------------------------------------------------


class ApiKeyStore:
    """In-memory API key store.

    Follows the ``robots_checker.py`` pattern: dict store with
    ``invalidate()`` for cleanup. Thread safety is managed by
    the caller (server-level locking in Phase δ).
    """

    def __init__(self, *, max_keys: int = 1000) -> None:
        self._keys: dict[str, KeyRecord] = {}  # key_hash → KeyRecord
        self._max_keys = max_keys

    def create_key(
        self,
        label: str,
        *,
        expires_at: float | None = None,
        scopes: frozenset[KeyScope] | None = None,
    ) -> tuple[str, KeyRecord]:
        """Create a new API key.

        Returns:
            (display_key, record) — display_key is shown once to the user.

        Raises:
            ValueError: if max_keys limit would be exceeded.
        """
        if len(self._keys) >= self._max_keys:
            raise ValueError(f"maximum key count ({self._max_keys}) reached")

        display_key, key_hash = generate_api_key()
        record = KeyRecord(
            key_hash=key_hash,
            label=label,
            version=KeyVersion.V1,
            created_at=time.time(),
            expires_at=expires_at,
            scopes=scopes if scopes is not None else frozenset({KeyScope.FULL}),
        )
        self._keys[key_hash] = record
        return display_key, record

    def verify(self, raw_key: str) -> KeyRecord | None:
        """Verify a raw API key. Returns KeyRecord if valid, None if rejected.

        Rejection reasons: format invalid, key not found, expired, revoked.
        Uses constant-time comparison via ``hmac.compare_digest``.
        """
        fmt_err = validate_key_format(raw_key)
        if fmt_err is not None:
            return None

        candidate_hash = hash_key(raw_key)

        matched_record: KeyRecord | None = None
        for stored_hash, record in self._keys.items():
            if hmac.compare_digest(candidate_hash, stored_hash):
                matched_record = record
                break

        if matched_record is None:
            return None

        # Check expiration
        if matched_record.expires_at is not None and time.time() > matched_record.expires_at:
            return None

        # Check revocation
        if matched_record.revoked:
            return None

        return matched_record

    def revoke(self, key_hash: str) -> bool:
        """Revoke a key by its hash. Returns True if found and revoked."""
        record = self._keys.get(key_hash)
        if record is None:
            return False
        if record.revoked:
            return False
        # Replace with revoked copy (frozen dataclass)
        self._keys[key_hash] = KeyRecord(
            key_hash=record.key_hash,
            label=record.label,
            version=record.version,
            created_at=record.created_at,
            expires_at=record.expires_at,
            revoked=True,
            scopes=record.scopes,
        )
        return True

    def list_keys(self) -> list[KeyRecord]:
        """Return all key records (including revoked)."""
        return list(self._keys.values())

    def invalidate(self) -> None:
        """Clear all stored keys."""
        self._keys.clear()

    @property
    def active_key_count(self) -> int:
        """Count of non-revoked, non-expired keys."""
        now = time.time()
        return sum(1 for r in self._keys.values() if not r.revoked and (r.expires_at is None or r.expires_at > now))

    @property
    def total_key_count(self) -> int:
        """Total number of stored key records."""
        return len(self._keys)
