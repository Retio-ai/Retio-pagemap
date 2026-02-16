"""Tests for structured metadata extraction (metadata.py).

Covers JSON-LD parsing, itemprop, OG meta, h1 fallback, and cascade priority.
"""

from __future__ import annotations

import json

from pagemap.metadata import extract_metadata
from pagemap.pruning import ChunkType, HtmlChunk

# --- Helpers ---


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


# --- JSON-LD Parsing ---


class TestJsonLdParsing:
    def test_product_basic(self):
        data = {"@type": "Product", "name": "테스트 상품", "offers": {"price": "13900"}}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["name"] == "테스트 상품"
        assert result["price"] == 13900.0

    def test_graph_array(self):
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebPage", "name": "Page"},
                {"@type": "Product", "name": "그래프 상품", "offers": {"price": "25000", "priceCurrency": "KRW"}},
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["name"] == "그래프 상품"
        assert result["price"] == 25000.0
        assert result["currency"] == "KRW"

    def test_offers_as_list(self):
        data = {
            "@type": "Product",
            "name": "리스트 오퍼",
            "offers": [
                {"price": "9900", "priceCurrency": "KRW"},
                {"price": "12000", "priceCurrency": "KRW"},
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["price"] == 9900.0

    def test_aggregate_offer(self):
        data = {
            "@type": "Product",
            "name": "집계 오퍼",
            "offers": {
                "@type": "AggregateOffer",
                "lowPrice": "5000",
                "highPrice": "10000",
                "priceCurrency": "KRW",
            },
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["price"] == 5000.0

    def test_aggregate_offer_with_inner_offers(self):
        data = {
            "@type": "Product",
            "name": "내부 오퍼",
            "offers": {
                "@type": "AggregateOffer",
                "lowPrice": "5000",
                "offers": [{"price": "7000", "priceCurrency": "KRW"}],
            },
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["price"] == 7000.0

    def test_brand_as_object(self):
        data = {"@type": "Product", "name": "브랜드 테스트", "brand": {"@type": "Brand", "name": "Nike"}}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["brand"] == "Nike"

    def test_brand_as_string(self):
        data = {"@type": "Product", "name": "브랜드 문자열", "brand": "Adidas"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["brand"] == "Adidas"

    def test_rating_and_reviews(self):
        data = {
            "@type": "Product",
            "name": "평점 테스트",
            "aggregateRating": {"ratingValue": "4.5", "reviewCount": "120"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["rating"] == 4.5
        assert result["review_count"] == 120

    def test_image_as_string(self):
        data = {"@type": "Product", "name": "이미지 테스트", "image": "https://example.com/img.jpg"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["image_url"] == "https://example.com/img.jpg"

    def test_image_as_list(self):
        data = {
            "@type": "Product",
            "name": "이미지 리스트",
            "image": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["image_url"] == "https://example.com/a.jpg"

    def test_malformed_json(self):
        chunk = _meta_chunk("{invalid json!!", attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result == {}

    def test_non_product_jsonld_ignored(self):
        data = {"@type": "Organization", "name": "Acme Corp"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert "name" not in result

    def test_individual_product_type(self):
        data = {"@type": "IndividualProduct", "name": "개별 상품", "offers": {"price": "19900"}}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["name"] == "개별 상품"
        assert result["price"] == 19900.0

    def test_type_as_list(self):
        data = {"@type": ["Product", "ItemPage"], "name": "다중 타입", "offers": {"price": "3000"}}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["name"] == "다중 타입"


# --- Cascade Priority ---


class TestCascade:
    def test_jsonld_over_og(self):
        jsonld = {"@type": "Product", "name": "JSON-LD 이름"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        og_chunk = _og_meta_chunk({"og:title": "OG 이름"})
        result = extract_metadata([ld_chunk, og_chunk], [], "Product")
        assert result["name"] == "JSON-LD 이름"

    def test_og_fallback(self):
        og_chunk = _og_meta_chunk({"og:title": "OG 폴백 이름"})
        result = extract_metadata([og_chunk], [], "Product")
        assert result["name"] == "OG 폴백 이름"

    def test_itemprop_price(self):
        """itemprop="price" content="159000" -- coupang case."""
        chunk = _heading_chunk("span", "", attrs={"itemprop": "price", "content": "159000"})
        result = extract_metadata([], [chunk], "Product")
        assert result["price"] == 159000.0

    def test_itemprop_name(self):
        chunk = _heading_chunk("span", "상품명 itemprop", attrs={"itemprop": "name"})
        result = extract_metadata([], [chunk], "Product")
        assert result["name"] == "상품명 itemprop"

    def test_h1_fallback(self):
        h1 = _heading_chunk("h1", "H1 제목 텍스트입니다")
        result = extract_metadata([], [h1], "Product")
        assert result["name"] == "H1 제목 텍스트입니다"

    def test_h1_too_short_ignored(self):
        h1 = _heading_chunk("h1", "AB")
        result = extract_metadata([], [h1], "Product")
        assert "name" not in result

    def test_h1_too_long_ignored(self):
        h1 = _heading_chunk("h1", "A" * 301)
        result = extract_metadata([], [h1], "Product")
        assert "name" not in result

    def test_empty_chunks(self):
        result = extract_metadata([], [], "Product")
        assert result == {}

    def test_jsonld_name_wins_over_itemprop_name(self):
        jsonld = {"@type": "Product", "name": "JSON-LD"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        ip_chunk = _heading_chunk("span", "Itemprop", attrs={"itemprop": "name"})
        result = extract_metadata([ld_chunk], [ip_chunk], "Product")
        assert result["name"] == "JSON-LD"

    def test_itemprop_fills_missing_jsonld_fields(self):
        jsonld = {"@type": "Product", "name": "JSON-LD 상품"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        ip_chunk = _heading_chunk("span", "", attrs={"itemprop": "price", "content": "29900"})
        result = extract_metadata([ld_chunk], [ip_chunk], "Product")
        assert result["name"] == "JSON-LD 상품"
        assert result["price"] == 29900.0

    def test_og_price(self):
        og_chunk = _og_meta_chunk({"og:price:amount": "15000", "og:price:currency": "KRW"})
        result = extract_metadata([og_chunk], [], "Product")
        assert result["price"] == 15000.0
        assert result["currency"] == "KRW"

    def test_news_article_schema(self):
        og_chunk = _og_meta_chunk(
            {
                "og:title": "뉴스 헤드라인",
                "article:published_time": "2026-01-15",
                "article:author": "기자",
            }
        )
        result = extract_metadata([og_chunk], [], "NewsArticle")
        assert result["headline"] == "뉴스 헤드라인"
        assert result["date_published"] == "2026-01-15"
        assert result["author"] == "기자"

    def test_news_h1_fallback_uses_headline_key(self):
        h1 = _heading_chunk("h1", "뉴스 기사 제목입니다")
        result = extract_metadata([], [h1], "NewsArticle")
        assert result["headline"] == "뉴스 기사 제목입니다"
        assert "name" not in result
