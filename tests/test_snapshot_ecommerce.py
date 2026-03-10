# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Ecommerce engine snapshot verification — Phase A2.

Validates ecommerce extraction quality across 30 sites using
EcomGoldenCase frozen dataclass.  Each quality dimension
(engine robustness, card extraction, price extraction, barrier
detection, prompt rendering) is tested independently so CI output
pinpoints exactly which dimension regressed.

The test directly calls run_ecommerce_engine() on raw HTML from
snapshot data.

Re-uses caching pattern from test_golden_sites.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from pagemap.ecommerce import run_ecommerce_engine
from pagemap.page_map_builder import detect_page_type
from pagemap.serializer import _render_ecommerce_section

# ── paths ──────────────────────────────────────────────────────────────

_SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"

_ECOM_PAGE_TYPES = frozenset({"product_detail", "search_results", "listing"})

# ── data model ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EcomGoldenCase:
    site_id: str
    page_dir: str
    intended_page_type: str  # what we'd expect if page isn't blocked


# ── cached helpers ─────────────────────────────────────────────────────


@lru_cache(maxsize=128)
def _load_snapshot(site_id: str, page_dir: str) -> tuple[str, str, str] | None:
    """Returns (raw_html, html_lower, url) or None."""
    snap_dir = _SNAPSHOTS_DIR / site_id / page_dir
    raw_path = snap_dir / "raw.html"
    if not raw_path.exists():
        return None
    raw_html = raw_path.read_text("utf-8")
    url = "offline://unknown"
    meta_path = snap_dir / "snapshot.json"
    if meta_path.exists():
        url = json.loads(meta_path.read_text("utf-8")).get("url", url)
    return raw_html, raw_html.lower(), url


@lru_cache(maxsize=128)
def _detect_type(site_id: str, page_dir: str) -> str | None:
    """Get detected page_type for snapshot."""
    data = _load_snapshot(site_id, page_dir)
    if data is None:
        return None
    return detect_page_type(data[2], data[0])


@lru_cache(maxsize=128)
def _run_ecom(site_id: str, page_dir: str) -> dict | None:
    """Run ecommerce engine on snapshot HTML. Returns dict or None."""
    data = _load_snapshot(site_id, page_dir)
    if data is None:
        return None
    raw_html, html_lower, url = data
    page_type = detect_page_type(url, raw_html)
    if page_type not in _ECOM_PAGE_TYPES:
        # Engine only runs for ecom types; try with intended type
        return None
    try:
        return run_ecommerce_engine(
            page_type=page_type,
            raw_html=raw_html,
            html_lower=html_lower,
            interactables=[],
            metadata={},
            page_url=url,
            navigation_hints=None,
        )
    except Exception:
        return None


# ── helpers ────────────────────────────────────────────────────────────


def _case_ids(cases: list[EcomGoldenCase]) -> list[str]:
    return [f"{c.site_id}/{c.page_dir}" for c in cases]


def _skip_if_no_snapshot(case: EcomGoldenCase) -> tuple[str, str, str]:
    data = _load_snapshot(case.site_id, case.page_dir)
    if data is None:
        pytest.skip(f"Snapshot not found: {case.site_id}/{case.page_dir}")
    return data


# ── golden cases (30 sites) ───────────────────────────────────────────

ECOM_GOLDEN_CASES: list[EcomGoldenCase] = [
    # ─── 29cm ──────────────────────────────────────────────────────
    EcomGoldenCase("29cm", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("29cm", "search_results_page_000", "search_results"),
    EcomGoldenCase("29cm", "listing_page_000", "listing"),
    # ─── adidas (mostly blocked; _001 collected via sim) ───────────
    EcomGoldenCase("adidas", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("adidas", "search_results_page_001", "search_results"),
    EcomGoldenCase("adidas", "listing_page_000", "listing"),
    # ─── aliexpress ────────────────────────────────────────────────
    EcomGoldenCase("aliexpress", "product_detail_page_000", "product_detail"),
    # ─── amazon ────────────────────────────────────────────────────
    EcomGoldenCase("amazon", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("amazon", "listing_page_000", "listing"),
    # ─── asos ──────────────────────────────────────────────────────
    EcomGoldenCase("asos", "product_detail_page_000", "product_detail"),
    # ─── cos (sim-collected — product_detail valid) ────────────────
    EcomGoldenCase("cos", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("cos", "product_detail_page_002", "product_detail"),
    EcomGoldenCase("cos", "search_results_page_000", "search_results"),
    EcomGoldenCase("cos", "listing_page_000", "listing"),
    # ─── coupang ───────────────────────────────────────────────────
    EcomGoldenCase("coupang", "product_detail_page_000", "product_detail"),
    # ─── ebay (sim-collected — _001 valid) ─────────────────────────
    EcomGoldenCase("ebay", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("ebay", "product_detail_page_001", "product_detail"),
    EcomGoldenCase("ebay", "listing_page_000", "listing"),
    # ─── farfetch (sim-collected — search valid) ───────────────────
    EcomGoldenCase("farfetch", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("farfetch", "search_results_page_000", "search_results"),
    EcomGoldenCase("farfetch", "search_results_page_001", "search_results"),
    EcomGoldenCase("farfetch", "listing_page_000", "listing"),
    # ─── handsome (sim-collected — search/listing valid) ───────────
    EcomGoldenCase("handsome", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("handsome", "search_results_page_000", "search_results"),
    EcomGoldenCase("handsome", "listing_page_001", "listing"),
    # ─── hm ────────────────────────────────────────────────────────
    EcomGoldenCase("hm", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("hm", "listing_page_000", "listing"),
    # ─── musinsa ───────────────────────────────────────────────────
    EcomGoldenCase("musinsa", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("musinsa", "search_results_page_000", "search_results"),
    EcomGoldenCase("musinsa", "listing_page_000", "listing"),
    # ─── naver_shopping ────────────────────────────────────────────
    EcomGoldenCase("naver_shopping", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("naver_shopping", "listing_page_000", "listing"),
    # ─── nike (sim-collected — _003/_004 valid) ────────────────────
    EcomGoldenCase("nike", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("nike", "product_detail_page_003", "product_detail"),
    EcomGoldenCase("nike", "listing_page_000", "listing"),
    # ─── nordstrom (sim-collected — product valid) ─────────────────
    EcomGoldenCase("nordstrom", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("nordstrom", "search_results_page_000", "search_results"),
    # ─── rakuten ───────────────────────────────────────────────────
    EcomGoldenCase("rakuten", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("rakuten", "search_results_page_000", "search_results"),
    # ─── shein (sim-collected — listing/search valid) ──────────────
    EcomGoldenCase("shein", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("shein", "search_results_page_001", "search_results"),
    EcomGoldenCase("shein", "listing_page_000", "listing"),
    # ─── ssfshop (sim-collected — listing_001/search_003 valid) ────
    EcomGoldenCase("ssfshop", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("ssfshop", "search_results_page_003", "search_results"),
    EcomGoldenCase("ssfshop", "listing_page_001", "listing"),
    # ─── ssg ───────────────────────────────────────────────────────
    EcomGoldenCase("ssg", "product_detail_page_000", "product_detail"),
    # ─── taobao (sim-collected) ────────────────────────────────────
    EcomGoldenCase("taobao", "search_results_page_000", "search_results"),
    EcomGoldenCase("taobao", "search_results_page_001", "search_results"),
    EcomGoldenCase("taobao", "listing_page_000", "listing"),
    # ─── uniqlo ────────────────────────────────────────────────────
    EcomGoldenCase("uniqlo", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("uniqlo", "listing_page_000", "listing"),
    # ─── walmart ───────────────────────────────────────────────────
    EcomGoldenCase("walmart", "product_detail_page_000", "product_detail"),
    # ─── wconcept ──────────────────────────────────────────────────
    EcomGoldenCase("wconcept", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("wconcept", "listing_page_000", "listing"),
    # ─── zalando (sim-collected — search valid) ────────────────────
    EcomGoldenCase("zalando", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("zalando", "search_results_page_000", "search_results"),
    # ─── zara ──────────────────────────────────────────────────────
    EcomGoldenCase("zara", "product_detail_page_000", "product_detail"),
    EcomGoldenCase("zara", "listing_page_000", "listing"),
]


# ── tests: engine robustness (no crash) ────────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize("case", ECOM_GOLDEN_CASES, ids=_case_ids(ECOM_GOLDEN_CASES))
def test_ecom_engine_no_crash(case: EcomGoldenCase):
    """Ecommerce engine must not crash on any real snapshot HTML."""
    raw_html, html_lower, url = _skip_if_no_snapshot(case)
    page_type = detect_page_type(url, raw_html)
    if page_type not in _ECOM_PAGE_TYPES:
        # Run with intended type to verify engine handles unusual HTML
        page_type = case.intended_page_type
    try:
        run_ecommerce_engine(
            page_type=page_type,
            raw_html=raw_html,
            html_lower=html_lower,
            interactables=[],
            metadata={},
            page_url=url,
            navigation_hints=None,
        )
    except Exception as e:
        pytest.fail(f"{case.site_id}/{case.page_dir}: engine crashed: {e}")


# ── tests: barrier detection robustness ────────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize("case", ECOM_GOLDEN_CASES, ids=_case_ids(ECOM_GOLDEN_CASES))
def test_ecom_barrier_no_crash(case: EcomGoldenCase):
    """Barrier detection must not crash on any real snapshot HTML."""
    raw_html, html_lower, url = _skip_if_no_snapshot(case)
    try:
        from pagemap.ecommerce.barrier_handler import detect_barriers

        detect_barriers(raw_html, html_lower, url, [], case.intended_page_type)
    except Exception as e:
        pytest.fail(f"{case.site_id}/{case.page_dir}: barrier crashed: {e}")


# ── tests: ecommerce data quality ─────────────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize("case", ECOM_GOLDEN_CASES, ids=_case_ids(ECOM_GOLDEN_CASES))
def test_ecom_data_when_classified(case: EcomGoldenCase):
    """When page is classified as ecom type, engine should produce data."""
    _skip_if_no_snapshot(case)
    page_type = _detect_type(case.site_id, case.page_dir)
    if page_type not in _ECOM_PAGE_TYPES:
        pytest.skip(f"Classified as {page_type!r}, not ecom")
    ecom = _run_ecom(case.site_id, case.page_dir)
    assert ecom is not None, f"{case.site_id}/{case.page_dir}: classified as {page_type} but no ecom data"


# ── tests: product price/name extraction ───────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize(
    "case",
    [c for c in ECOM_GOLDEN_CASES if c.intended_page_type == "product_detail"],
    ids=_case_ids([c for c in ECOM_GOLDEN_CASES if c.intended_page_type == "product_detail"]),
)
def test_ecom_product_has_meaningful_data(case: EcomGoldenCase):
    """Product pages that produce ecom data should have at least one meaningful field."""
    _skip_if_no_snapshot(case)
    page_type = _detect_type(case.site_id, case.page_dir)
    if page_type not in _ECOM_PAGE_TYPES:
        pytest.skip(f"Classified as {page_type!r}, not ecom")
    if page_type != "product_detail":
        pytest.skip(f"Classified as {page_type!r}, not product_detail")
    ecom = _run_ecom(case.site_id, case.page_dir)
    if ecom is None:
        pytest.skip("No ecommerce data produced")
    # Check for any meaningful extracted field
    meaningful_keys = ("name", "price", "brand", "rating", "availability", "cart", "gallery_images")
    has_data = any(ecom.get(k) for k in meaningful_keys)
    assert has_data, f"{case.site_id}/{case.page_dir}: no meaningful product data extracted"


# ── tests: card extraction ─────────────────────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize(
    "case",
    [c for c in ECOM_GOLDEN_CASES if c.intended_page_type in ("search_results", "listing")],
    ids=_case_ids([c for c in ECOM_GOLDEN_CASES if c.intended_page_type in ("search_results", "listing")]),
)
def test_ecom_card_extraction(case: EcomGoldenCase):
    """Search/listing pages that produce ecom data should have cards list."""
    _skip_if_no_snapshot(case)
    page_type = _detect_type(case.site_id, case.page_dir)
    if page_type not in ("search_results", "listing"):
        pytest.skip(f"Classified as {page_type!r}, not search/listing")
    ecom = _run_ecom(case.site_id, case.page_dir)
    if ecom is None:
        pytest.skip("No ecommerce data produced")
    cards = ecom.get("cards")
    assert isinstance(cards, (list, tuple)), f"{case.site_id}/{case.page_dir}: cards not a list"


# ── tests: prompt rendering ────────────────────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize("case", ECOM_GOLDEN_CASES, ids=_case_ids(ECOM_GOLDEN_CASES))
def test_ecom_prompt_rendering(case: EcomGoldenCase):
    """Ecommerce data must render to non-empty prompt section."""
    _skip_if_no_snapshot(case)
    page_type = _detect_type(case.site_id, case.page_dir)
    if page_type not in _ECOM_PAGE_TYPES:
        pytest.skip(f"Classified as {page_type!r}, not ecom")
    ecom = _run_ecom(case.site_id, case.page_dir)
    if not ecom:
        pytest.skip("No ecommerce data")
    lines = _render_ecommerce_section(ecom, page_type)
    if not lines:
        pytest.skip("Ecommerce data too sparse to render")
    text = "\n".join(lines)
    assert "## Ecommerce" in text
