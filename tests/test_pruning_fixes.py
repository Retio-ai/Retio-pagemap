"""Tests for P1 pruning information loss fixes.

Covers 4 issues:
  1. FORM chunks kept in main / no-main
  2. aside filter sidebar recognition via interactive descendants
  3. High-value short text (availability, shipping, scarcity, discount)
  4. MEDIA chunks kept in main / no-main
  + Regression: existing behaviour preserved
"""

from __future__ import annotations

import lxml.html
import pytest

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.aom_filter import (
    _compute_weight,
    _count_content_matches,
    _has_interactive_descendants,
)
from pagemap.pruning.pruner import (
    PruneDecision,
    _is_high_value_short_text,
    _xpath_common_depth,
    prune_chunks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    in_main: bool = True,
    tag: str = "div",
    attrs: dict | None = None,
) -> HtmlChunk:
    return HtmlChunk(
        xpath="/html/body/main/div[1]" if in_main else "/html/body/div[1]",
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


def _parse_el(html_str: str) -> lxml.html.HtmlElement:
    """Parse an HTML fragment and return the first element inside body."""
    doc = lxml.html.fromstring(f"<html><body>{html_str}</body></html>")
    body = doc.find(".//body")
    assert body is not None
    children = list(body)
    assert len(children) >= 1
    return children[0]


# ===========================================================================
# Issue 1: FORM chunks
# ===========================================================================


class TestFormChunks:
    def test_form_in_main_kept(self):
        """FORM in main is always kept regardless of text length."""
        chunk = _make_chunk("Login", ChunkType.FORM, in_main=True, tag="form")
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert decision.reason == "in-main-form"

    def test_form_in_main_empty_text_kept(self):
        """Even empty-ish FORM in main is kept (functional form)."""
        chunk = _make_chunk("", ChunkType.FORM, in_main=True, tag="form")
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert decision.reason == "in-main-form"

    def test_form_no_main_long_text_kept(self):
        """FORM with >20 chars on no-main page is kept."""
        chunk = _make_chunk(
            "Search by category and price range",
            ChunkType.FORM,
            in_main=False,
            tag="form",
        )
        decision = _prune_single(chunk, has_main=False)
        assert decision.keep is True
        assert decision.reason == "keep-form-no-main"

    def test_form_no_main_short_text_pruned(self):
        """FORM with <=20 chars on no-main page is pruned."""
        chunk = _make_chunk("Go", ChunkType.FORM, in_main=False, tag="form")
        decision = _prune_single(chunk, has_main=False)
        assert decision.keep is False


# ===========================================================================
# Issue 2: aside filter sidebar
# ===========================================================================


class TestAsideFilterSidebar:
    def test_interactive_descendants_with_select(self):
        el = _parse_el("<aside><select><option>A</option></select></aside>")
        assert _has_interactive_descendants(el) is True

    def test_interactive_descendants_with_input(self):
        el = _parse_el('<aside><input type="text" /></aside>')
        assert _has_interactive_descendants(el) is True

    def test_interactive_descendants_with_textarea(self):
        el = _parse_el("<aside><textarea></textarea></aside>")
        assert _has_interactive_descendants(el) is True

    def test_hidden_input_only_not_interactive(self):
        el = _parse_el('<aside><input type="hidden" name="csrf" /></aside>')
        assert _has_interactive_descendants(el) is False

    def test_links_only_not_interactive(self):
        el = _parse_el('<aside><a href="/related">Product A</a></aside>')
        assert _has_interactive_descendants(el) is False

    def test_aside_with_filter_controls_high_weight(self):
        el = _parse_el('<aside><select><option>Price</option></select><input type="checkbox" />Filter</aside>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "filter-sidebar" in reason

    def test_aside_without_controls_low_weight(self):
        el = _parse_el('<aside><a href="/p1">Related Product 1</a><a href="/p2">Related Product 2</a></aside>')
        weight, reason = _compute_weight(el)
        assert weight < 0.5

    def test_complementary_role_with_controls_high_weight(self):
        el = _parse_el('<div role="complementary"><input type="text" placeholder="Filter" /></div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "filter-sidebar" in reason

    def test_complementary_role_without_controls_low_weight(self):
        el = _parse_el('<div role="complementary"><a href="/link">Link</a></div>')
        weight, reason = _compute_weight(el)
        assert weight < 0.5


# ===========================================================================
# Issue 3: High-value short text
# ===========================================================================


class TestHighValueShortText:
    @pytest.mark.parametrize(
        "text",
        [
            "In stock",
            "Out of stock",
            "Sold out",
            "Available",
            "품절",
            "재고",
            "在庫",
            "品切れ",
            "épuisé",
            "auf lager",
        ],
    )
    def test_availability_terms_detected(self, text: str):
        assert _is_high_value_short_text(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Free shipping",
            "Free delivery",
            "무료배송",
            "무료 배송",
            "送料無料",
            "livraison gratuite",
            "kostenloser versand",
        ],
    )
    def test_shipping_terms_detected(self, text: str):
        assert _is_high_value_short_text(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Only 3 left",
            "Just 5 remaining",
            "남은 2",
            "残り3",
            "seulement 1",
            "nur 2",
        ],
    )
    def test_scarcity_detected(self, text: str):
        assert _is_high_value_short_text(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "30% off",
            "50% 할인",
            "20% sale",
            "10% discount",
            "15% remise",
            "25% rabatt",
            "30% 割引",
            "25% Preisnachlass",
        ],
    )
    def test_discount_detected(self, text: str):
        assert _is_high_value_short_text(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Click here",
            "Menu",
            "Home",
            "More",
            "OK",
            "100% cotton",
        ],
    )
    def test_noise_not_detected(self, text: str):
        assert _is_high_value_short_text(text) is False

    def test_short_availability_kept_in_main(self):
        """Short 'In stock' text in main is kept via high-value pattern."""
        chunk = _make_chunk("In stock", ChunkType.TEXT_BLOCK, in_main=True)
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert "high-value" in decision.reason

    def test_short_shipping_kept_in_main(self):
        chunk = _make_chunk("Free shipping", ChunkType.TEXT_BLOCK, in_main=True)
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert "high-value" in decision.reason

    def test_short_noise_pruned_in_main(self):
        """Short noise text like 'Click here' is still pruned."""
        chunk = _make_chunk("Click here", ChunkType.TEXT_BLOCK, in_main=True)
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is False
        assert decision.reason == "in-main-short"

    def test_korean_availability_kept(self):
        chunk = _make_chunk("품절", ChunkType.TEXT_BLOCK, in_main=True)
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True

    def test_high_value_short_list_kept_in_main(self):
        """LIST chunk with short high-value text is kept."""
        chunk = _make_chunk("Only 3 left", ChunkType.LIST, in_main=True, tag="ul")
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert "high-value" in decision.reason


# ===========================================================================
# Issue 4: MEDIA chunks
# ===========================================================================


class TestMediaChunks:
    def test_media_in_main_meaningful_caption_kept(self):
        """MEDIA with figcaption >10 chars in main is kept."""
        chunk = _make_chunk(
            "Product front view in natural light",
            ChunkType.MEDIA,
            in_main=True,
            tag="figure",
        )
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert decision.reason == "in-main-media"

    def test_media_in_main_short_caption_pruned(self):
        """MEDIA with <=10 chars in main is pruned (decorative)."""
        chunk = _make_chunk("Photo", ChunkType.MEDIA, in_main=True, tag="figure")
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is False
        assert decision.reason == "in-main-short"

    def test_media_no_main_long_caption_kept(self):
        """MEDIA with >20 chars on no-main page is kept."""
        chunk = _make_chunk(
            "High-resolution product image with zoom",
            ChunkType.MEDIA,
            in_main=False,
            tag="figure",
        )
        decision = _prune_single(chunk, has_main=False)
        assert decision.keep is True
        assert decision.reason == "keep-media-no-main"

    def test_media_no_main_short_caption_pruned(self):
        """MEDIA with <=20 chars on no-main page is pruned."""
        chunk = _make_chunk("Thumbnail", ChunkType.MEDIA, in_main=False, tag="figure")
        decision = _prune_single(chunk, has_main=False)
        assert decision.keep is False


# ===========================================================================
# P2.1: Coupang price filter xpath proximity
# ===========================================================================


class TestXPathCommonDepth:
    @pytest.mark.parametrize(
        "xpath1,xpath2,expected",
        [
            ("/html/body/div[1]/span[1]", "/html/body/div[1]/span[2]", 3),
            ("/html/body/div[1]", "/html/body/div[5]", 2),
            ("/html/body/div[1]/div[2]/p", "/html/body/div[1]/div[2]/span", 4),
            ("/html/body/div[1]", "/html/body/div[1]", 3),
            ("/html/body/div[1]", "/html/section/div[1]", 1),
        ],
    )
    def test_common_depth(self, xpath1, xpath2, expected):
        assert _xpath_common_depth(xpath1, xpath2) == expected


class TestCoupangPriceFilter:
    def _make_price_chunk(self, xpath: str, in_main: bool = False) -> HtmlChunk:
        return HtmlChunk(
            xpath=xpath,
            html="<span>189,000원</span>",
            text="189,000원",
            tag="span",
            chunk_type=ChunkType.TEXT_BLOCK,
            attrs={},
            parent_xpath="/".join(xpath.split("/")[:-1]),
            depth=len(xpath.split("/")) - 1,
            in_main=in_main,
        )

    def test_bundle_prices_same_container_kept(self):
        """5 prices in same container (/html/body/div[1]/...) → all kept."""
        chunks = [self._make_price_chunk(f"/html/body/div[1]/span[{i}]") for i in range(1, 6)]
        results = prune_chunks(chunks, schema_name="Product", has_main=False)
        for _, decision in results:
            assert decision.keep is True

    def test_recommendation_prices_different_container_filtered(self):
        """Prices in different top-level divs → filtered after 3rd."""
        chunks = [
            self._make_price_chunk("/html/body/div[1]/span[1]"),
            self._make_price_chunk("/html/body/div[1]/span[2]"),
            self._make_price_chunk("/html/body/div[1]/span[3]"),
            # These are in a different container
            self._make_price_chunk("/html/body/div[5]/span[1]"),
            self._make_price_chunk("/html/body/div[5]/span[2]"),
        ]
        results = prune_chunks(chunks, schema_name="Product", has_main=False)
        kept = [i for i, (_, d) in enumerate(results) if d.keep]
        filtered = [i for i, (_, d) in enumerate(results) if not d.keep]
        assert len(kept) >= 3  # first 3 kept
        assert len(filtered) >= 1  # at least some filtered

    def test_old_new_price_pair_kept(self):
        """Only 2 prices (old/new pair) → below threshold → all kept."""
        chunks = [
            self._make_price_chunk("/html/body/div[1]/span[1]"),
            self._make_price_chunk("/html/body/div[1]/span[2]"),
        ]
        results = prune_chunks(chunks, schema_name="Product", has_main=False)
        for _, decision in results:
            assert decision.keep is True

    def test_in_main_prices_always_kept(self):
        """Prices in <main> → always kept regardless of count."""
        chunks = [self._make_price_chunk(f"/html/body/main/div[1]/span[{i}]", in_main=True) for i in range(1, 7)]
        results = prune_chunks(chunks, schema_name="Product", has_main=True)
        for _, decision in results:
            assert decision.keep is True

    def test_non_product_schema_no_filter(self):
        """Non-Product schema → price filter not applied."""
        chunks = [self._make_price_chunk(f"/html/body/div[{i}]/span[1]") for i in range(1, 7)]
        results = prune_chunks(chunks, schema_name="NewsArticle", has_main=False)
        # NewsArticle doesn't match price fields the same way
        # Just verify no coupang-recommendation-filter reason
        for _, decision in results:
            assert "coupang-recommendation" not in decision.reason


# ===========================================================================
# P2.2: News h2 → section_heading
# ===========================================================================


class TestNewsHeadlineH2Fix:
    def test_h1_matches_headline(self):
        chunk = _make_chunk("Breaking News Title", ChunkType.HEADING, tag="h1")
        decision = _prune_single(chunk, schema="NewsArticle", has_main=True)
        assert decision.keep is True
        assert "headline" in decision.matched_fields

    def test_h2_matches_section_heading_not_headline(self):
        chunk = _make_chunk("Related Stories", ChunkType.HEADING, tag="h2")
        decision = _prune_single(chunk, schema="NewsArticle", has_main=True)
        assert decision.keep is True
        assert "section_heading" in decision.matched_fields
        assert "headline" not in decision.matched_fields

    def test_h2_still_kept_in_news(self):
        """h2 is still kept (section_heading is a valid schema match)."""
        chunk = _make_chunk("Sub Section Title", ChunkType.HEADING, tag="h2")
        decision = _prune_single(chunk, schema="NewsArticle", has_main=True)
        assert decision.keep is True

    def test_h1_with_itemprop_headline(self):
        chunk = _make_chunk(
            "Main Headline",
            ChunkType.HEADING,
            tag="h1",
            attrs={"itemprop": "headline"},
        )
        decision = _prune_single(chunk, schema="NewsArticle", has_main=True)
        assert "headline" in decision.matched_fields

    def test_h2_with_itemprop_headline_no_dual_classification(self):
        """h2 with itemprop=headline → section_heading only, not headline."""
        chunk = _make_chunk(
            "Sub Headline",
            ChunkType.HEADING,
            tag="h2",
            attrs={"itemprop": "headline"},
        )
        decision = _prune_single(chunk, schema="NewsArticle", has_main=True)
        assert "section_heading" in decision.matched_fields
        assert "headline" not in decision.matched_fields
        assert decision.keep is True


# ===========================================================================
# P2.7: Content class/ID patterns
# ===========================================================================


class TestContentPatterns:
    def test_article_body_class(self):
        el = _parse_el('<div class="article-body">Long article text</div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "content" in reason

    def test_main_content_class(self):
        el = _parse_el('<div class="main-content">Content here</div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5

    def test_post_body_id(self):
        el = _parse_el('<div id="post-body">Post content</div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5

    def test_entry_content_class(self):
        el = _parse_el('<div class="entry-content">Entry text</div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5

    def test_content_overrides_noise(self):
        """Element with both content and noise classes → content wins."""
        el = _parse_el('<div class="sidebar-content ad-banner">Mixed</div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "content-override" in reason

    def test_pure_noise_still_low(self):
        el = _parse_el('<div class="ad-banner sidebar-promo">Noise</div>')
        weight, reason = _compute_weight(el)
        assert weight < 0.5

    def test_no_class_id_default(self):
        el = _parse_el("<div>Plain text</div>")
        weight, reason = _compute_weight(el)
        assert weight == 1.0
        assert reason == "default"

    def test_tailwind_text_no_false_positive(self):
        """Tailwind classes like 'text-sm' should NOT trigger content match."""
        el = _parse_el('<div class="text-sm text-gray-500">Small text</div>')
        assert _count_content_matches(el) == 0

    def test_postcss_no_false_positive(self):
        """'postcss-config' should NOT match \\bpost\\b."""
        el = _parse_el('<div class="postcss-config">Config</div>')
        assert _count_content_matches(el) == 0


# ===========================================================================
# P2.6: Link density
# ===========================================================================


class TestLinkDensity:
    def test_high_link_density_low_weight(self):
        """Block with >80% link text → weight < 0.5."""
        # 90% of text is in links
        el = _parse_el(
            "<div>"
            '<a href="/a">Link text that is very long and takes most space</a> '
            '<a href="/b">Another long link text here</a> '
            "tiny"
            "</div>"
        )
        weight, reason = _compute_weight(el)
        assert weight < 0.5
        assert "link-density" in reason

    def test_low_link_density_kept(self):
        """Block with <50% link text → weight >= 0.5."""
        el = _parse_el(
            "<div>"
            "This is a paragraph with mostly regular text content that is quite long. "
            "It contains a lot of information. "
            '<a href="/a">one link</a>'
            "</div>"
        )
        weight, _reason = _compute_weight(el)
        assert weight >= 0.5

    def test_short_block_skipped(self):
        """Block with <50 chars → density check skipped."""
        el = _parse_el('<div><a href="/a">Short</a></div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "link-density" not in reason

    def test_non_block_tag_skipped(self):
        """Inline tag (span) → density check not applied."""
        el = _parse_el(
            "<span>"
            '<a href="/a">Link text that is very long and takes most space here</a> '
            '<a href="/b">Another long link</a> '
            "x"
            "</span>"
        )
        weight, reason = _compute_weight(el)
        assert "link-density" not in reason

    def test_density_exactly_at_50_pct_kept(self):
        """Density exactly 0.5 → NOT penalized (threshold is >0.5)."""
        # 50 chars link text + 50 chars plain = 100 total, density=0.5
        link_text = "a" * 50
        plain_text = "b" * 50
        el = _parse_el(f'<div><a href="/x">{link_text}</a>{plain_text}</div>')
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "link-density" not in reason

    def test_density_just_above_80_pct_penalized(self):
        """Density just above 0.8 → high penalty."""
        # 81 chars link + 19 chars plain = 100 total, density=0.81
        link_text = "a" * 81
        plain_text = "b" * 19
        el = _parse_el(f'<div><a href="/x">{link_text}</a>{plain_text}</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.2
        assert "link-density-high" in reason

    def test_content_class_overrides_link_density(self):
        """Element with content class → content pattern returns first, skipping density."""
        el = _parse_el(
            '<div class="article-content">'
            '<a href="/a">Link text that is very long and takes most space</a> '
            '<a href="/b">Another link</a> '
            "x"
            "</div>"
        )
        weight, reason = _compute_weight(el)
        assert weight >= 0.5
        assert "content" in reason


# ===========================================================================
# Regression tests
# ===========================================================================


class TestRegression:
    def test_heading_still_kept_in_main(self):
        chunk = _make_chunk("Product Details", ChunkType.HEADING, in_main=True, tag="h2")
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert decision.reason == "in-main-heading"

    def test_long_text_still_kept_in_main(self):
        chunk = _make_chunk(
            "This is a long paragraph with more than fifty characters in it for testing.",
            ChunkType.TEXT_BLOCK,
            in_main=True,
        )
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert decision.reason == "in-main-text"

    def test_meta_always_kept(self):
        chunk = _make_chunk("og:title", ChunkType.META, in_main=False)
        decision = _prune_single(chunk, has_main=True)
        assert decision.keep is True
        assert "meta" in decision.reason

    def test_heading_kept_no_main(self):
        chunk = _make_chunk("Title", ChunkType.HEADING, in_main=False, tag="h2")
        decision = _prune_single(chunk, has_main=False)
        assert decision.keep is True

    def test_navigation_role_still_removed(self):
        el = _parse_el('<nav role="navigation"><a href="/">Home</a></nav>')
        weight, _ = _compute_weight(el)
        assert weight < 0.5

    def test_related_products_aside_still_removed(self):
        """aside with only links (related products) is still removed."""
        el = _parse_el('<aside><a href="/p1">Product 1</a><a href="/p2">Product 2</a></aside>')
        weight, _ = _compute_weight(el)
        assert weight < 0.5
