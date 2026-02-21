# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""URL health checker for benchmark sites.

Scans all URLs in config.yaml, classifies each as valid/expired/blocked/dummy/redirect,
and produces a JSON health report. Dummy URLs (placeholder IDs) are pre-filtered without
hitting the simulator, saving significant time.

Usage:
    from pagemap.check_urls import check_all_urls, save_report
    report = check_all_urls(site_filter="musinsa")
    save_report(report, Path("url_health_report.json"))
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from .collect import _count_tokens_approx, _validate_snapshot
from .collect_sim import SimulatorController, load_config

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────────────


class HealthStatus(StrEnum):
    """URL health classification result."""

    VALID = "valid"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    DUMMY = "dummy"
    REDIRECT = "redirect"


@dataclass(frozen=True)
class UrlHealthResult:
    """Health check result for a single URL (immutable value object)."""

    url: str
    status: HealthStatus
    final_url: str
    html_size: int
    token_count: int
    reason: str


@dataclass(frozen=True)
class HealthReportSummary:
    """Aggregate counts by status."""

    total: int
    valid: int
    expired: int
    blocked: int
    dummy: int
    redirect: int


@dataclass
class HealthReport:
    """Complete URL health check report."""

    checked_at: str
    summary: HealthReportSummary
    sites: dict[str, dict[str, list[UrlHealthResult]]]  # site -> page_type -> results


# ── Known dummy ID patterns ─────────────────────────────────────────────────

_KNOWN_DUMMY_IDS: frozenset[str] = frozenset(
    {
        "1234567001",
        "1234568001",
        "1234569001",
        "10000000001",
        "10000000002",
        "10000000003",
        "3500000001",
        "3500000002",
        "3500000003",
        "6000000001",
        "6000000002",
        "6000000003",
        "D000000001",
        "D000000002",
        "D000000003",
        "1000000000001",
        "1000000000002",
        "1000000000003",
        "LO12345678",
        "LO12345679",
        "LO12345680",
        "2000000001",
        "2000000002",
        "2000000003",
        "130000001",
        "130000002",
        "130000003",
        "10000001",
        "10000002",
        "10000003",
        "50000001",
        "50000002",
        "50000003",
        "P00000001",
        "P00000002",
        "P00000003",
        "SPJE1234567",
        "SPJE1234568",
        "SPJE1234569",
        "MSA1234567",
        "MSA1234568",
        "MSA1234569",
        "SAT1234567",
        "SAT1234568",
        "SAT1234569",
        "3800001",
        "3800002",
        "3800003",
        "15000001",
        "15000002",
        "15000003",
        "17000001",
        "17000002",
        "17000003",
        "20000001",
        "20000002",
        "20000003",
        "25000001",
        "25000002",
        "25000003",
        "303123456",
        "303234567",
        "303345678",
        "MWGC012B0002",
        "MWGC013C0003",
    }
)

# Patterns for product IDs that look like sequential placeholders
_DUMMY_ID_RE = re.compile(r"1234567\d{3}|123456[789]$")

# ── Anti-bot / expired patterns ──────────────────────────────────────────────

_ANTI_BOT_PATTERNS: frozenset[str] = frozenset(
    {
        "errors.edgesuite.net",
        "akamai",
        "access denied",
        "captcha",
        "challenge-platform",
        "cf-browser-verification",
        "just a moment",
    }
)

_EXPIRED_PATTERNS: frozenset[str] = frozenset(
    {
        'alert("존재하지않는',
        "alert('존재하지않는",
        "history.go(-1)",
        "품절",
        "판매종료",
        "판매 종료",
        "deleted product",
        "이 상품은 판매가 종료",
    }
)


# ── Pure functions ───────────────────────────────────────────────────────────


def _is_dummy_url(url: str) -> tuple[bool, str]:
    """Check if URL contains a known dummy/placeholder product ID.

    Returns:
        (is_dummy, reason) tuple.
    """
    # Extract last path segment or query param value that looks like an ID
    parsed = urlparse(url)
    path = parsed.path

    # Check against known dummy IDs in full URL
    for dummy_id in _KNOWN_DUMMY_IDS:
        if dummy_id in url:
            return True, f"Known dummy ID: {dummy_id}"

    # Check 1234567xxx pattern in path
    if _DUMMY_ID_RE.search(path):
        return True, "Sequential placeholder pattern (1234567xxx)"

    return False, ""


def _detect_redirect(original_url: str, final_url: str) -> tuple[bool, str]:
    """Detect meaningful redirects (product → home/search).

    Minor differences like www prefix or trailing slash are ignored.

    Returns:
        (is_redirect, reason) tuple.
    """
    orig_parsed = urlparse(original_url)
    final_parsed = urlparse(final_url)

    # Normalize: strip www, trailing slash
    def _normalize_host(host: str) -> str:
        return host.removeprefix("www.").rstrip(".")

    orig_host = _normalize_host(orig_parsed.hostname or "")
    final_host = _normalize_host(final_parsed.hostname or "")

    orig_path = orig_parsed.path.rstrip("/")
    final_path = final_parsed.path.rstrip("/")

    # Same host + same path → not a redirect
    if orig_host == final_host and orig_path == final_path:
        return False, ""

    # Different host entirely (subdomain change) but same domain → not meaningful
    if orig_host != final_host:
        # Check if it's just a www normalization
        if orig_host.split(".")[-2:] == final_host.split(".")[-2:]:
            if orig_path == final_path:
                return False, ""

    # Detect product → search/home redirect
    redirect_targets = {"/search", "/", ""}
    if final_path in redirect_targets or final_path.endswith("/search"):
        return True, f"Redirected from product to {final_path or '/'}"

    # Detect significant path change (e.g. /product/123 → /kr/ko/search)
    if orig_path != final_path and len(final_path) < len(orig_path) // 2:
        return True, f"Redirected to shorter path: {final_path}"

    return False, ""


def _classify(url: str, html: str | None, final_url: str) -> UrlHealthResult:
    """Classify a URL's health status.

    Priority: DUMMY → BLOCKED → REDIRECT → EXPIRED → VALID.
    """
    # 1. Dummy check (no HTML needed)
    is_dummy, dummy_reason = _is_dummy_url(url)
    if is_dummy:
        return UrlHealthResult(
            url=url, status=HealthStatus.DUMMY, final_url=final_url, html_size=0, token_count=0, reason=dummy_reason
        )

    # No HTML means navigation failed
    if html is None:
        return UrlHealthResult(
            url=url,
            status=HealthStatus.BLOCKED,
            final_url=final_url,
            html_size=0,
            token_count=0,
            reason="Navigation failed (no HTML)",
        )

    html_size = len(html)
    html_lower = html.lower()
    token_count = _count_tokens_approx(html)

    # 2. Anti-bot / blocked check
    if token_count < 2000:
        for pattern in _ANTI_BOT_PATTERNS:
            if pattern in html_lower:
                return UrlHealthResult(
                    url=url,
                    status=HealthStatus.BLOCKED,
                    final_url=final_url,
                    html_size=html_size,
                    token_count=token_count,
                    reason=f"Anti-bot pattern: {pattern}",
                )

    # 3. Redirect check
    is_redirect, redirect_reason = _detect_redirect(url, final_url)
    if is_redirect:
        return UrlHealthResult(
            url=url,
            status=HealthStatus.REDIRECT,
            final_url=final_url,
            html_size=html_size,
            token_count=token_count,
            reason=redirect_reason,
        )

    # 4. Expired check — _validate_snapshot reuse + extended patterns
    validation_status, validation_msg = _validate_snapshot(html, final_url)
    if validation_status in ("not_found", "blank", "minimal"):
        return UrlHealthResult(
            url=url,
            status=HealthStatus.EXPIRED,
            final_url=final_url,
            html_size=html_size,
            token_count=token_count,
            reason=f"Validation: {validation_status} — {validation_msg}",
        )
    if validation_status == "bot_blocked":
        return UrlHealthResult(
            url=url,
            status=HealthStatus.BLOCKED,
            final_url=final_url,
            html_size=html_size,
            token_count=token_count,
            reason=f"Validation: {validation_msg}",
        )

    # Extended expired patterns (Korean e-commerce specific)
    if token_count < 5000:
        for pattern in _EXPIRED_PATTERNS:
            if pattern in html_lower:
                return UrlHealthResult(
                    url=url,
                    status=HealthStatus.EXPIRED,
                    final_url=final_url,
                    html_size=html_size,
                    token_count=token_count,
                    reason=f"Expired pattern: {pattern}",
                )

    # 5. Valid
    return UrlHealthResult(
        url=url,
        status=HealthStatus.VALID,
        final_url=final_url,
        html_size=html_size,
        token_count=token_count,
        reason="OK",
    )


# ── IO functions ─────────────────────────────────────────────────────────────


def _check_site_urls(
    site_id: str,
    site_config: dict,
    controller: SimulatorController,
) -> dict[str, list[UrlHealthResult]]:
    """Check all URLs for a single site.

    Reuses the simulator session across URLs for efficiency.
    Dummy URLs are pre-classified without simulator access.

    Returns:
        page_type -> list of UrlHealthResult.
    """
    url_map = site_config.get("urls", {})
    if isinstance(url_map, list):
        url_map = {"product_detail": url_map}

    results: dict[str, list[UrlHealthResult]] = {}
    session_started = False

    for page_type, urls in url_map.items():
        page_results: list[UrlHealthResult] = []
        for url in urls:
            # Pre-filter dummies
            is_dummy, dummy_reason = _is_dummy_url(url)
            if is_dummy:
                page_results.append(
                    UrlHealthResult(
                        url=url,
                        status=HealthStatus.DUMMY,
                        final_url=url,
                        html_size=0,
                        token_count=0,
                        reason=dummy_reason,
                    )
                )
                logger.debug("  [DUMMY] %s", url[:80])
                continue

            # Live check via simulator
            try:
                if not session_started:
                    resp = controller.session_start(url)
                    if resp is None:
                        page_results.append(
                            UrlHealthResult(
                                url=url,
                                status=HealthStatus.BLOCKED,
                                final_url=url,
                                html_size=0,
                                token_count=0,
                                reason="Session start failed",
                            )
                        )
                        continue
                    session_started = True
                else:
                    resp = controller.session_navigate(url)
                    if resp is None:
                        # Try recovery: end + restart session
                        logger.warning("  Navigate failed, restarting session for %s", url[:60])
                        controller.session_end()
                        session_started = False
                        resp = controller.session_start(url)
                        if resp is None:
                            page_results.append(
                                UrlHealthResult(
                                    url=url,
                                    status=HealthStatus.BLOCKED,
                                    final_url=url,
                                    html_size=0,
                                    token_count=0,
                                    reason="Navigation failed after recovery",
                                )
                            )
                            continue
                        session_started = True

                html = controller.session_get_html()
                final_url = resp.get("url", url) if isinstance(resp, dict) else url
                result = _classify(url, html, final_url)
                page_results.append(result)
                logger.info("  [%s] %s → %s", result.status.value.upper(), url[:60], result.reason)

            except Exception as e:
                logger.error("  ERROR checking %s: %s", url[:60], e)
                # Try to recover session
                with contextlib.suppress(Exception):
                    controller.session_end()
                session_started = False
                page_results.append(
                    UrlHealthResult(
                        url=url,
                        status=HealthStatus.BLOCKED,
                        final_url=url,
                        html_size=0,
                        token_count=0,
                        reason=f"Exception: {e}",
                    )
                )

        results[page_type] = page_results

    # Clean up session
    if session_started:
        with contextlib.suppress(Exception):
            controller.session_end()

    return results


def check_all_urls(
    config: dict | None = None,
    *,
    site_filter: str | None = None,
) -> HealthReport:
    """Check health of all URLs in config.yaml.

    Args:
        config: Pre-loaded config dict. Loads from disk if None.
        site_filter: If set, only check this single site.

    Returns:
        HealthReport with per-site, per-page-type results.
    """
    if config is None:
        config = load_config()

    controller = SimulatorController(config)

    # Boot simulator
    if not controller.get_booted_device_udid():
        if not controller.boot_device():
            raise RuntimeError("Failed to boot simulator")

    if not controller.launch_app():
        raise RuntimeError("Failed to launch app")

    if not controller.wait_for_ping(timeout=10.0):
        raise RuntimeError("App not responding to ping")

    sites_config = config.get("sites", {})
    all_sites: dict[str, dict[str, list[UrlHealthResult]]] = {}

    # Counters
    counts: dict[HealthStatus, int] = {s: 0 for s in HealthStatus}

    target_sites = {site_filter: sites_config[site_filter]} if site_filter else sites_config
    if site_filter and site_filter not in sites_config:
        raise ValueError(f"Site '{site_filter}' not found in config")

    for site_id, site_cfg in target_sites.items():
        logger.info("=== Checking %s ===", site_id)
        site_results = _check_site_urls(site_id, site_cfg, controller)
        all_sites[site_id] = site_results

        for page_results in site_results.values():
            for r in page_results:
                counts[r.status] += 1

    total = sum(counts.values())
    summary = HealthReportSummary(
        total=total,
        valid=counts[HealthStatus.VALID],
        expired=counts[HealthStatus.EXPIRED],
        blocked=counts[HealthStatus.BLOCKED],
        dummy=counts[HealthStatus.DUMMY],
        redirect=counts[HealthStatus.REDIRECT],
    )

    return HealthReport(
        checked_at=datetime.now(UTC).isoformat(),
        summary=summary,
        sites=all_sites,
    )


def save_report(report: HealthReport, output_path: Path) -> None:
    """Serialize HealthReport to JSON.

    Args:
        report: The health report to save.
        output_path: Destination file path.
    """

    def _to_dict(obj: object) -> object:
        if isinstance(obj, UrlHealthResult | HealthReportSummary):
            return {k: _to_dict(v) for k, v in obj.__dict__.items()}
        if isinstance(obj, HealthStatus):
            return obj.value
        if isinstance(obj, dict):
            return {k: _to_dict(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_to_dict(v) for v in obj]
        return obj

    data = {
        "checked_at": report.checked_at,
        "summary": _to_dict(report.summary),
        "sites": _to_dict(report.sites),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Report saved to %s", output_path)
