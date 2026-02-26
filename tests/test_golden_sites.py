# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only
"""Golden-site regression tests.

Each variant-000 snapshot is checked across multiple quality dimensions:
page_type classification, minimum content tokens, HTML entity pollution,
image extraction, and keyword presence.  Split into separate test functions
so CI output pinpoints exactly WHICH dimension regressed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from pagemap import PageMap
from pagemap.page_map_builder import build_page_map_offline
from pagemap.preprocessing.preprocess import count_tokens
from pagemap.serializer import to_agent_prompt

# ── paths ──────────────────────────────────────────────────────────────

_SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"

# ── entity regex ───────────────────────────────────────────────────────

_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

# ── data model ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoldenCase:
    site_id: str
    page_dir: str  # e.g. "product_detail_page_000"
    expected_page_types: frozenset[str] | None  # None = skip check
    min_tokens: int  # semantic floor
    expect_images: bool  # True for product_detail with images
    content_keywords: tuple[str, ...]  # must appear in pruned_context


# ── cached builder ─────────────────────────────────────────────────────


@lru_cache(maxsize=64)
def _build(site_id: str, page_dir: str) -> PageMap | None:
    """Build PageMap once per snapshot; returns None if snapshot missing."""
    snap_dir = _SNAPSHOTS_DIR / site_id / page_dir
    raw_path = snap_dir / "raw.html"
    if not raw_path.exists():
        return None
    raw_html = raw_path.read_text("utf-8")
    url = "offline://unknown"
    meta_path = snap_dir / "snapshot.json"
    if meta_path.exists():
        url = json.loads(meta_path.read_text("utf-8")).get("url", url)
    return build_page_map_offline(raw_html=raw_html, url=url, site_id=site_id, page_id=page_dir)


# ── helpers ────────────────────────────────────────────────────────────


def _case_ids(cases: list[GoldenCase]) -> list[str]:
    return [f"{c.site_id}/{c.page_dir}" for c in cases]


def _get_or_skip(case: GoldenCase) -> PageMap:
    pm = _build(case.site_id, case.page_dir)
    if pm is None:
        pytest.skip(f"Snapshot not found: {case.site_id}/{case.page_dir}")
    return pm


# ── golden cases (all variant-000) ────────────────────────────────────

GOLDEN_CASES: list[GoldenCase] = [
    # ─── 29cm ──────────────────────────────────────────────────────
    GoldenCase("29cm", "listing_page_000", None, 20, False, ()),
    GoldenCase("29cm", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("트랙 재킷", "아디다스")),
    GoldenCase("29cm", "search_results_page_000", frozenset({"search_results"}), 5, False, ()),
    # ─── bbc_korean ────────────────────────────────────────────────
    GoldenCase("bbc_korean", "page_000", frozenset({"article", "news"}), 50, False, ("태권도", "주지사")),
    # ─── cos ───────────────────────────────────────────────────────
    GoldenCase("cos", "listing_page_000", None, 20, False, ()),
    GoldenCase("cos", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("FACADE",)),
    GoldenCase("cos", "search_results_page_000", None, 20, False, ()),
    # ─── coupang ───────────────────────────────────────────────────
    GoldenCase("coupang", "page_000", frozenset({"product_detail"}), 30, True, ("어반카", "컵홀더")),
    GoldenCase("coupang", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("어반카", "컵홀더")),
    # ─── github ────────────────────────────────────────────────────
    GoldenCase("github", "page_000", None, 5, False, ()),
    # ─── govkr ─────────────────────────────────────────────────────
    GoldenCase("govkr", "page_000", None, 5, False, ()),
    # ─── handsome ──────────────────────────────────────────────────
    GoldenCase("handsome", "listing_page_000", frozenset({"error"}), 5, False, ()),
    GoldenCase("handsome", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("TIME", "데님")),
    GoldenCase("handsome", "search_results_page_000", frozenset({"search_results"}), 20, False, ("handsome",)),
    # ─── hm ────────────────────────────────────────────────────────
    GoldenCase("hm", "listing_page_000", frozenset({"listing"}), 5, False, ()),
    GoldenCase("hm", "product_detail_page_000", None, 20, True, ()),
    GoldenCase("hm", "search_results_page_000", frozenset({"search_results"}), 20, False, ()),
    # ─── musinsa ───────────────────────────────────────────────────
    GoldenCase("musinsa", "listing_page_000", frozenset({"listing"}), 5, False, ()),
    GoldenCase("musinsa", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("바디백", "플레이언")),
    GoldenCase("musinsa", "search_results_page_000", frozenset({"search_results"}), 20, False, ("청바지",)),
    # ─── naver_news ────────────────────────────────────────────────
    GoldenCase("naver_news", "page_000", frozenset({"article", "news"}), 50, False, ("반효진", "사격")),
    # ─── nike ──────────────────────────────────────────────────────
    GoldenCase("nike", "listing_page_000", None, 5, False, ()),
    GoldenCase("nike", "product_detail_page_000", None, 5, False, ()),
    GoldenCase("nike", "search_results_page_000", None, 5, False, ()),
    # ─── ssfshop ───────────────────────────────────────────────────
    GoldenCase("ssfshop", "listing_page_000", None, 20, False, ()),
    GoldenCase("ssfshop", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("Suede", "Jacket")),
    GoldenCase("ssfshop", "search_results_page_000", None, 5, False, ()),
    # ─── uniqlo ────────────────────────────────────────────────────
    GoldenCase("uniqlo", "listing_page_000", None, 20, False, ()),
    GoldenCase("uniqlo", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("스웨트셔츠", "UNIQLO")),
    GoldenCase("uniqlo", "search_results_page_000", frozenset({"search_results"}), 5, False, ()),
    # ─── wconcept ──────────────────────────────────────────────────
    GoldenCase("wconcept", "listing_page_000", frozenset({"listing"}), 20, False, ("가디건",)),
    GoldenCase("wconcept", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("W CONCEPT",)),
    GoldenCase("wconcept", "search_results_page_000", frozenset({"search_results"}), 20, False, ()),
    # ─── wikipedia_ko ──────────────────────────────────────────────
    GoldenCase("wikipedia_ko", "page_000", frozenset({"article"}), 50, False, ("조선", "수도")),
    # ─── zara ──────────────────────────────────────────────────────
    GoldenCase("zara", "listing_page_000", None, 20, False, ()),
    GoldenCase("zara", "product_detail_page_000", frozenset({"product_detail"}), 30, True, ("레더", "점퍼", "ZARA")),
    GoldenCase("zara", "search_results_page_000", frozenset({"search_results"}), 5, False, ()),
]

# ── filtered subsets ───────────────────────────────────────────────────

_PAGE_TYPE_CASES = [c for c in GOLDEN_CASES if c.expected_page_types is not None]
_IMAGE_CASES = [c for c in GOLDEN_CASES if c.expect_images]
_KEYWORD_CASES = [c for c in GOLDEN_CASES if c.content_keywords]


# ── tests ──────────────────────────────────────────────────────────────


@pytest.mark.snapshot
@pytest.mark.parametrize("case", GOLDEN_CASES, ids=_case_ids(GOLDEN_CASES))
def test_golden_minimum_content(case: GoldenCase):
    """Pruned context must meet a semantic token floor."""
    pm = _get_or_skip(case)
    tokens = count_tokens(pm.pruned_context)
    assert tokens >= case.min_tokens, f"{case.site_id}/{case.page_dir}: {tokens} tokens < floor {case.min_tokens}"


@pytest.mark.snapshot
@pytest.mark.parametrize("case", GOLDEN_CASES, ids=_case_ids(GOLDEN_CASES))
def test_golden_no_html_entities(case: GoldenCase):
    """Agent prompt must not contain raw HTML entities."""
    pm = _get_or_skip(case)
    prompt = to_agent_prompt(pm)
    found = _ENTITY_RE.findall(prompt)
    assert not found, f"{case.site_id}/{case.page_dir}: HTML entities in prompt: {found[:5]}"


@pytest.mark.snapshot
@pytest.mark.parametrize("case", _PAGE_TYPE_CASES, ids=_case_ids(_PAGE_TYPE_CASES))
def test_golden_page_type(case: GoldenCase):
    """Page type must match one of the expected classifications."""
    pm = _get_or_skip(case)
    assert pm.page_type in case.expected_page_types, (
        f"{case.site_id}/{case.page_dir}: page_type={pm.page_type!r}, expected one of {case.expected_page_types}"
    )


@pytest.mark.snapshot
@pytest.mark.parametrize("case", _IMAGE_CASES, ids=_case_ids(_IMAGE_CASES))
def test_golden_images(case: GoldenCase):
    """Product pages must extract at least one image."""
    pm = _get_or_skip(case)
    assert pm.images, f"{case.site_id}/{case.page_dir}: no images extracted"


@pytest.mark.snapshot
@pytest.mark.parametrize("case", _KEYWORD_CASES, ids=_case_ids(_KEYWORD_CASES))
def test_golden_content_keywords(case: GoldenCase):
    """Key content words must survive pruning."""
    pm = _get_or_skip(case)
    ctx_lower = pm.pruned_context.lower()
    for kw in case.content_keywords:
        assert kw.lower() in ctx_lower, f"{case.site_id}/{case.page_dir}: keyword {kw!r} not in pruned_context"
