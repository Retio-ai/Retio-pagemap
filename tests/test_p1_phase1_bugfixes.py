"""Tests for P1 Phase 1 bug fixes and safety improvements.

Covers 6 issues:
  1.1: XPath index numeric sort (_xpath_sort_key)
  1.2: Skip empty chunks from schema match
  1.3: Warn on unknown schema_name in prune_chunks()
  1.4: Defensive try/except around pagination int()
  8.1: URL allowlist (http/https/relative only) + length limit
  8.2: _decompose_element max_depth recursion guard
"""

from __future__ import annotations

import logging

import lxml.html
import pytest

from pagemap.pruned_context_builder import (
    _extract_pagination_info,
    extract_pagination_structured,
    extract_product_images,
)
from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.compressor import _xpath_sort_key, remerge_chunks
from pagemap.pruning.preprocessor import _decompose_element
from pagemap.pruning.pruner import PruneDecision, prune_chunks

# ── Helpers ──────────────────────────────────────────────────────────


def _make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    in_main: bool = True,
    tag: str = "div",
    attrs: dict | None = None,
    xpath: str | None = None,
) -> HtmlChunk:
    if xpath is None:
        xpath = "/html/body/main/div[1]" if in_main else "/html/body/div[1]"
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs=attrs or {},
        parent_xpath="/html/body/main" if in_main else "/html/body",
        depth=3,
        in_main=in_main,
    )


def _prune_single(
    chunk: HtmlChunk,
    schema: str = "Product",
    has_main: bool = True,
) -> PruneDecision:
    results = prune_chunks([chunk], schema_name=schema, has_main=has_main)
    assert len(results) == 1
    return results[0][1]


# ── 1.1 XPath sort key ──────────────────────────────────────────────


class TestXPathSortKey:
    @pytest.mark.parametrize(
        "xpath,expected",
        [
            ("/html/body/div[2]", (("html", 0), ("body", 0), ("div", 2))),
            ("/html/body/div[10]", (("html", 0), ("body", 0), ("div", 10))),
            ("/html/body/div", (("html", 0), ("body", 0), ("div", 0))),
            ("/json-ld[0]", (("json-ld", 0),)),
            ("/og-meta", (("og-meta", 0),)),
        ],
    )
    def test_xpath_sort_key_parsing(self, xpath: str, expected: tuple):
        assert _xpath_sort_key(xpath) == expected

    def test_div2_before_div10(self):
        """div[2] should sort before div[10] numerically."""
        assert _xpath_sort_key("/body/div[2]") < _xpath_sort_key("/body/div[10]")

    def test_remerge_preserves_document_order(self):
        """remerge_chunks should sort div[2] before div[10]."""
        c2 = _make_chunk("second", xpath="/html/body/div[2]")
        c10 = _make_chunk("tenth", xpath="/html/body/div[10]")
        # Pass in reverse order — remerge should fix it
        result = remerge_chunks([c10, c2])
        assert result.index("second") < result.index("tenth")

    def test_remerge_empty_returns_empty(self):
        assert remerge_chunks([]) == ""


# ── 1.2 Empty chunk filter ──────────────────────────────────────────


class TestEmptyChunkFilter:
    def test_empty_product_name_pruned(self):
        """Empty text with product-name class should not be kept."""
        chunk = _make_chunk(
            "",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"class": "product-name"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        # Should not be kept via schema-match (empty text, no content attr)
        assert decision.reason != "schema-match" or decision.keep is False

    def test_nonempty_product_name_kept(self):
        """Non-empty product name should be kept."""
        chunk = _make_chunk(
            "Galaxy S25",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"class": "product-name"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.keep is True

    def test_whitespace_only_pruned(self):
        """Whitespace-only text (already stripped by preprocessor) should not match."""
        chunk = _make_chunk(
            "",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"class": "product-name"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.reason != "schema-match" or decision.keep is False

    def test_content_attr_fallback_kept(self):
        """Empty text but content attr should be kept (e.g. itemprop=price)."""
        chunk = _make_chunk(
            "",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"itemprop": "price", "content": "29900", "class": "product-price"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.keep is True
        assert "schema-match" in decision.reason

    def test_meta_always_kept(self):
        """META chunk with empty text is still kept (Rule 1)."""
        chunk = _make_chunk("", ChunkType.META, in_main=False)
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.keep is True
        assert "meta" in decision.reason


# ── 1.3 Schema name warning ─────────────────────────────────────────


class TestSchemaNameWarning:
    def test_unknown_schema_logs_warning(self, caplog):
        """Unknown schema_name should produce a warning log."""
        chunk = _make_chunk("Some text", ChunkType.TEXT_BLOCK, in_main=True)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.pruner"):
            prune_chunks([chunk], schema_name="UnknownSchema", has_main=True)
        assert any("Unknown schema_name='UnknownSchema'" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "schema",
        ["Product", "NewsArticle", "WikiArticle", "SaaSPage", "GovernmentPage"],
    )
    def test_valid_schemas_no_warning(self, caplog, schema: str):
        """Valid schema names should not produce any warning."""
        chunk = _make_chunk("Some text", ChunkType.TEXT_BLOCK, in_main=True)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.pruner"):
            prune_chunks([chunk], schema_name=schema, has_main=True)
        assert not any("Unknown schema_name" in r.message for r in caplog.records)

    def test_empty_schema_no_warning(self, caplog):
        """Empty string schema should not warn (intentional unspecified)."""
        chunk = _make_chunk("Some text", ChunkType.TEXT_BLOCK, in_main=True)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.pruner"):
            prune_chunks([chunk], schema_name="", has_main=True)
        assert not any("Unknown schema_name" in r.message for r in caplog.records)


# ── 1.4 Pagination defense ──────────────────────────────────────────


class TestPaginationDefense:
    def test_normal_pagination(self):
        html = '<a href="?page=3">3</a><a href="?page=25">25</a>'
        result = _extract_pagination_info(html)
        assert "25" in result

    def test_korean_pagination(self):
        html = '페이지 3 / 25 <a href="?page=1">1</a>'
        result = extract_pagination_structured(html)
        assert result.get("current_page") == 3
        assert result.get("total_pages") == 25

    def test_url_param_pagination(self):
        html = '<a href="/list?page=10">10</a>'
        result = extract_pagination_structured(html)
        assert result.get("total_pages") == 10

    def test_empty_html_no_crash(self):
        result = extract_pagination_structured("")
        assert result == {}

    def test_structured_pagination(self):
        html = 'Page 3 of 25 <a href="?page=1">1</a>'
        result = extract_pagination_structured(html)
        assert result.get("current_page") == 3
        assert result.get("total_pages") == 25


# ── 8.1 URL validation ──────────────────────────────────────────────


class TestURLValidation:
    @pytest.mark.parametrize(
        "src",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "vbscript:msgbox(1)",
            "data:image/gif;base64,R0lGODlh",
            "blob:https://example.com/uuid",
            "file:///etc/passwd",
        ],
    )
    def test_blocked_schemes(self, src: str):
        """Dangerous URL schemes should be filtered out."""
        html = f'<img src="{src}" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    @pytest.mark.parametrize(
        "src",
        [
            "https://example.com/product.jpg",
            "http://example.com/product.jpg",
            "//cdn.example.com/product.jpg",
            "/images/product.jpg",
            "images/product.jpg",
        ],
    )
    def test_allowed_urls(self, src: str):
        """Safe URLs should pass through."""
        html = f'<img src="{src}" />'
        result, _ = extract_product_images(html)
        assert len(result) >= 1

    def test_long_url_blocked(self):
        """URLs exceeding 2048 chars should be blocked."""
        long_url = "https://example.com/" + "a" * 2100
        html = f'<img src="{long_url}" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0


# ── 8.2 Recursion depth limit ───────────────────────────────────────


class TestRecursionDepthLimit:
    def _build_nested_html(self, depth: int) -> str:
        """Build deeply nested div HTML."""
        open_tags = "".join("<div>" for _ in range(depth))
        close_tags = "".join("</div>" for _ in range(depth))
        return f"<html><body>{open_tags}<p>deep text</p>{close_tags}</body></html>"

    def _parse_and_decompose(self, html: str, max_depth: int = 100) -> list[HtmlChunk]:
        parser = lxml.html.HTMLParser(recover=True, encoding="utf-8")
        doc = lxml.html.document_fromstring(html.encode("utf-8"), parser=parser)
        tree = doc.getroottree()
        body = doc.body if doc.body is not None else doc
        return _decompose_element(body, tree, depth=0, max_depth=max_depth)

    def test_shallow_dom_works(self):
        html = self._build_nested_html(5)
        chunks = self._parse_and_decompose(html)
        assert any("deep text" in c.text for c in chunks)

    def test_deep_nesting_truncated_no_crash(self):
        """150-depth nesting with max_depth=50 should not crash."""
        html = self._build_nested_html(150)
        chunks = self._parse_and_decompose(html, max_depth=50)
        # Should NOT find the deep text — it was truncated
        # (may or may not find it depending on exact nesting, but no crash)
        assert isinstance(chunks, list)

    def test_default_max_depth_allows_normal(self):
        """80-depth nesting should work with default max_depth=100."""
        html = self._build_nested_html(80)
        chunks = self._parse_and_decompose(html, max_depth=100)
        assert any("deep text" in c.text for c in chunks)

    def test_depth_exceeded_logs_warning(self, caplog):
        """Exceeding max_depth should produce a warning log."""
        html = self._build_nested_html(20)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.preprocessor"):
            self._parse_and_decompose(html, max_depth=10)
        assert any("Max decomposition depth" in r.message for r in caplog.records)


# ── QR-03 Image Filtering ────────────────────────────────────────────


class TestImageFilteringQR03:
    """QR-03: Image filtering — negative filters, size, semantics, <picture>, dedup."""

    # -- Negative filter: new exclude keywords --

    @pytest.mark.parametrize(
        "keyword",
        [
            "wordmark",
            "tagline",
            "copyright",
            "favicon",
            "badge",
            "shackle",
            "disambig",
            "padlock",
            "protection-shackle",
            "beacon",
            "separator",
            "divider",
        ],
    )
    def test_new_exclude_keywords(self, keyword: str):
        """New exclude keywords should be filtered out."""
        html = f'<img src="https://example.com/{keyword}-image.png" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0, f"Expected {keyword} to be excluded"

    def test_protection_in_product_url_not_excluded(self):
        """Legitimate product URL with 'protection' should NOT be excluded."""
        html = '<img src="https://example.com/screen-protection-case.jpg" alt="Case" />'
        result, _ = extract_product_images(html)
        assert len(result) == 1

    @pytest.mark.parametrize(
        "domain",
        [
            "scorecardresearch.com",
            "doubleclick.net",
            "google-analytics.com",
            "facebook.com/tr",
            "bat.bing.com",
            "amazon-adsystem.com",
        ],
    )
    def test_tracking_domains_excluded(self, domain: str):
        """Known tracking domains should be filtered out."""
        html = f'<img src="https://{domain}/pixel.gif" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0, f"Expected {domain} to be excluded"

    def test_shields_io_badge_excluded(self):
        """shields.io badges should be filtered out."""
        html = '<img src="https://img.shields.io/badge/build-passing-green.svg" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    # -- Size filter: URL path --

    @pytest.mark.parametrize("px", [20, 40, 50])
    def test_url_path_small_size_excluded(self, px: int):
        """URL path with small px size should be filtered out."""
        html = f'<img src="https://upload.wikimedia.org/wikipedia/commons/thumb/{px}px-icon.png" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0, f"Expected {px}px to be excluded"

    def test_url_path_large_size_passes(self):
        """URL path with 100px or larger should pass."""
        html = '<img src="https://upload.wikimedia.org/wikipedia/commons/thumb/100px-photo.jpg" />'
        result, _ = extract_product_images(html)
        assert len(result) == 1

    # -- Size filter: HTML attributes --

    def test_1x1_pixel_by_attrs_excluded(self):
        """1x1 pixel tracking image via HTML attributes should be filtered."""
        html = '<img src="https://example.com/track.gif" width="1" height="1" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_small_width_attr_excluded(self):
        """width=20 image should be filtered."""
        html = '<img src="https://example.com/small.png" width="20" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_normal_size_attr_passes(self):
        """width=200 image should pass."""
        html = '<img src="https://example.com/content.jpg" width="200" height="150" />'
        result, _ = extract_product_images(html)
        assert len(result) == 1

    # -- Semantic filter: decorative images (2026 best practice) --

    def test_role_presentation_excluded(self):
        """role='presentation' marks decorative image → exclude."""
        html = '<img src="https://example.com/decor.png" role="presentation" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_role_none_excluded(self):
        """role='none' marks decorative image → exclude."""
        html = '<img src="https://example.com/decor.png" role="none" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_aria_hidden_excluded(self):
        """aria-hidden='true' marks decorative image → exclude."""
        html = '<img src="https://example.com/decor.png" aria-hidden="true" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_empty_alt_excluded(self):
        """alt='' (empty alt) marks decorative image → exclude."""
        html = '<img src="https://example.com/decor.png" alt="" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_nonempty_alt_passes(self):
        """alt='Product photo' should NOT be excluded."""
        html = '<img src="https://example.com/product.jpg" alt="Product photo" />'
        result, _ = extract_product_images(html)
        assert len(result) == 1

    def test_fetchpriority_high_boosts(self):
        """fetchpriority='high' should boost image to top priority."""
        html = (
            '<img src="https://example.com/secondary.jpg" alt="Secondary" />'
            '<img src="https://example.com/hero.jpg" alt="Hero" fetchpriority="high" />'
        )
        result, _ = extract_product_images(html)
        assert len(result) == 2
        # Hero should be first despite appearing second in DOM
        assert result[0] == "https://example.com/hero.jpg"

    # -- <picture> support (2026 critical) --

    def test_picture_source_srcset_extracted(self):
        """<picture><source srcset> should extract the URL."""
        html = """
        <picture>
            <source srcset="https://example.com/large.jpg 1200w, https://example.com/small.jpg 400w" type="image/jpeg" />
            <img src="https://example.com/fallback.jpg" alt="Product" />
        </picture>
        """
        result, _ = extract_product_images(html)
        # Should include the largest source (1200w) and the fallback
        assert "https://example.com/large.jpg" in result

    def test_picture_fallback_img_included(self):
        """<picture> fallback <img> should also be included."""
        html = """
        <picture>
            <source srcset="https://example.com/large.webp 1200w" type="image/webp" />
            <img src="https://example.com/fallback.jpg" alt="Product" />
        </picture>
        """
        result, _ = extract_product_images(html)
        assert "https://example.com/fallback.jpg" in result

    def test_picture_x_descriptor_picks_highest(self):
        """<picture> srcset with x descriptors should pick the highest density."""
        html = """
        <picture>
            <source srcset="https://example.com/photo-1x.jpg 1x, https://example.com/photo-3x.jpg 3x, https://example.com/photo-2x.jpg 2x" />
            <img src="https://example.com/fallback.jpg" alt="Photo" />
        </picture>
        """
        result, _ = extract_product_images(html)
        assert "https://example.com/photo-3x.jpg" in result

    def test_picture_decorative_fallback_skipped(self):
        """<picture> fallback <img alt=""> should be skipped (decorative)."""
        html = """
        <picture>
            <source srcset="https://example.com/hero.webp 1200w" type="image/webp" />
            <img src="https://example.com/hero-fallback.jpg" alt="" />
        </picture>
        """
        result, _ = extract_product_images(html)
        # Source URL should be extracted, but decorative fallback should be skipped
        assert "https://example.com/hero.webp" in result
        assert "https://example.com/hero-fallback.jpg" not in result

    def test_no_picture_regular_img_works(self):
        """Regular <img> without <picture> should still work."""
        html = '<img src="https://example.com/product.jpg" alt="Product" />'
        result, _ = extract_product_images(html)
        assert result == ["https://example.com/product.jpg"]

    # -- URL deduplication --

    def test_imwidth_dedup(self):
        """H&M-style imwidth variants should deduplicate to one (largest)."""
        widths = [256, 384, 528, 768, 1080, 1536, 2160]
        imgs = "\n".join(f'<img src="https://image.hm.com/product.jpg?imwidth={w}" />' for w in widths)
        result, _ = extract_product_images(imgs)
        assert len(result) == 1
        assert "imwidth=2160" in result[0]

    def test_different_images_not_deduped(self):
        """Different base URLs should NOT be deduplicated."""
        html = """
        <img src="https://cdn.example.com/product-a.jpg?w=800" />
        <img src="https://cdn.example.com/product-b.jpg?w=800" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 2

    def test_dedup_keeps_largest_variant(self):
        """Dedup should keep the variant with the largest size param."""
        html = """
        <img src="https://cdn.example.com/photo.jpg?w=200" />
        <img src="https://cdn.example.com/photo.jpg?w=1200" />
        <img src="https://cdn.example.com/photo.jpg?w=600" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 1
        assert "w=1200" in result[0]

    def test_dedup_merges_hint_flags(self):
        """Dedup should OR-merge hint flags across variants."""
        html = """
        <img src="https://cdn.example.com/product-gallery.jpg?w=200" class="gallery" alt="Product" />
        <img src="https://cdn.example.com/product-gallery.jpg?w=1200" alt="Photo" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 1
        assert "w=1200" in result[0]
        # The hint from the smaller variant (gallery class) should be preserved
        # Verify indirectly: if a non-hint image exists, the hinted one should come first
        html2 = (
            '<img src="https://cdn.example.com/random-photo.jpg" alt="Random" />'
            '<img src="https://cdn.example.com/product-gallery.jpg?w=200" class="gallery" alt="Product" />'
            '<img src="https://cdn.example.com/product-gallery.jpg?w=1200" alt="Photo" />'
        )
        result2, _ = extract_product_images(html2)
        assert len(result2) == 2
        # product-gallery should be first (has merged hint from gallery class)
        assert "product-gallery" in result2[0]

    # -- Integration scenarios --

    def test_wikipedia_scenario(self):
        """Wikipedia: wordmark + shackle + 20px icon excluded, content image kept."""
        html = """
        <img src="https://en.wikipedia.org/static/images/wordmark.svg" />
        <img src="https://upload.wikimedia.org/wikipedia/commons/a/a9/Shackle.svg" />
        <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/20px-disambig.png" />
        <img src="https://upload.wikimedia.org/wikipedia/commons/3/3a/WLF_collage.jpg" alt="Collage" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 1
        assert "WLF_collage" in result[0]

    def test_hm_dedup_scenario(self):
        """H&M: 10 imwidth variants of same image → 1 result (largest)."""
        base = "https://image.hm.com/assets/hm/12/34/product.jpg"
        imgs = "\n".join(
            f'<img src="{base}?imwidth={w}" />' for w in [256, 384, 528, 768, 800, 1080, 1200, 1536, 1800, 2160]
        )
        result, _ = extract_product_images(imgs)
        assert len(result) == 1
        assert "imwidth=2160" in result[0]

    # -- Regression tests: existing filters still work --

    @pytest.mark.parametrize(
        "keyword",
        ["icon", "logo", "banner", "sprite", "spacer", "blank"],
    )
    def test_existing_exclude_patterns_still_work(self, keyword: str):
        """Original exclude patterns should still filter."""
        html = f'<img src="https://example.com/{keyword}.png" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_existing_tracking_ad_patterns(self):
        """ad_ and tracking patterns still filtered."""
        html = """
        <img src="https://example.com/ad_banner.jpg" />
        <img src="https://example.com/tracking.gif" />
        <img src="https://example.com/pixel.gif" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 0

    @pytest.mark.parametrize(
        "src",
        [
            "javascript:alert(1)",
            "vbscript:msgbox(1)",
            "data:image/gif;base64,R0lGODlh",
            "blob:https://example.com/uuid",
            "file:///etc/passwd",
        ],
    )
    def test_security_scheme_regression(self, src: str):
        """Dangerous URL schemes should still be blocked."""
        html = f'<img src="{src}" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_long_url_regression(self):
        """URLs exceeding 2048 chars should still be blocked."""
        long_url = "https://example.com/" + "a" * 2100
        html = f'<img src="{long_url}" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_product_hint_still_prioritized(self):
        """Product-hint images should still be sorted first."""
        html = """
        <img src="https://example.com/random.jpg" alt="random" />
        <img src="https://example.com/product-main.jpg" class="gallery" alt="Product" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 2
        assert "product-main" in result[0] or "gallery" in result[0]

    # -- Amazon scenario --

    def test_amazon_scenario(self):
        """Amazon: flyout/CTA excluded, m.media-amazon.com product image kept."""
        html = """
        <img src="https://images-na.ssl-images-amazon.com/images/G/01/flyout_72dpi.png" />
        <img src="https://images-na.ssl-images-amazon.com/images/G/01/YourPrimePIV_fallback_CTA.jpg" />
        <img src="https://m.media-amazon.com/images/I/81NGS0K9MkL._AC_SL1500_.jpg" alt="AirPods" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 1
        assert "m.media-amazon.com" in result[0]
        assert not any("flyout" in u for u in result)
        assert not any("CTA" in u for u in result)

    # -- <figure> context boost --

    def test_figure_context_boosts_priority(self):
        """<figure>-contained images should get automatic hint boost."""
        html = """
        <img src="https://example.com/random-photo.jpg" alt="Random" />
        <figure>
            <img src="https://example.com/editorial.jpg" alt="Editorial" />
            <figcaption>An editorial image</figcaption>
        </figure>
        """
        result, _ = extract_product_images(html)
        assert len(result) == 2
        # Figure image should be sorted first (has hint boost)
        assert "editorial" in result[0]

    # -- loading="eager" boost --

    def test_loading_eager_boosts_priority(self):
        """loading='eager' images should be prioritized."""
        html = """
        <img src="https://example.com/secondary.jpg" alt="Secondary" />
        <img src="https://example.com/hero.jpg" alt="Hero" loading="eager" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 2
        # Eager-loaded image should be first (has hint boost)
        assert "hero" in result[0]

    # -- SVG filtering --

    def test_svg_without_hint_excluded(self):
        """Generic .svg files should be excluded."""
        html = '<img src="https://example.com/decorative-element.svg" alt="Decor" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_svg_with_product_hint_kept(self):
        """SVG from product-hint domain (e.g. wikimedia) should be kept."""
        html = '<img src="https://upload.wikimedia.org/wikipedia/commons/infographic.svg" alt="Info" />'
        result, _ = extract_product_images(html)
        assert len(result) == 1

    # -- CDN product hints --

    def test_cdn_product_hints_prioritized(self):
        """Major CDN domains should be recognized as product hints."""
        html = """
        <img src="https://example.com/unknown.jpg" alt="Unknown" />
        <img src="https://m.media-amazon.com/images/I/product.jpg" alt="Amazon" />
        <img src="https://thumbnail7.coupangcdn.com/product.jpg" alt="Coupang" />
        <img src="https://image.msscdn.net/goods.jpg" alt="Musinsa" />
        """
        result, _ = extract_product_images(html)
        # CDN images should be prioritized over generic unknown image
        assert "m.media-amazon.com" in result[0] or "coupang" in result[0] or "msscdn" in result[0]

    # -- srcset size validation --

    def test_srcset_all_small_excluded(self):
        """Images where all srcset variants are < 100w should be excluded."""
        html = '<img src="https://example.com/tiny.jpg" srcset="https://example.com/tiny.jpg 50w, https://example.com/tiny2.jpg 80w" alt="Tiny" />'
        result, _ = extract_product_images(html)
        assert len(result) == 0

    def test_srcset_one_large_kept(self):
        """Images where at least one srcset variant >= 100w should be kept."""
        html = '<img src="https://example.com/photo.jpg" srcset="https://example.com/s.jpg 50w, https://example.com/l.jpg 800w" alt="Photo" />'
        result, _ = extract_product_images(html)
        assert len(result) >= 1

    # -- Refined primary hint --

    def test_refined_primary_hint_no_false_positive(self):
        """'primary-color.png' should NOT match product hint (too broad)."""
        html = '<img src="https://example.com/primary-color.png" alt="Color" />'
        result, _ = extract_product_images(html)
        # Should still be in results (not excluded) but NOT have hint boost
        # We test indirectly: if another image without hint exists, order matters
        html2 = """
        <img src="https://example.com/primary-color.png" alt="Color" />
        <img src="https://example.com/product-gallery.jpg" alt="Product" />
        """
        result2, _ = extract_product_images(html2)
        assert len(result2) == 2
        # product-gallery has the hint, should be first
        assert "product-gallery" in result2[0]

    def test_refined_primary_image_hint_matches(self):
        """'primary-image.jpg' should match the refined product hint."""
        html = """
        <img src="https://example.com/random.jpg" alt="Random" />
        <img src="https://example.com/primary-image.jpg" alt="Main" />
        """
        result, _ = extract_product_images(html)
        assert len(result) == 2
        # primary-image should be prioritized
        assert "primary-image" in result[0]


class TestMetadataImageMerge:
    """Tests for _merge_structured_images in page_map_builder."""

    def test_jsonld_image_prepended(self):
        """Structured data image should be prepended to list."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
        metadata = {"image_url": "https://cdn.example.com/structured.jpg"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is True
        assert result[0] == "https://cdn.example.com/structured.jpg"
        assert len(result) == 3

    def test_jsonld_image_dedup_exact(self):
        """Exact duplicate should not be added again."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/photo.jpg"]
        metadata = {"image_url": "https://cdn.example.com/photo.jpg"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is False
        assert len(result) == 1

    def test_jsonld_image_dedup_canonical(self):
        """Resize variant of existing image should be deduped."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/photo.jpg?w=800"]
        metadata = {"image_url": "https://cdn.example.com/photo.jpg?w=1200"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is False
        assert len(result) == 1

    def test_no_metadata_image(self):
        """No image_url in metadata → original list unchanged."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/a.jpg"]
        metadata = {"title": "Test"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is False
        assert result == ["https://cdn.example.com/a.jpg"]

    def test_cap_at_ten(self):
        """Result should never exceed 10 images."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = [f"https://cdn.example.com/{i}.jpg" for i in range(10)]
        metadata = {"image_url": "https://cdn.example.com/extra.jpg"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is True
        assert len(result) == 10
        assert result[0] == "https://cdn.example.com/extra.jpg"

    def test_excluded_metadata_image_rejected(self):
        """Metadata image matching exclude patterns should be rejected."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/a.jpg"]
        metadata = {"image_url": "https://cdn.example.com/logo-main.png"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is False
        assert len(result) == 1

    def test_unsafe_scheme_rejected(self):
        """Metadata image with unsafe scheme should be rejected."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/a.jpg"]
        metadata = {"image_url": "javascript:alert(1)"}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is False

    def test_long_url_rejected(self):
        """Metadata image with excessively long URL should be rejected."""
        from pagemap.page_map_builder import _merge_structured_images

        html_images = ["https://cdn.example.com/a.jpg"]
        metadata = {"image_url": "https://cdn.example.com/" + "x" * 2100}
        result, merged = _merge_structured_images(html_images, metadata)
        assert merged is False


class TestImageFilterTelemetry:
    """Tests for image filter telemetry stats returned by extract_product_images."""

    def test_stats_returned(self):
        """extract_product_images should return filter stats dict."""
        html = '<img src="https://example.com/product.jpg" alt="Product" />'
        result, stats = extract_product_images(html)
        assert isinstance(stats, dict)
        assert "total_candidates" in stats
        assert "after_decorative_filter" in stats
        assert "after_size_attrs_filter" in stats
        assert "after_all_filters" in stats
        assert "after_picture_merge" in stats
        assert "after_dedup" in stats
        assert "final_count" in stats
        assert "structured_image_merged" in stats

    def test_stats_counts_correct(self):
        """Filter stats should reflect actual filtering behavior."""
        html = """
        <img src="https://example.com/product.jpg" alt="Product" />
        <img src="https://example.com/decor.png" alt="" />
        <img src="https://example.com/logo.png" alt="Logo" />
        <img src="https://example.com/content.jpg" alt="Content" />
        """
        result, stats = extract_product_images(html)
        # 4 img tags total
        assert stats["total_candidates"] == 4
        # 1 decorative (alt="") should be filtered
        assert stats["after_decorative_filter"] == 3
        # no size attrs to filter
        assert stats["after_size_attrs_filter"] == 3
        # logo.png excluded by pattern → after_all_filters = 2
        assert stats["after_all_filters"] == 2
        # Final count should match result length
        assert stats["final_count"] == len(result)
        # structured_image_merged defaults to False
        assert stats["structured_image_merged"] is False
