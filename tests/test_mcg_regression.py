# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""MCG regression tests: verify that known-difficult pages produce content.

Each test case represents a page that previously failed (empty pruned_context)
and was fixed. The tests ensure these pages continue to produce meaningful
output as the compression pipeline evolves.

Requires snapshot data in data/snapshots/. Skipped at collection time when
snapshots are absent (via the ``snapshot`` marker + conftest hook).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pagemap.page_map_builder import build_page_map_offline
from pagemap.preprocessing.preprocess import count_tokens

# Project-root snapshots directory
_SNAPSHOTS_DIR = Path(__file__).parent.parent / "data" / "snapshots"

# (site_id, page_type, variant, min_tokens)
# min_tokens is the minimum expected token count for pruned_context.
REGRESSION_CASES = [
    ("nike", "product_detail", "000", 10),
    ("hm", "listing", "000", 1),
    ("hm", "listing", "002", 1),
    ("handsome", "search_results", "000", 5),
    ("handsome", "search_results", "001", 5),
    ("handsome", "search_results", "002", 5),
    ("musinsa", "search_results", "000", 5),
    ("musinsa", "search_results", "001", 5),
    ("musinsa", "search_results", "002", 5),
    ("musinsa", "product_detail", "001", 1),
    ("ssfshop", "search_results", "000", 5),
    ("zara", "search_results", "000", 5),
]


def _case_id(val):
    """Generate readable test IDs from parametrize values."""
    if isinstance(val, tuple):
        return f"{val[0]}/{val[1]}/{val[2]}"
    return str(val)


def _snapshot_path(site_id: str, page_type: str, variant: str) -> Path:
    """Resolve snapshot directory path."""
    return _SNAPSHOTS_DIR / site_id / f"{page_type}_page_{variant}"


@pytest.mark.snapshot
@pytest.mark.parametrize(
    "site_id,page_type,variant,min_tokens",
    REGRESSION_CASES,
    ids=[f"{c[0]}/{c[1]}/{c[2]}" for c in REGRESSION_CASES],
)
def test_mcg_regression_produces_content(site_id, page_type, variant, min_tokens):
    """Verify that the page produces at least min_tokens of content."""
    snap_dir = _snapshot_path(site_id, page_type, variant)
    raw_path = snap_dir / "raw.html"

    if not raw_path.exists():
        pytest.skip(f"Snapshot not found: {snap_dir}")

    raw_html = raw_path.read_text(encoding="utf-8")

    # Load URL from snapshot metadata if available
    meta_path = snap_dir / "snapshot.json"
    url = "offline://unknown"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        url = meta.get("url", url)

    page_map = build_page_map_offline(
        raw_html=raw_html,
        url=url,
        site_id=site_id,
        page_id=f"{page_type}_page_{variant}",
    )

    token_count = count_tokens(page_map.pruned_context)
    assert token_count >= min_tokens, (
        f"{site_id}/{page_type}/{variant}: expected >= {min_tokens} tokens, got {token_count}"
    )
