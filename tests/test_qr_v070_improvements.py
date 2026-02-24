# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QR v0.7.0 quality improvements.

Improvement 1: Article content extraction (AOM filter + article compressor)
Improvement 2: Video page type + metadata
Improvement 3: Amazon price extraction
"""

from __future__ import annotations

import json

import lxml.html

from pagemap.metadata import extract_metadata
from pagemap.page_classifier import classify_page
from pagemap.page_map_builder import detect_schema
from pagemap.pruned_context_builder import _compress_for_article, _compress_for_product, _compress_for_video
from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.aom_filter import _compute_weight, _is_inside_article_or_main, aom_filter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html(body: str, head: str = "") -> str:
    head_section = f"<head>{head}</head>" if head else ""
    return f"<html>{head_section}<body>{body}</body></html>"


def _meta_chunk(text: str, attrs: dict | None = None) -> HtmlChunk:
    return HtmlChunk(
        xpath="/html/head/script",
        html="",
        text=text,
        tag="script",
        chunk_type=ChunkType.META,
        attrs=attrs or {},
    )


def _heading_chunk(tag: str, text: str, attrs: dict | None = None) -> HtmlChunk:
    return HtmlChunk(
        xpath=f"/html/body/{tag}",
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=ChunkType.HEADING,
        attrs=attrs or {},
    )


def _og_meta_chunk(og_attrs: dict) -> HtmlChunk:
    return HtmlChunk(
        xpath="/html/head/meta",
        html="",
        text="",
        tag="meta",
        chunk_type=ChunkType.META,
        attrs=og_attrs,
    )


def _text_chunk(text: str, tag: str = "p", in_main: bool = True) -> HtmlChunk:
    return HtmlChunk(
        xpath=f"/html/body/main/{tag}",
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=ChunkType.TEXT_BLOCK,
        attrs={},
        parent_xpath="/html/body/main" if in_main else "/html/body",
        depth=3,
        in_main=in_main,
    )


# ===========================================================================
# Improvement 1: Article Content Extraction
# ===========================================================================


class TestAomFilterArticleExemption:
    """1A: <p> inside <article>/<main> with long non-link text gets exemption."""

    def test_p_inside_article_long_text_exempt(self):
        """<p> with >80 chars non-link text inside <article> → weight 0.9."""
        long_text = "A" * 100
        doc = lxml.html.document_fromstring(
            _html(f'<article><p>{long_text} <a href="#">ref1</a> <a href="#">ref2</a></p></article>')
        )
        p = doc.find(".//p")
        tree = doc.getroottree()
        weight, reason = _compute_weight(p, tree=tree)
        assert weight == 0.9
        assert reason == "article-content-p"

    def test_p_inside_main_long_text_exempt(self):
        """<p> with >80 chars non-link text inside <main> → weight 0.9."""
        long_text = "B" * 100
        doc = lxml.html.document_fromstring(_html(f'<main><p>{long_text} <a href="#">link</a></p></main>'))
        p = doc.find(".//p")
        tree = doc.getroottree()
        weight, reason = _compute_weight(p, tree=tree)
        assert weight == 0.9
        assert reason == "article-content-p"

    def test_p_inside_article_short_text_no_exemption(self):
        """<p> with ≤80 chars non-link text → normal penalty applies."""
        short_text = "A" * 30
        link_text = "B" * 40  # density > 0.5
        doc = lxml.html.document_fromstring(
            _html(f'<article><p>{short_text} <a href="#">{link_text}</a></p></article>')
        )
        p = doc.find(".//p")
        tree = doc.getroottree()
        weight, reason = _compute_weight(p, tree=tree)
        # Should get normal link-density penalty, not article exemption
        assert weight < 0.9

    def test_p_outside_article_no_exemption(self):
        """<p> outside <article>/<main> → normal link-density penalty."""
        long_text = "C" * 100
        link_text = "D" * 60  # moderate density
        doc = lxml.html.document_fromstring(_html(f'<div><p>{long_text} <a href="#">{link_text}</a></p></div>'))
        p = doc.find(".//p")
        tree = doc.getroottree()
        weight, reason = _compute_weight(p, tree=tree)
        # No article exemption → normal penalty
        assert "article" not in reason

    def test_div_inside_article_no_exemption(self):
        """<div> inside <article> → normal penalty (only <p> gets exemption)."""
        long_text = "E" * 100
        link_text = "F" * 60
        doc = lxml.html.document_fromstring(
            _html(f'<article><div>{long_text} <a href="#">{link_text}</a></div></article>')
        )
        div = doc.find(".//div")
        tree = doc.getroottree()
        weight, reason = _compute_weight(div, tree=tree)
        assert "article" not in reason

    def test_p_article_high_density_still_penalized(self):
        """<p> inside <article> with very high link density (>0.8) → still penalized."""
        short_text = "G" * 90
        link_text = "H" * 400  # density >> 0.8
        doc = lxml.html.document_fromstring(
            _html(f'<article><p>{short_text} <a href="#">{link_text}</a></p></article>')
        )
        p = doc.find(".//p")
        tree = doc.getroottree()
        weight, reason = _compute_weight(p, tree=tree)
        assert weight <= 0.2
        assert "article-p-link-dense" in reason


class TestIsInsideArticleOrMain:
    def test_direct_child_of_article(self):
        doc = lxml.html.document_fromstring(_html("<article><p>text</p></article>"))
        p = doc.find(".//p")
        assert _is_inside_article_or_main(p) is True

    def test_nested_in_article(self):
        doc = lxml.html.document_fromstring(_html("<article><div><p>text</p></div></article>"))
        p = doc.find(".//p")
        assert _is_inside_article_or_main(p) is True

    def test_in_main(self):
        doc = lxml.html.document_fromstring(_html("<main><p>text</p></main>"))
        p = doc.find(".//p")
        assert _is_inside_article_or_main(p) is True

    def test_outside_article(self):
        doc = lxml.html.document_fromstring(_html("<div><p>text</p></div>"))
        p = doc.find(".//p")
        assert _is_inside_article_or_main(p) is False


class TestWikipediaDomainMapping:
    """1C: Wikipedia domain mapping covers all languages."""

    def test_en_wikipedia(self):
        assert detect_schema("https://en.wikipedia.org/wiki/Python") == "WikiArticle"

    def test_ko_wikipedia(self):
        assert detect_schema("https://ko.wikipedia.org/wiki/파이썬") == "WikiArticle"

    def test_ja_wikipedia(self):
        assert detect_schema("https://ja.wikipedia.org/wiki/Python") == "WikiArticle"

    def test_fr_wikipedia(self):
        assert detect_schema("https://fr.wikipedia.org/wiki/Python") == "WikiArticle"

    def test_wikipedia_generic(self):
        """wikipedia.org itself should match."""
        assert detect_schema("https://www.wikipedia.org/") == "WikiArticle"


class TestArticleCompressorBudgetBased:
    """1B: Article compressor now uses budget, extracts more than 2 paragraphs."""

    def test_metadata_title_used(self):
        src = _html("<p>Some content paragraph with enough text here for testing.</p>")
        result = _compress_for_article(src, max_tokens=500, metadata={"headline": "Test Article Title"})
        assert "Test Article Title" in result

    def test_chunks_used_for_structure(self):
        chunks = [
            HtmlChunk(
                xpath="/html/body/main/h2",
                html="<h2>Introduction</h2>",
                text="Introduction",
                tag="h2",
                chunk_type=ChunkType.HEADING,
                attrs={},
            ),
            HtmlChunk(
                xpath="/html/body/main/p",
                html="<p>This is the intro paragraph.</p>",
                text="This is the intro paragraph.",
                tag="p",
                chunk_type=ChunkType.TEXT_BLOCK,
                attrs={},
            ),
            HtmlChunk(
                xpath="/html/body/main/h2[2]",
                html="<h2>Methods</h2>",
                text="Methods",
                tag="h2",
                chunk_type=ChunkType.HEADING,
                attrs={},
            ),
            HtmlChunk(
                xpath="/html/body/main/p[2]",
                html="<p>We used Python for analysis.</p>",
                text="We used Python for analysis.",
                tag="p",
                chunk_type=ChunkType.TEXT_BLOCK,
                attrs={},
            ),
        ]
        src = _html(
            "<h2>Introduction</h2><p>This is the intro paragraph.</p>"
            "<h2>Methods</h2><p>We used Python for analysis.</p>"
        )
        result = _compress_for_article(src, max_tokens=500, chunks=chunks)
        assert "Introduction" in result
        assert "Methods" in result

    def test_budget_limits_output(self):
        """Very low budget should truncate output."""
        long_text = "X" * 500
        src = _html(f"<h1>Title</h1><p>{long_text}</p>")
        result = _compress_for_article(src, max_tokens=10)
        # Should be truncated to roughly 10 tokens
        assert len(result) < 200

    def test_fallback_without_chunks(self):
        """Without chunks, falls back to text-line extraction."""
        src = _html(
            "<h1>Article Title Here</h1>"
            "<p>2024-10-22</p>"
            "<p>A substantial paragraph with enough content for extraction here.</p>"
        )
        result = _compress_for_article(src, max_tokens=500)
        assert "Article Title" in result
        assert "2024-10-22" in result


class TestSchemaOverrideDispatch:
    """1C: WikiArticle schema overrides article page_type compressor."""

    def test_wiki_schema_overrides_article_compressor(self):
        from pagemap.pruned_context_builder import _SCHEMA_COMPRESSORS

        # WikiArticle should use wiki compressor, not article compressor
        assert "WikiArticle" in _SCHEMA_COMPRESSORS


# ===========================================================================
# Improvement 2: Video Page Type + Metadata
# ===========================================================================


class TestVideoPageClassifier:
    """2A: video page type signals."""

    def test_youtube_watch_url(self):
        result = classify_page("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result.page_type == "video"

    def test_youtu_be_url(self):
        result = classify_page("https://youtu.be/dQw4w9WgXcQ")
        assert result.page_type == "video"

    def test_vimeo_url(self):
        result = classify_page("https://vimeo.com/123456789")
        assert result.page_type == "video"

    def test_youtube_root_not_video(self):
        """YouTube root URL should be landing, not video."""
        result = classify_page("https://www.youtube.com/")
        assert result.page_type != "video"

    def test_video_path_signal(self):
        result = classify_page("https://example.com/videos/my-video")
        assert "url_video_path" in result.signals

    def test_og_type_video_other(self):
        """og:type="video.other" (YouTube) → video signal."""
        html = '<html><head><meta property="og:type" content="video.other"></head><body></body></html>'
        result = classify_page("https://example.com/page", raw_html=html)
        assert "meta_og_video" in result.signals

    def test_videoobject_jsonld(self):
        """VideoObject JSON-LD → video page type."""
        jsonld = json.dumps({"@type": "VideoObject", "name": "Test Video"})
        html = f'<html><head><script type="application/ld+json">{jsonld}</script></head><body></body></html>'
        result = classify_page("https://example.com/page", raw_html=html)
        assert result.page_type == "video"

    def test_dom_video_player(self):
        html = '<html><body><div class="ytd-player"><video src="test.mp4"></video></div></body></html>'
        result = classify_page("https://example.com/page", raw_html=html)
        assert "dom_video_player" in result.signals


class TestVideoObjectMetadata:
    """2B: VideoObject JSON-LD parsing."""

    def _video_jsonld(self, **overrides) -> str:
        data = {
            "@type": "VideoObject",
            "name": "Test Video Title",
            "description": "A test video description.",
            "uploadDate": "2024-01-15T10:00:00Z",
            "duration": "PT5M30S",
            "thumbnailUrl": "https://example.com/thumb.jpg",
            "author": {"@type": "Person", "name": "Test Channel"},
            "interactionStatistic": [
                {
                    "@type": "InteractionCounter",
                    "interactionType": {"@type": "WatchAction"},
                    "userInteractionCount": 1500000,
                },
                {
                    "@type": "InteractionCounter",
                    "interactionType": {"@type": "LikeAction"},
                    "userInteractionCount": 25000,
                },
                {
                    "@type": "InteractionCounter",
                    "interactionType": {"@type": "CommentAction"},
                    "userInteractionCount": 3200,
                },
            ],
        }
        data.update(overrides)
        return json.dumps(data)

    def test_basic_fields(self):
        jsonld_text = self._video_jsonld()
        meta = [_meta_chunk(jsonld_text, {"type": "application/ld+json"})]
        result = extract_metadata(meta, [], "VideoObject")
        assert result["name"] == "Test Video Title"
        assert result["description"] == "A test video description."
        assert result["upload_date"] == "2024-01-15T10:00:00Z"
        assert result["duration"] == "PT5M30S"
        assert result["channel"] == "Test Channel"

    def test_interaction_statistics(self):
        jsonld_text = self._video_jsonld()
        meta = [_meta_chunk(jsonld_text, {"type": "application/ld+json"})]
        result = extract_metadata(meta, [], "VideoObject")
        assert result["view_count"] == 1500000
        assert result["like_count"] == 25000
        assert result["comment_count"] == 3200

    def test_thumbnail_url(self):
        jsonld_text = self._video_jsonld()
        meta = [_meta_chunk(jsonld_text, {"type": "application/ld+json"})]
        result = extract_metadata(meta, [], "VideoObject")
        assert result["thumbnail_url"] == "https://example.com/thumb.jpg"

    def test_thumbnail_array(self):
        """thumbnailUrl as array → take first."""
        jsonld_text = self._video_jsonld(
            thumbnailUrl=["https://example.com/thumb1.jpg", "https://example.com/thumb2.jpg"]
        )
        meta = [_meta_chunk(jsonld_text, {"type": "application/ld+json"})]
        result = extract_metadata(meta, [], "VideoObject")
        assert result["thumbnail_url"] == "https://example.com/thumb1.jpg"

    def test_string_interaction_type(self):
        """interactionType as URL string (schema.org prefix)."""
        data = {
            "@type": "VideoObject",
            "name": "Video",
            "interactionStatistic": [
                {
                    "@type": "InteractionCounter",
                    "interactionType": "http://schema.org/WatchAction",
                    "userInteractionCount": 5000,
                }
            ],
        }
        meta = [_meta_chunk(json.dumps(data), {"type": "application/ld+json"})]
        result = extract_metadata(meta, [], "VideoObject")
        assert result["view_count"] == 5000

    def test_og_fallback(self):
        """OG meta fallback for VideoObject."""
        og = _og_meta_chunk(
            {
                "og:title": "Video Title from OG",
                "og:description": "OG description",
            }
        )
        result = extract_metadata([og], [], "VideoObject")
        assert result["name"] == "Video Title from OG"


class TestVideoDomainMapping:
    """2B: YouTube/Vimeo domain → VideoObject schema."""

    def test_youtube_com(self):
        assert detect_schema("https://www.youtube.com/watch?v=abc") == "VideoObject"

    def test_youtu_be(self):
        assert detect_schema("https://youtu.be/abc") == "VideoObject"

    def test_vimeo_com(self):
        assert detect_schema("https://vimeo.com/123") == "VideoObject"


class TestVideoCompressor:
    """2C: Video compressor output."""

    def test_metadata_output(self):
        metadata = {
            "name": "My Video",
            "channel": "TestChannel",
            "upload_date": "2024-01-15",
            "duration": "PT10M",
            "view_count": 1500000,
            "like_count": 25000,
            "comment_count": 3200,
            "description": "This is a test video description.",
        }
        result = _compress_for_video("<html><body><p>test</p></body></html>", 500, metadata=metadata)
        assert "My Video" in result
        assert "TestChannel" in result
        assert "2024-01-15" in result
        assert "PT10M" in result
        assert "1.5M views" in result
        assert "25.0K likes" in result
        assert "3.2K comments" in result

    def test_fallback_without_metadata(self):
        """Without metadata, falls back to text extraction."""
        html = _html("<h1>Video Title</h1><p>Some description content here for fallback.</p>")
        result = _compress_for_video(html, 500, metadata=None)
        assert "Video Title" in result

    def test_budget_respected(self):
        metadata = {
            "name": "Short Video",
            "description": "X" * 5000,
        }
        result = _compress_for_video("<html><body><p>test</p></body></html>", 10, metadata=metadata)
        # Should be truncated
        assert len(result) < 500


# ===========================================================================
# Improvement 3: Amazon Price Extraction
# ===========================================================================


class TestProductContentRescue:
    """3A: Product schema content rescue regardless of remaining text."""

    def test_product_rescue_with_long_remaining(self):
        """Product schema: rescue price elements even when remaining text > 100 chars."""
        # Build a page with plenty of remaining text AND a price element
        # that would be removed by link-density
        body = (
            "<main>"
            "<div>" + "Content text. " * 20 + "</div>"  # lots of remaining text
            '<div><a href="#">Link1</a><a href="#">Link2</a><a href="#">Link3</a> $99.99</div>'
            "</main>"
        )
        doc = lxml.html.document_fromstring(_html(body))
        stats = aom_filter(doc, schema_name="Product")
        # The price element should be rescued
        remaining = doc.text_content()
        assert "$99.99" in remaining or stats.content_rescue_count > 0

    def test_non_product_no_rescue_with_long_remaining(self):
        """Non-Product schema: no rescue when remaining text > 100 chars."""
        body = (
            "<main>"
            "<div>" + "Content text. " * 20 + "</div>"
            '<div><a href="#">Link1</a><a href="#">Link2</a><a href="#">Link3</a> $99.99</div>'
            "</main>"
        )
        doc = lxml.html.document_fromstring(_html(body))
        stats = aom_filter(doc, schema_name="NewsArticle")
        # Content rescue should NOT fire (remaining > 100 chars + non-Product)
        assert stats.content_rescue_count == 0


class TestDomPriceFallback:
    """3B: DOM-based price extraction fallback in metadata."""

    def test_price_class_extraction(self):
        """Extract price from chunk with price-related class."""
        heading = _heading_chunk("span", "$49.99", attrs={"class": "a-price a-offscreen"})
        result = extract_metadata([], [heading], "Product")
        assert result.get("price") == 49.99

    def test_jsonld_price_takes_priority(self):
        """JSON-LD price should take priority over DOM fallback."""
        jsonld = json.dumps(
            {
                "@type": "Product",
                "name": "Widget",
                "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD"},
            }
        )
        meta = [_meta_chunk(jsonld, {"type": "application/ld+json"})]
        heading = _heading_chunk("span", "$49.99", attrs={"class": "a-price"})
        result = extract_metadata(meta, [heading], "Product")
        assert result["price"] == 29.99  # JSON-LD wins

    def test_shipping_price_ignored(self):
        """Shipping/handling price should not be extracted."""
        heading = _heading_chunk("span", "$5.99", attrs={"class": "shipping-price"})
        result = extract_metadata([], [heading], "Product")
        # "shipping" keyword should filter this out
        assert result.get("price") is None

    def test_currency_patterns(self):
        """Various currency patterns are recognized."""
        heading = _heading_chunk("span", "€199.99", attrs={"class": "price"})
        result = extract_metadata([], [heading], "Product")
        assert result.get("price") == 199.99


class TestProductCompressorPriceFallback:
    """3C: Product compressor price fallback from pruned_html."""

    def test_price_from_html_class(self):
        """Price extracted from HTML with price class when no other source."""
        html = _html('<div class="a-price"><span>$29.99</span></div>')
        result = _compress_for_product(html, max_tokens=500)
        assert "$29.99" in result

    def test_metadata_price_preferred(self):
        """Metadata price preferred over HTML fallback."""
        html = _html('<div class="a-price"><span>$49.99</span></div>')
        metadata = {"name": "Widget", "price": 29.99, "currency": "USD"}
        result = _compress_for_product(html, max_tokens=500, metadata=metadata)
        assert "$29.99" in result or "29.99" in result
