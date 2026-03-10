# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S8-4: Plugin Signature Verification — TOFU SHA-256 integrity for manifests.

Pure module (no server state dependency). Pattern follows tool_authz.py.

Protects against CVE-2025-59536 style attacks (hook injection → RCE)
by detecting modifications to plugin manifest files after first use.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "IntegrityStatus",
    "FileIntegrityResult",
    "IntegrityReport",
    "IntegrityVerifier",
    "compute_file_hash",
    "verify_plugin_integrity",
    "emit_integrity_log",
    "emit_integrity_telem",
]

logger = logging.getLogger("pagemap.server")

# Files to protect (relative to project root)
_PROTECTED_FILES: tuple[str, ...] = (
    "server.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    "mcp.json",
)

_ENV_GATE = "PAGEMAP_PLUGIN_INTEGRITY"
_ENV_PINNED = "PAGEMAP_INTEGRITY_HASHES"
_MANIFEST_FILENAME = ".integrity_manifest.json"
_MAX_PARENT_LEVELS = 10
_CHUNK_SIZE = 8192


# ── Status enum ──────────────────────────────────────────────────────


class IntegrityStatus(StrEnum):
    OK = "ok"
    MISMATCH = "mismatch"
    MISSING = "missing"
    NEW = "new"


# ── Data types ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FileIntegrityResult:
    """Result of integrity check for a single file."""

    path: str
    status: IntegrityStatus
    expected_hash: str
    actual_hash: str


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """Aggregated integrity verification report."""

    results: tuple[FileIntegrityResult, ...]
    timestamp: float
    manifest_path: str
    has_violations: bool
    has_missing: bool
    new_files: int


# ── Hash computation ─────────────────────────────────────────────────


def compute_file_hash(path: Path) -> str | None:
    """Compute SHA-256 hex digest of a file. Returns None on I/O error (fail-open)."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ── Internal helpers ─────────────────────────────────────────────────


def _find_project_root() -> Path | None:
    """Walk up from this module's directory to find the project root.

    Returns the first directory containing ``server.json``, or None.
    """
    current = Path(__file__).resolve().parent
    for _ in range(_MAX_PARENT_LEVELS):
        if (current / "server.json").is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _resolve_manifest_path() -> Path:
    """Return manifest path under ``~/.pagemap/``, creating the directory if needed."""
    base = Path.home() / ".pagemap"
    base.mkdir(parents=True, exist_ok=True)
    return base / _MANIFEST_FILENAME


def _load_manifest(path: Path) -> dict[str, str]:
    """Load manifest JSON. Returns empty dict on any error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("manifest_version") == 1:
            return dict(data.get("hashes", {}))
    except Exception:  # nosec B110
        pass
    return {}


def _save_manifest(path: Path, hashes: dict[str, str]) -> None:
    """Atomically save manifest via tempfile + os.replace."""
    payload = json.dumps(
        {"manifest_version": 1, "hashes": hashes, "updated": time.time()},
        indent=2,
        ensure_ascii=False,
    )
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except Exception:  # nosec B110
        with suppress(OSError):
            os.unlink(tmp)


def _parse_pinned_hashes() -> dict[str, str]:
    """Parse ``PAGEMAP_INTEGRITY_HASHES`` env var (JSON object)."""
    raw = os.environ.get(_ENV_PINNED, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except Exception:  # nosec B110
        pass
    return {}


# ── Verifier ─────────────────────────────────────────────────────────


class IntegrityVerifier:
    """TOFU-based integrity verifier for plugin manifest files."""

    def __init__(
        self,
        *,
        project_root: Path,
        manifest_path: Path,
        pinned_hashes: dict[str, str] | None = None,
    ) -> None:
        self._root = project_root
        self._manifest_path = manifest_path
        self._pinned = pinned_hashes or {}

    def verify_all(self) -> IntegrityReport:
        """Verify all protected files and return an IntegrityReport."""
        stored = _load_manifest(self._manifest_path)
        # Pinned hashes override stored
        merged = {**stored, **self._pinned}

        results: list[FileIntegrityResult] = []
        new_hashes = dict(stored)
        new_count = 0

        for rel in _PROTECTED_FILES:
            fpath = self._root / rel
            actual = compute_file_hash(fpath)

            if actual is None:
                # File doesn't exist or unreadable
                expected = merged.get(rel, "")
                if expected:
                    results.append(FileIntegrityResult(rel, IntegrityStatus.MISSING, expected, ""))
                # If no expected hash and file doesn't exist, skip silently
                continue

            expected = merged.get(rel)
            if expected is None:
                # TOFU: first time seeing this file
                results.append(FileIntegrityResult(rel, IntegrityStatus.NEW, "", actual))
                new_hashes[rel] = actual
                new_count += 1
            elif actual == expected:
                results.append(FileIntegrityResult(rel, IntegrityStatus.OK, expected, actual))
            else:
                results.append(FileIntegrityResult(rel, IntegrityStatus.MISMATCH, expected, actual))

        # Save updated manifest (TOFU registration)
        if new_count > 0:
            _save_manifest(self._manifest_path, new_hashes)

        return IntegrityReport(
            results=tuple(results),
            timestamp=time.time(),
            manifest_path=str(self._manifest_path),
            has_violations=any(r.status == IntegrityStatus.MISMATCH for r in results),
            has_missing=any(r.status == IntegrityStatus.MISSING for r in results),
            new_files=new_count,
        )


# ── Public API ───────────────────────────────────────────────────────


def verify_plugin_integrity() -> IntegrityReport | None:
    """Run plugin integrity verification. Returns None if disabled or on error (fail-open)."""
    gate = os.environ.get(_ENV_GATE, "1").strip().lower()
    if gate in ("0", "false", "no", "off"):
        return None

    try:
        root = _find_project_root()
        if root is None:
            return None
        manifest = _resolve_manifest_path()
        pinned = _parse_pinned_hashes()
        verifier = IntegrityVerifier(project_root=root, manifest_path=manifest, pinned_hashes=pinned)
        return verifier.verify_all()
    except Exception:  # nosec B110
        return None


def emit_integrity_log(report: IntegrityReport) -> None:
    """Emit structured log entries for integrity results."""
    for r in report.results:
        extra = {"file": r.path, "status": r.status, "expected": r.expected_hash, "actual": r.actual_hash}
        if r.status == IntegrityStatus.MISMATCH:
            logger.warning("plugin_integrity_violation: %s", r.path, extra=extra)
        elif r.status == IntegrityStatus.MISSING:
            logger.warning("plugin_integrity_missing: %s", r.path, extra=extra)
        elif r.status == IntegrityStatus.NEW:
            logger.info("plugin_integrity_registered: %s", r.path, extra=extra)
        elif r.status == IntegrityStatus.OK:
            logger.info("plugin_integrity_ok: %s", r.path, extra=extra)


def emit_integrity_telem(report: IntegrityReport) -> None:
    """Emit telemetry events for integrity results (fire-and-forget)."""
    with suppress(Exception):  # nosec B110
        from pagemap.telemetry import emit
        from pagemap.telemetry.events import (
            PLUGIN_INTEGRITY_OK,
            PLUGIN_INTEGRITY_VIOLATION,
        )

        event = PLUGIN_INTEGRITY_VIOLATION if report.has_violations else PLUGIN_INTEGRITY_OK
        payload = {
            "has_violations": report.has_violations,
            "has_missing": report.has_missing,
            "new_files": report.new_files,
            "files_checked": len(report.results),
            "results": {r.path: r.status for r in report.results},
        }
        emit(event, payload)
