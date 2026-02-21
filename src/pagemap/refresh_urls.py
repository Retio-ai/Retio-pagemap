# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""URL auto-replacement pipeline for benchmark sites.

Replaces expired/dummy URLs in config.yaml with fresh product URLs discovered
from listing pages. Optionally re-collects snapshots after replacement.

Data flow:
    HealthReport (or auto-detect) -> expired URLs -> listing HTML
    -> <a href> parse -> config.yaml update -> recollect

Usage:
    from pagemap.refresh_urls import refresh_all_urls, refresh_and_collect
    results = refresh_all_urls(dry_run=True)
    results = refresh_and_collect(site_filter="wconcept")
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml

from .check_urls import HealthReport, HealthStatus, _is_dummy_url
from .collect_sim import CONFIG_PATH, SimulatorController, collect_snapshot_sim

logger = logging.getLogger(__name__)

_SNAPSHOT_DIR = Path(__file__).parent.parent.parent / "data" / "snapshots"


# ── Types ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UrlChange:
    """Record of a single URL replacement (immutable value)."""

    site_id: str
    page_type: str
    index: int
    old_url: str
    new_url: str
    source_listing_url: str
    reason: str


@dataclass
class RefreshResult:
    """Result of refresh operation for one site."""

    site_id: str
    changes: list[UrlChange] = field(default_factory=list)
    skipped_reason: str = ""
    candidate_count: int = 0
    listing_url_used: str = ""


# ── Pure functions ───────────────────────────────────────────────────────────

_HREF_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_product_urls(
    raw_html: str,
    base_url: str,
    product_pattern: str,
    *,
    exclude: set[str] | None = None,
    max_results: int = 20,
) -> list[str]:
    """Extract product URLs from listing page HTML.

    Parses <a href> tags, filters by domain and product_pattern on path,
    deduplicates while preserving document order.

    Args:
        raw_html: Raw HTML of a listing page.
        base_url: Base URL for resolving relative hrefs.
        product_pattern: Regex pattern to match against URL path.
        exclude: Set of URLs to exclude (already valid).
        max_results: Maximum number of results to return.

    Returns:
        List of absolute product URLs in document order.
    """
    if exclude is None:
        exclude = set()

    parsed_base = urlparse(base_url)
    base_domain = (parsed_base.hostname or "").removeprefix("www.")

    try:
        pattern_re = re.compile(product_pattern)
    except re.error:
        logger.warning("Invalid product_url_pattern: %s", product_pattern)
        return []

    seen: set[str] = set()
    results: list[str] = []

    for match in _HREF_RE.finditer(raw_html):
        href = match.group(1)

        # Resolve relative URLs
        if href.startswith("/"):
            href = urljoin(base_url, href)
        elif not href.startswith("http"):
            continue

        # Parse and filter
        parsed = urlparse(href)
        href_domain = (parsed.hostname or "").removeprefix("www.")

        # Same domain only
        if href_domain != base_domain:
            continue

        # Match product pattern against path
        if not pattern_re.search(parsed.path):
            continue

        # Normalize: strip fragment, deduplicate
        normalized = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"

        if normalized in seen or normalized in exclude:
            continue

        # Skip dummy URLs
        is_dummy, _ = _is_dummy_url(normalized)
        if is_dummy:
            continue

        seen.add(normalized)
        results.append(normalized)

        if len(results) >= max_results:
            break

    return results


# ── IO helpers ───────────────────────────────────────────────────────────────


def _get_listing_html(
    site_id: str,
    listing_url: str,
    controller: SimulatorController,
) -> str | None:
    """Fetch listing page HTML via simulator session."""
    try:
        resp = controller.session_start(listing_url)
        if resp is None:
            logger.warning("Failed to load listing for %s: %s", site_id, listing_url[:60])
            return None
        html = controller.session_get_html()
        controller.session_end()
        return html
    except Exception as e:
        logger.error("Error fetching listing for %s: %s", site_id, e)
        with contextlib.suppress(Exception):
            controller.session_end()
        return None


def _get_listing_html_from_snapshot(
    site_id: str,
    snapshot_dir: Path,
) -> tuple[str, str] | None:
    """Fallback: read listing page HTML from existing snapshot.

    Returns:
        (html, listing_url) tuple, or None if not found.
    """
    site_dir = snapshot_dir / site_id
    if not site_dir.exists():
        return None

    for page_dir in sorted(site_dir.iterdir()):
        if not page_dir.is_dir():
            continue
        if "listing" not in page_dir.name:
            continue

        raw_path = page_dir / "raw.html"
        meta_path = page_dir / "snapshot.json"
        if not raw_path.exists():
            continue

        html = raw_path.read_text(encoding="utf-8")
        url = ""
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            url = meta.get("url", "")
        return html, url

    return None


def _find_replacements(
    site_id: str,
    config: dict,
    controller: SimulatorController | None,
    snapshot_dir: Path | None,
) -> tuple[list[str], str]:
    """Find candidate replacement URLs from listing pages.

    Returns:
        (candidate_urls, listing_url_used).
    """
    site_cfg = config["sites"].get(site_id, {})
    product_pattern = site_cfg.get("product_url_pattern", "")
    if not product_pattern:
        logger.warning("No product_url_pattern for %s, skipping", site_id)
        return [], ""

    # Collect existing valid URLs to exclude
    url_map = site_cfg.get("urls", {})
    if isinstance(url_map, list):
        url_map = {"product_detail": url_map}
    existing_urls: set[str] = set()
    for urls in url_map.values():
        for u in urls:
            is_dummy, _ = _is_dummy_url(u)
            if not is_dummy:
                existing_urls.add(u)

    # Get listing URLs
    listing_urls = url_map.get("listing", [])

    # Try simulator first
    if controller and listing_urls:
        for listing_url in listing_urls:
            is_dummy, _ = _is_dummy_url(listing_url)
            if is_dummy:
                continue
            html = _get_listing_html(site_id, listing_url, controller)
            if html:
                candidates = extract_product_urls(
                    html,
                    listing_url,
                    product_pattern,
                    exclude=existing_urls,
                )
                if candidates:
                    return candidates, listing_url

    # Fallback: snapshot files
    snap_dir = snapshot_dir or _SNAPSHOT_DIR
    if snap_dir.exists():
        result = _get_listing_html_from_snapshot(site_id, snap_dir)
        if result:
            html, listing_url = result
            if html and listing_url:
                candidates = extract_product_urls(
                    html,
                    listing_url,
                    product_pattern,
                    exclude=existing_urls,
                )
                if candidates:
                    return candidates, listing_url

    return [], ""


def _refresh_site(
    site_id: str,
    bad_urls: list[tuple[int, str]],
    config: dict,
    controller: SimulatorController | None,
    *,
    dry_run: bool,
    snapshot_dir: Path | None = None,
) -> RefreshResult:
    """Replace bad URLs for a single site.

    Args:
        site_id: Site identifier.
        bad_urls: List of (index, url) tuples for URLs to replace.
        config: Loaded config dict (will be mutated if not dry_run).
        controller: SimulatorController instance (None to skip live fetch).
        dry_run: If True, do not modify config.
        snapshot_dir: Path to snapshot directory for fallback.
    """
    candidates, listing_url = _find_replacements(site_id, config, controller, snapshot_dir)
    result = RefreshResult(
        site_id=site_id,
        candidate_count=len(candidates),
        listing_url_used=listing_url,
    )

    if not candidates:
        result.skipped_reason = "No candidate URLs found"
        logger.warning("  %s: no candidate URLs found", site_id)
        return result

    site_cfg = config["sites"][site_id]
    url_map = site_cfg.get("urls", {})
    if isinstance(url_map, list):
        url_map = {"product_detail": url_map}
        site_cfg["urls"] = url_map

    for candidate_idx, (page_idx, old_url) in enumerate(bad_urls):
        if candidate_idx >= len(candidates):
            logger.warning("  %s: ran out of candidates at index %d", site_id, page_idx)
            break

        new_url = candidates[candidate_idx]

        # Determine page_type from index
        page_type = "product_detail"
        cumulative = 0
        for pt, urls in url_map.items():
            if cumulative + len(urls) > page_idx:
                page_type = pt
                local_idx = page_idx - cumulative
                break
            cumulative += len(urls)
        else:
            local_idx = page_idx

        change = UrlChange(
            site_id=site_id,
            page_type=page_type,
            index=local_idx,
            old_url=old_url,
            new_url=new_url,
            source_listing_url=listing_url,
            reason="auto-replaced",
        )
        result.changes.append(change)

        if not dry_run:
            url_map[page_type][local_idx] = new_url

        logger.info(
            "  [%s] %s[%d]: %s → %s", "DRY" if dry_run else "REPLACE", page_type, local_idx, old_url[:50], new_url[:50]
        )

    return result


def _save_config(config: dict, config_path: Path) -> None:
    """Save config dict to YAML with backup."""
    # Backup
    backup_path = config_path.with_suffix(".yaml.bak")
    if config_path.exists():
        shutil.copy2(config_path, backup_path)
        logger.info("Config backed up to %s", backup_path)

    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    logger.info("Config saved to %s", config_path)


def _write_change_log(results: list[RefreshResult], output_path: Path) -> None:
    """Write URL change audit log."""
    changes = []
    for r in results:
        for c in r.changes:
            changes.append(
                {
                    "site_id": c.site_id,
                    "page_type": c.page_type,
                    "index": c.index,
                    "old_url": c.old_url,
                    "new_url": c.new_url,
                    "source_listing_url": c.source_listing_url,
                    "reason": c.reason,
                }
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Change log saved to %s (%d changes)", output_path, len(changes))


# ── Public API ───────────────────────────────────────────────────────────────


def refresh_all_urls(
    *,
    health_report: HealthReport | None = None,
    config_path: Path = CONFIG_PATH,
    dry_run: bool = False,
    site_filter: str | None = None,
    use_simulator: bool = True,
) -> list[RefreshResult]:
    """Replace expired/dummy URLs in config.yaml with fresh ones.

    Args:
        health_report: Pre-computed health report. Auto-detects dummies if None.
        config_path: Path to config.yaml.
        dry_run: If True, don't modify config.yaml.
        site_filter: Only refresh this site.
        use_simulator: Whether to use simulator for live listing fetch.

    Returns:
        List of RefreshResult per site.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    controller: SimulatorController | None = None
    if use_simulator:
        controller = SimulatorController(config)
        if not controller.get_booted_device_udid():
            if not controller.boot_device():
                logger.warning("Simulator not available, falling back to snapshot-only")
                controller = None
        if controller:
            controller.launch_app()
            if not controller.wait_for_ping(timeout=10.0):
                logger.warning("App not responding, falling back to snapshot-only")
                controller = None

    # Build list of bad URLs per site
    sites_config = config.get("sites", {})
    target_sites = {site_filter: sites_config[site_filter]} if site_filter else sites_config
    if site_filter and site_filter not in sites_config:
        raise ValueError(f"Site '{site_filter}' not found in config")

    results: list[RefreshResult] = []

    for site_id, site_cfg in target_sites.items():
        url_map = site_cfg.get("urls", {})
        if isinstance(url_map, list):
            url_map = {"product_detail": url_map}

        bad_urls: list[tuple[int, str]] = []
        flat_idx = 0

        if health_report and site_id in health_report.sites:
            # Use health report
            for _page_type, hr_results in health_report.sites[site_id].items():
                for _i, hr in enumerate(hr_results):
                    if hr.status != HealthStatus.VALID:
                        bad_urls.append((flat_idx, hr.url))
                    flat_idx += 1
        else:
            # Auto-detect dummies only
            for _pt, urls in url_map.items():
                for _i, url in enumerate(urls):
                    is_dummy, _ = _is_dummy_url(url)
                    if is_dummy:
                        bad_urls.append((flat_idx, url))
                    flat_idx += 1

        if not bad_urls:
            logger.info("  %s: all URLs OK, skipping", site_id)
            results.append(RefreshResult(site_id=site_id, skipped_reason="All URLs OK"))
            continue

        logger.info("=== Refreshing %s (%d bad URLs) ===", site_id, len(bad_urls))
        result = _refresh_site(site_id, bad_urls, config, controller, dry_run=dry_run)
        results.append(result)

    # Save config + audit log
    if not dry_run and any(r.changes for r in results):
        _save_config(config, config_path)
        _write_change_log(results, config_path.parent / "url_changes.json")

    return results


def refresh_and_collect(
    *,
    health_report: HealthReport | None = None,
    config_path: Path = CONFIG_PATH,
    site_filter: str | None = None,
) -> list[RefreshResult]:
    """Replace URLs and re-collect snapshots.

    Chains refresh_all_urls() with collect_snapshot_sim(). On collection
    failure, retries with the next candidate URL (max 3 attempts).

    Args:
        health_report: Pre-computed health report.
        config_path: Path to config.yaml.
        site_filter: Only refresh this site.

    Returns:
        List of RefreshResult per site.
    """
    results = refresh_all_urls(
        health_report=health_report,
        config_path=config_path,
        dry_run=False,
        site_filter=site_filter,
        use_simulator=True,
    )

    # Re-load config after refresh
    with open(config_path) as f:
        config = yaml.safe_load(f)

    controller = SimulatorController(config)
    if not controller.get_booted_device_udid():
        controller.boot_device()
    controller.launch_app()
    if not controller.wait_for_ping(timeout=10.0):
        logger.error("App not responding for collection")
        return results

    for result in results:
        if not result.changes:
            continue

        for change in result.changes:
            url = change.new_url
            logger.info("Collecting %s/%s[%d]: %s", change.site_id, change.page_type, change.index, url[:60])

            meta = collect_snapshot_sim(
                url=url,
                site_id=change.site_id,
                page_type=change.page_type,
                page_index=change.index,
                controller=controller,
            )

            if meta is None:
                logger.warning("Collection failed for %s", url[:60])
                continue

            # Validate collected snapshot
            status = meta.get("validation_status", "")
            tokens = meta.get("html_token_count", 0)
            if status != "valid" or tokens < 1000:
                logger.warning(
                    "Collection quality issue: status=%s tokens=%d for %s",
                    status,
                    tokens,
                    url[:60],
                )

    return results
