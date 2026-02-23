# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Privacy utilities for telemetry data sanitization."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Content field names that must never appear in telemetry payloads
_BLOCKED_FIELDS = frozenset(
    {
        "pruned_html",
        "raw_html",
        "html",
        "text",
        "content",
        "page_source",
        "body",
        "inner_html",
        "outer_html",
        "snapshot",
        "value",
        "name",
    }
)

_INSTALL_DIR = Path.home() / ".pagemap"
_INSTALL_ID_FILE = _INSTALL_DIR / "installation_id"


def sanitize_url(url: str, *, hash_paths: bool = False) -> str:
    """Remove query/fragment from URL, optionally hash path segments.

    Args:
        url: URL to sanitize.
        hash_paths: If True, replace each path segment with a truncated SHA-256 hash.
            Domain is preserved for analytics.

    Returns:
        Sanitized URL string.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    path = parsed.path
    if hash_paths and path:
        segments = path.split("/")
        hashed = []
        for seg in segments:
            if seg:  # skip empty segments from leading/trailing slashes
                h = hashlib.sha256(seg.encode("utf-8")).hexdigest()[:4]
                hashed.append(h)
            else:
                hashed.append(seg)
        path = "/".join(hashed)

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def sanitize_payload(payload: dict) -> dict:
    """Remove blocked content fields from a payload dict (shallow + one level nested).

    Returns a new dict with blocked fields removed.
    """
    cleaned: dict = {}
    for key, value in payload.items():
        if key in _BLOCKED_FIELDS:
            continue
        if isinstance(value, dict):
            cleaned[key] = {k: v for k, v in value.items() if k not in _BLOCKED_FIELDS}
        else:
            cleaned[key] = value
    return cleaned


def get_installation_id() -> str:
    """Get or create a persistent anonymous installation ID.

    Stored at ~/.pagemap/installation_id. Derived from random UUID,
    not from hardware or user identity.
    """
    try:
        if _INSTALL_ID_FILE.exists():
            stored = _INSTALL_ID_FILE.read_text().strip()
            if stored:
                return stored
    except Exception:  # nosec B110
        pass

    install_id = uuid.uuid4().hex
    try:
        _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        _INSTALL_ID_FILE.write_text(install_id + "\n")
    except Exception:  # nosec B110
        pass  # Best-effort — return the generated ID even if persistence fails

    return install_id
