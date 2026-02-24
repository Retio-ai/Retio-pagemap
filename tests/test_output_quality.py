# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Output quality gates: CI-enforceable metrics for PageMap extraction quality.

Category A (TestSyntheticQuality): Always runs — uses inline HTML fixtures.
Category B (TestSnapshotQuality): Requires data/snapshots/ — skipped via
    the ``snapshot`` marker + conftest hook when absent.

Metrics enforced:
- Extraction ratio: pruned_tokens / raw_html_tokens (dual: ratio + absolute)
- Interactable validity: role, name, affordance fields populated correctly
- Entity leak detection: no raw HTML entities in output
- Image URL entity detection: no &amp; in extracted image URLs
- Tracking pixel detection: not ALL images are tracking pixels
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pagemap import PageMap
from pagemap.page_map_builder import build_page_map_offline
from pagemap.preprocessing.preprocess import count_tokens
from pagemap.pruned_context_builder import (
    _EXCLUDE_IMG_PATTERNS,
    extract_product_images,
)
from pagemap.serializer import to_agent_prompt

# ---------------------------------------------------------------------------
# Thresholds — dual gate: ratio (scale-relative) + absolute min tokens
# ---------------------------------------------------------------------------

MIN_EXTRACTION_RATIO: dict[str, float] = {
    "product_detail": 0.00005,  # observed min ~0.0001 (nike)
    "article": 0.0002,  # observed min ~0.0004 (wikipedia_ko)
    "listing": 0.00001,  # observed min ~0.000015 (hm)
    "search_results": 0.00002,  # observed min ~0.000036 (uniqlo)
    "form": 0.0002,  # observed min ~0.0004 (github)
    "documentation": 0.0002,
    "landing": 0.00005,
}
DEFAULT_MIN_RATIO = 0.00001

MIN_PRUNED_TOKENS: dict[str, int] = {
    "product_detail": 10,
    "article": 50,
    "listing": 5,
    "search_results": 5,
    "form": 20,
    "documentation": 20,
    "landing": 5,
}
DEFAULT_MIN_TOKENS = 5

MIN_INTERACTABLE_SNR = 0.7

_VALID_AFFORDANCES = {"click", "type", "select", "toggle"}

_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")

# ---------------------------------------------------------------------------
# Snapshot discovery
# ---------------------------------------------------------------------------

_SNAPSHOTS_DIR = Path(__file__).parent.parent / "data" / "snapshots"


def _discover_snapshots() -> list[tuple[str, str, str, Path]]:
    """Yield (site_id, page_type, page_id, snap_dir) from data/snapshots/.

    Handles both ``{type}_page_{v}`` (e.g. ``product_detail_page_000``)
    and bare ``page_{v}`` naming (reads snapshot.json or falls back to
    ``"unknown"``).
    """
    if not _SNAPSHOTS_DIR.exists():
        return []

    results: list[tuple[str, str, str, Path]] = []
    for site_dir in sorted(_SNAPSHOTS_DIR.iterdir()):
        if not site_dir.is_dir():
            continue
        site_id = site_dir.name
        for page_dir in sorted(site_dir.iterdir()):
            if not page_dir.is_dir():
                continue
            raw_path = page_dir / "raw.html"
            if not raw_path.exists():
                continue

            dir_name = page_dir.name
            page_id = dir_name

            # Extract page_type from directory name or snapshot metadata
            if "_page_" in dir_name and not dir_name.startswith("page_"):
                page_type = dir_name.rsplit("_page_", 1)[0]
            else:
                # Bare page_NNN — try snapshot.json
                meta_path = page_dir / "snapshot.json"
                page_type = "unknown"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text("utf-8"))
                        page_type = meta.get("page_type", "unknown")
                    except (json.JSONDecodeError, OSError):
                        pass

            results.append((site_id, page_type, page_id, page_dir))
    return results


_SNAPSHOT_PARAMS = _discover_snapshots()

# ---------------------------------------------------------------------------
# PageMap cache — build_page_map_offline is pure; cache across tests
# ---------------------------------------------------------------------------

_PAGE_MAP_CACHE: dict[tuple[str, str], PageMap] = {}


def _get_or_build(raw_html: str, url: str, site_id: str, page_id: str, **kw) -> PageMap:
    """Lazily cache build_page_map_offline results."""
    key = (site_id, page_id)
    if key not in _PAGE_MAP_CACHE:
        _PAGE_MAP_CACHE[key] = build_page_map_offline(
            raw_html=raw_html, url=url, site_id=site_id, page_id=page_id, **kw
        )
    return _PAGE_MAP_CACHE[key]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _compute_extraction_ratio(page_map: PageMap, raw_html: str) -> float:
    """pruned_tokens / count_tokens(raw_html), guard against div-by-zero."""
    raw_tokens = count_tokens(raw_html)
    if raw_tokens == 0:
        return 0.0
    return page_map.pruned_tokens / raw_tokens


def _compute_interactable_snr(page_map: PageMap) -> float:
    """Fraction of interactables with non-empty .name field."""
    total = len(page_map.interactables)
    if total == 0:
        return 1.0
    named = sum(1 for i in page_map.interactables if i.name.strip())
    return named / total


_BOUNDARY_TAG_LINE_RE = re.compile(r"^</?web_content_[0-9a-f]+.*>$", re.MULTILINE)


def _strip_boundary_tags(text: str) -> str:
    """Remove <web_content_*> open/close tag lines."""
    return _BOUNDARY_TAG_LINE_RE.sub("", text)


def _has_entity_leaks(text: str) -> bool:
    """Check for HTML entities using _ENTITY_RE regex."""
    return bool(_ENTITY_RE.search(text))


def _load_snapshot(snap_dir: Path) -> tuple[str, str]:
    """Return (raw_html, url) from a snapshot directory."""
    raw_html = (snap_dir / "raw.html").read_text("utf-8")
    url = "offline://unknown"
    meta_path = snap_dir / "snapshot.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            url = meta.get("url", url)
        except (json.JSONDecodeError, OSError):
            pass
    return raw_html, url


# ---------------------------------------------------------------------------
# Synthetic HTML fixtures
# ---------------------------------------------------------------------------

PRODUCT_PAGE_HTML = """\
<html><head><title>Premium Leather Jacket - Fashion Store</title></head><body>
<nav><a href="/">Home</a> <a href="/men">Men</a> <a href="/women">Women</a>
<a href="/sale">Sale</a></nav>
<main>
<h1>Premium Leather Jacket</h1>
<p class="price">$189.00 <span class="original">$259.00</span></p>
<p>Crafted from genuine Italian leather with a modern slim fit design.
Features a full zip closure, two front pockets, and an interior pocket.
The jacket is lined with soft cotton for comfort in all seasons.
Available in black, brown, and cognac. Perfect for casual and semi-formal occasions.
This premium jacket combines classic style with contemporary tailoring for a
versatile addition to any wardrobe.</p>
<label for="size">Size</label>
<select id="size" name="size"><option>S</option><option>M</option>
<option>L</option><option>XL</option></select>
<button type="submit">Add to Cart</button>
<div class="reviews">4.6 stars (847 reviews)</div>
</main>
<footer><p>Copyright 2026 Fashion Store. All rights reserved.
Contact us at support@example.com. Terms of Service. Privacy Policy.</p></footer>
</body></html>"""

ARTICLE_PAGE_HTML = """\
<html><head><title>Climate Change Impact on Agriculture - News Daily</title></head><body>
<nav><a href="/">Home</a> <a href="/politics">Politics</a>
<a href="/science">Science</a> <a href="/tech">Technology</a></nav>
<main>
<article>
<h1>Climate Change Impact on Global Agriculture</h1>
<p class="byline">By Jane Smith | Published February 24, 2026</p>
<p>Rising global temperatures are fundamentally reshaping agricultural practices
across the world. According to a new report from the United Nations Food and
Agriculture Organization, crop yields in tropical regions have declined by an
average of 5.3 percent over the past decade.</p>
<p>The report highlights several key findings. First, water scarcity is
becoming the primary constraint on food production in South Asia and
sub-Saharan Africa. Second, shifting weather patterns have disrupted
traditional planting calendars, forcing farmers to adapt their techniques.
Third, new pest species are expanding their ranges into previously
unaffected regions.</p>
<p>Experts emphasize that adaptation strategies must be implemented at
both local and national levels. Investment in drought-resistant crop
varieties and modern irrigation systems could offset some of the projected
losses. However, without significant reductions in greenhouse gas emissions,
the long-term outlook remains challenging for global food security.</p>
</article>
</main>
<aside><h3>Related Articles</h3><ul>
<li><a href="/article/1">Water Crisis in South Asia</a></li>
<li><a href="/article/2">New Drought-Resistant Wheat Variety</a></li>
</ul></aside>
<footer><p>News Daily 2026. Editorial Policy. Contact the Newsroom.</p></footer>
</body></html>"""

ENTITY_LEAK_HTML = """\
<html><head><title>Entity Test Page</title></head><body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<main>
<h1>Product Details &amp; Specifications</h1>
<p>Price:&nbsp;$99.99&nbsp;(tax&nbsp;included)</p>
<p>This product comes with a 2-year warranty&#8212;no questions asked.
The manufacturer&apos;s suggested retail price is $129.99.</p>
<p>Features include: high-quality materials &amp; craftsmanship,
water-resistant coating, and a lightweight design.
Dimensions: 10&quot; x 8&quot; x 3&quot;. Weight: 2.5 lbs.</p>
<p>For questions, email support@example.com or call 1-800-555-0123.
Available in stores &amp; online. Ships within 3&#8211;5 business days.</p>
</main>
<footer><p>Copyright &copy; 2026 Example Corp. All rights reserved.</p></footer>
</body></html>"""

CONTENT_PAGE_HTML = """\
<html><head><title>Getting Started Guide - Developer Docs</title></head><body>
<nav><a href="/">Docs Home</a> <a href="/api">API Reference</a>
<a href="/guides">Guides</a></nav>
<main>
<h1>Getting Started</h1>
<h2>Installation</h2>
<p>Install the package using pip. The package requires Python 3.10 or later
and has minimal dependencies. We recommend using a virtual environment.</p>
<pre><code>pip install example-package</code></pre>
<h2>Configuration</h2>
<p>Create a configuration file in your project root. The configuration
supports JSON and YAML formats. At minimum, you need to specify the
API endpoint and authentication credentials.</p>
<pre><code>{"api_url": "https://api.example.com", "api_key": "your-key"}</code></pre>
<h2>Basic Usage</h2>
<p>Import the client and create an instance with your configuration.
The client handles authentication, retries, and connection pooling
automatically. All API methods return typed response objects.</p>
<pre><code>from example import Client
client = Client(config_path="config.json")
result = client.query("hello world")</code></pre>
</main>
<footer><p>Developer Documentation v2.1. Last updated February 2026.</p></footer>
</body></html>"""

IMG_ENTITY_URL_HTML = """\
<html><head><title>Image URL Entity Test</title></head><body>
<main>
<h1>Product with entity URLs</h1>
<p>Description text long enough to avoid error page classification.
This product features high quality materials and modern design.</p>
<img src="https://cdn.example.com/img.jpg?w=800&amp;h=600&amp;fit=crop" alt="Product">
<img src="https://cdn.example.com/img2.jpg?format=auto&amp;quality=80" alt="Detail">
</main></body></html>"""


# ---------------------------------------------------------------------------
# Category A: Synthetic quality tests (always run)
# ---------------------------------------------------------------------------


class TestSyntheticQuality:
    """Quality gates using inline HTML fixtures — no snapshot data required."""

    def test_product_page_extraction_ratio(self):
        pm = _get_or_build(
            PRODUCT_PAGE_HTML,
            "https://example.com/product/123",
            "synthetic",
            "product_000",
            page_type="product_detail",
        )
        ratio = _compute_extraction_ratio(pm, PRODUCT_PAGE_HTML)
        min_ratio = MIN_EXTRACTION_RATIO["product_detail"]
        assert ratio >= min_ratio, f"product extraction ratio {ratio:.6f} < min {min_ratio}"

    def test_article_page_extraction_ratio(self):
        pm = _get_or_build(
            ARTICLE_PAGE_HTML,
            "https://example.com/article/climate",
            "synthetic",
            "article_000",
            page_type="article",
        )
        ratio = _compute_extraction_ratio(pm, ARTICLE_PAGE_HTML)
        min_ratio = MIN_EXTRACTION_RATIO["article"]
        assert ratio >= min_ratio, f"article extraction ratio {ratio:.6f} < min {min_ratio}"

    def test_no_html_entities_in_prompt(self):
        pm = _get_or_build(
            ENTITY_LEAK_HTML,
            "https://example.com/entities",
            "synthetic",
            "entity_000",
            page_type="product_detail",
        )
        # Check pruned_context directly (no boundary wrapper)
        assert not _has_entity_leaks(pm.pruned_context), (
            f"entity leak in pruned_context: {_ENTITY_RE.findall(pm.pruned_context)[:5]}"
        )
        # Check full prompt with boundary tags stripped
        prompt = to_agent_prompt(pm)
        stripped = _strip_boundary_tags(prompt)
        assert not _has_entity_leaks(stripped), (
            f"entity leak in prompt (boundary-stripped): {_ENTITY_RE.findall(stripped)[:5]}"
        )

    def test_image_urls_no_entities(self):
        """Image URLs extracted from HTML must not contain raw &amp; entities."""
        imgs, _stats = extract_product_images(
            IMG_ENTITY_URL_HTML,
            "https://example.com/product/entity-test",
        )
        for url in imgs:
            assert "&amp;" not in url, f"image URL contains &amp; entity: {url}"
        # Verify we actually extracted something
        assert len(imgs) >= 1, "expected at least 1 image URL"

    def test_product_page_extracts_interactables(self):
        """Product page with button + select must produce interactables."""
        pm = _get_or_build(
            PRODUCT_PAGE_HTML,
            "https://example.com/product/123",
            "synthetic",
            "product_000",
            page_type="product_detail",
        )
        assert len(pm.interactables) >= 1, "expected at least 1 interactable"
        roles = {i.role for i in pm.interactables}
        assert "button" in roles, f"expected a button interactable, got roles: {roles}"

    def test_interactable_fields_valid(self):
        """Every interactable must have valid role, name, and affordance."""
        pm = _get_or_build(
            PRODUCT_PAGE_HTML,
            "https://example.com/product/123",
            "synthetic",
            "product_000",
            page_type="product_detail",
        )
        for elem in pm.interactables:
            assert elem.role.strip(), f"interactable ref={elem.ref} has empty role"
            assert elem.name.strip(), f"interactable ref={elem.ref} ({elem.role}) has empty name"
            assert elem.affordance in _VALID_AFFORDANCES, (
                f"interactable ref={elem.ref} ({elem.role}) has invalid "
                f"affordance '{elem.affordance}', expected one of {_VALID_AFFORDANCES}"
            )

    def test_nonempty_pruned_context(self):
        pm = _get_or_build(
            CONTENT_PAGE_HTML,
            "https://example.com/docs/getting-started",
            "synthetic",
            "content_000",
            page_type="documentation",
        )
        assert pm.pruned_context.strip(), "pruned_context is empty"
        assert pm.pruned_tokens > 0, f"pruned_tokens is {pm.pruned_tokens}, expected > 0"


# ---------------------------------------------------------------------------
# Category B: Snapshot quality tests (require data/snapshots/)
# ---------------------------------------------------------------------------


def _snapshot_id(param):
    """Generate readable test IDs for parametrized snapshot tests."""
    site_id, _page_type, page_id, _snap_dir = param
    return f"{site_id}/{page_id}"


@pytest.mark.snapshot
class TestSnapshotQuality:
    """Quality gates over real-world snapshot data."""

    @pytest.mark.parametrize("param", _SNAPSHOT_PARAMS, ids=_snapshot_id)
    def test_snapshot_extraction_ratio(self, param):
        site_id, page_type, page_id, snap_dir = param
        raw_html, url = _load_snapshot(snap_dir)
        pm = _get_or_build(raw_html, url, site_id, page_id, page_type=page_type)
        ratio = _compute_extraction_ratio(pm, raw_html)
        min_ratio = MIN_EXTRACTION_RATIO.get(page_type, DEFAULT_MIN_RATIO)
        min_tokens = MIN_PRUNED_TOKENS.get(page_type, DEFAULT_MIN_TOKENS)
        assert ratio >= min_ratio, (
            f"extraction ratio {ratio:.6f} < min {min_ratio} "
            f"for {site_id}/{page_id} (type={page_type}, "
            f"pruned={pm.pruned_tokens}, raw={count_tokens(raw_html)})"
        )
        assert pm.pruned_tokens >= min_tokens, (
            f"pruned_tokens {pm.pruned_tokens} < min {min_tokens} for {site_id}/{page_id} (type={page_type})"
        )

    @pytest.mark.parametrize("param", _SNAPSHOT_PARAMS, ids=_snapshot_id)
    def test_snapshot_interactable_snr(self, param):
        site_id, page_type, page_id, snap_dir = param
        raw_html, url = _load_snapshot(snap_dir)
        pm = _get_or_build(raw_html, url, site_id, page_id, page_type=page_type)
        if len(pm.interactables) == 0:
            pytest.skip(f"{site_id}/{page_id}: 0 interactables")
        snr = _compute_interactable_snr(pm)
        assert snr >= MIN_INTERACTABLE_SNR, (
            f"interactable SNR {snr:.2f} < min {MIN_INTERACTABLE_SNR} "
            f"for {site_id}/{page_id} ({len(pm.interactables)} total)"
        )

    @pytest.mark.parametrize("param", _SNAPSHOT_PARAMS, ids=_snapshot_id)
    def test_snapshot_no_entity_leaks(self, param):
        site_id, page_type, page_id, snap_dir = param
        raw_html, url = _load_snapshot(snap_dir)
        pm = _get_or_build(raw_html, url, site_id, page_id, page_type=page_type)
        # Check pruned_context directly
        assert not _has_entity_leaks(pm.pruned_context), (
            f"entity leak in pruned_context for {site_id}/{page_id}: {_ENTITY_RE.findall(pm.pruned_context)[:5]}"
        )
        # Check prompt with boundary tags stripped
        prompt = to_agent_prompt(pm)
        stripped = _strip_boundary_tags(prompt)
        assert not _has_entity_leaks(stripped), (
            f"entity leak in prompt for {site_id}/{page_id}: {_ENTITY_RE.findall(stripped)[:5]}"
        )

    @pytest.mark.parametrize("param", _SNAPSHOT_PARAMS, ids=_snapshot_id)
    def test_snapshot_images_not_all_tracking(self, param):
        site_id, page_type, page_id, snap_dir = param
        raw_html, url = _load_snapshot(snap_dir)
        pm = _get_or_build(raw_html, url, site_id, page_id, page_type=page_type)
        if not pm.images:
            pytest.skip(f"{site_id}/{page_id}: no images")
        tracking_count = sum(1 for img_url in pm.images if _EXCLUDE_IMG_PATTERNS.search(img_url))
        assert tracking_count < len(pm.images), (
            f"ALL {len(pm.images)} images match tracking patterns for {site_id}/{page_id}"
        )
