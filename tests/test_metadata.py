"""Tests for structured metadata extraction (metadata.py).

Covers JSON-LD parsing, itemprop, OG meta, h1 fallback, and cascade priority.
"""

from __future__ import annotations

import json

import pytest

from pagemap.metadata import (
    _extract_image_url,
    _extract_price_from_html,
    _extract_price_from_offers,
    _extract_video_meta_from_dom,
    _find_type_in_jsonld,
    _parse_h1,
    _to_float,
    _to_int,
    extract_metadata,
)
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


# --- JSON-LD NewsArticle ---


class TestJsonLdNewsArticle:
    def test_basic(self):
        data = {
            "@type": "NewsArticle",
            "headline": "속보: 중요 뉴스",
            "author": {"@type": "Person", "name": "김기자"},
            "datePublished": "2026-02-20",
            "publisher": {"@type": "Organization", "name": "한국일보"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "NewsArticle")
        assert result["headline"] == "속보: 중요 뉴스"
        assert result["author"] == "김기자"
        assert result["date_published"] == "2026-02-20"
        assert result["publisher"] == "한국일보"

    def test_author_as_string(self):
        data = {
            "@type": "NewsArticle",
            "headline": "뉴스 기사",
            "author": "John Doe",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "NewsArticle")
        assert result["author"] == "John Doe"

    def test_article_type(self):
        data = {"@type": "Article", "headline": "일반 기사"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "NewsArticle")
        assert result["headline"] == "일반 기사"

    def test_blog_posting_type(self):
        data = {"@type": "BlogPosting", "headline": "블로그 포스트"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "NewsArticle")
        assert result["headline"] == "블로그 포스트"

    def test_article_body_truncated(self):
        data = {
            "@type": "NewsArticle",
            "headline": "기사",
            "articleBody": "A" * 500,
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "NewsArticle")
        assert len(result["article_body"]) <= 200

    def test_cascade_og_fills_publisher(self):
        """JSON-LD missing publisher, OG fills it."""
        jsonld = {"@type": "NewsArticle", "headline": "기사 제목"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        og_chunk = _og_meta_chunk({"og:site_name": "매체명"})
        result = extract_metadata([ld_chunk, og_chunk], [], "NewsArticle")
        assert result["headline"] == "기사 제목"
        assert result["publisher"] == "매체명"


# --- JSON-LD BreadcrumbList ---


class TestJsonLdBreadcrumbList:
    def test_basic(self):
        data = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": {"@id": "https://example.com/"}},
                {"@type": "ListItem", "position": 2, "name": "Category", "item": {"@id": "https://example.com/cat"}},
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert "breadcrumbs" in result
        assert len(result["breadcrumbs"]) == 2
        assert result["breadcrumbs"][0]["name"] == "Home"
        assert result["breadcrumbs"][1]["name"] == "Category"

    def test_alongside_product(self):
        """BreadcrumbList in separate JSON-LD block alongside Product."""
        product_data = {"@type": "Product", "name": "상품", "offers": {"price": "10000"}}
        bc_data = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home"},
            ],
        }
        p_chunk = _meta_chunk(json.dumps(product_data), attrs={"type": "application/ld+json"})
        bc_chunk = _meta_chunk(json.dumps(bc_data), attrs={"type": "application/ld+json"})
        result = extract_metadata([p_chunk, bc_chunk], [], "Product")
        assert result["name"] == "상품"
        assert result["price"] == 10000.0
        assert len(result["breadcrumbs"]) == 1

    def test_item_as_object(self):
        """item field is an object with @id and name."""
        data = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "item": {"@type": "WebPage", "@id": "https://example.com/page", "name": "Page"},
                },
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["breadcrumbs"][0]["name"] == "Page"
        assert result["breadcrumbs"][0]["url"] == "https://example.com/page"

    def test_empty_not_included(self):
        """Empty BreadcrumbList → no breadcrumbs key."""
        data = {"@type": "BreadcrumbList", "itemListElement": []}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert "breadcrumbs" not in result


# --- JSON-LD FAQPage ---


class TestJsonLdFAQPage:
    def test_basic(self):
        data = {
            "@type": "FAQPage",
            "name": "자주 묻는 질문",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "배송은 얼마나 걸리나요?",
                    "acceptedAnswer": {"@type": "Answer", "text": "보통 2-3일 소요됩니다."},
                },
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "FAQPage")
        assert result["name"] == "자주 묻는 질문"
        assert len(result["questions"]) == 1
        assert result["questions"][0]["question"] == "배송은 얼마나 걸리나요?"
        assert result["questions"][0]["answer"] == "보통 2-3일 소요됩니다."

    def test_multiple_qa_pairs(self):
        data = {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "Q1?",
                    "acceptedAnswer": {"@type": "Answer", "text": "A1"},
                },
                {
                    "@type": "Question",
                    "name": "Q2?",
                    "acceptedAnswer": {"@type": "Answer", "text": "A2"},
                },
                {
                    "@type": "Question",
                    "name": "Q3?",
                    "acceptedAnswer": {"@type": "Answer", "text": "A3"},
                },
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "FAQPage")
        assert len(result["questions"]) == 3

    def test_empty_main_entity(self):
        data = {"@type": "FAQPage", "name": "FAQ", "mainEntity": []}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "FAQPage")
        assert result["name"] == "FAQ"
        assert "questions" not in result

    def test_main_entity_as_single_dict(self):
        """mainEntity is a single Question dict instead of list."""
        data = {
            "@type": "FAQPage",
            "name": "단일 질문",
            "mainEntity": {
                "@type": "Question",
                "name": "하나만?",
                "acceptedAnswer": {"@type": "Answer", "text": "네."},
            },
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "FAQPage")
        assert result["name"] == "단일 질문"
        assert len(result["questions"]) == 1

    def test_missing_accepted_answer_uses_suggested(self):
        data = {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "커뮤니티 질문?",
                    "suggestedAnswer": {"@type": "Answer", "text": "추천 답변입니다."},
                },
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "FAQPage")
        assert result["questions"][0]["answer"] == "추천 답변입니다."

    def test_answer_text_truncated(self):
        data = {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "긴 답변?",
                    "acceptedAnswer": {"@type": "Answer", "text": "B" * 1000},
                },
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "FAQPage")
        assert len(result["questions"][0]["answer"]) <= 500


# --- JSON-LD Event ---


class TestJsonLdEvent:
    def test_basic(self):
        data = {
            "@type": "Event",
            "name": "서울 뮤직 페스티벌",
            "startDate": "2026-07-15T18:00:00+09:00",
            "endDate": "2026-07-15T22:00:00+09:00",
            "location": {
                "@type": "Place",
                "name": "올림픽 공원",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": "서울",
                    "addressRegion": "송파구",
                },
            },
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["name"] == "서울 뮤직 페스티벌"
        assert result["start_date"] == "2026-07-15T18:00:00+09:00"
        assert result["end_date"] == "2026-07-15T22:00:00+09:00"
        assert "올림픽 공원" in result["location"]

    def test_location_as_string(self):
        data = {
            "@type": "Event",
            "name": "이벤트",
            "location": "Grand Ballroom, Seoul",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["location"] == "Grand Ballroom, Seoul"

    def test_virtual_location(self):
        data = {
            "@type": "Event",
            "name": "온라인 세미나",
            "location": {"@type": "VirtualLocation", "url": "https://zoom.us/j/123"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["location"] == "https://zoom.us/j/123"

    def test_music_event_subtype(self):
        data = {"@type": "MusicEvent", "name": "콘서트"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["name"] == "콘서트"

    def test_offers_price_reuse(self):
        data = {
            "@type": "Event",
            "name": "유료 이벤트",
            "offers": {"@type": "Offer", "price": "50000", "priceCurrency": "KRW"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["price"] == 50000.0
        assert result["currency"] == "KRW"

    def test_event_status_cancelled(self):
        data = {
            "@type": "Event",
            "name": "취소된 이벤트",
            "eventStatus": "https://schema.org/EventCancelled",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["event_status"] == "EventCancelled"

    def test_organizer_and_performer_separate(self):
        data = {
            "@type": "Event",
            "name": "공연",
            "performer": {"@type": "Person", "name": "아티스트"},
            "organizer": {"@type": "Organization", "name": "기획사"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["performer"] == "아티스트"
        assert result["organizer"] == "기획사"


# --- JSON-LD LocalBusiness ---


class TestJsonLdLocalBusiness:
    def test_basic(self):
        data = {
            "@type": "LocalBusiness",
            "name": "맛있는 식당",
            "telephone": "+82-2-1234-5678",
            "priceRange": "$$",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "강남대로 123",
                "addressLocality": "서울",
                "addressRegion": "강남구",
                "postalCode": "06000",
            },
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert result["name"] == "맛있는 식당"
        assert result["telephone"] == "+82-2-1234-5678"
        assert result["price_range"] == "$$"
        assert "강남대로 123" in result["address"]

    def test_restaurant_subtype(self):
        data = {"@type": "Restaurant", "name": "레스토랑"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert result["name"] == "레스토랑"

    def test_aggregate_rating(self):
        data = {
            "@type": "LocalBusiness",
            "name": "가게",
            "aggregateRating": {"ratingValue": "4.2", "reviewCount": "350"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert result["rating"] == 4.2
        assert result["review_count"] == 350

    def test_address_as_string(self):
        data = {
            "@type": "LocalBusiness",
            "name": "가게",
            "address": "서울시 강남구 역삼동 123-45",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert "역삼동" in result["address"]

    def test_opening_hours_specification(self):
        data = {
            "@type": "LocalBusiness",
            "name": "가게",
            "openingHoursSpecification": [
                {"dayOfWeek": ["Monday", "Tuesday"], "opens": "09:00", "closes": "18:00"},
                {"dayOfWeek": "Wednesday", "opens": "10:00", "closes": "17:00"},
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert "Monday" in result["opening_hours"]
        assert "09:00" in result["opening_hours"]

    def test_geo_coordinates(self):
        data = {
            "@type": "LocalBusiness",
            "name": "가게",
            "geo": {"@type": "GeoCoordinates", "latitude": "37.5665", "longitude": "126.9780"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert result["geo"]["latitude"] == 37.5665
        assert result["geo"]["longitude"] == 126.9780


# --- Dispatch Refactoring ---


class TestDispatchRefactoring:
    def test_product_still_works(self):
        """Existing Product behavior preserved after refactoring."""
        data = {"@type": "Product", "name": "기존 상품", "offers": {"price": "9900"}}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["name"] == "기존 상품"
        assert result["price"] == 9900.0

    def test_unknown_schema_empty_jsonld(self):
        """Unknown schema name → no JSON-LD parsing, still has OG/itemprop."""
        og_chunk = _og_meta_chunk({"og:title": "Unknown"})
        result = extract_metadata([og_chunk], [], "SomeUnknownSchema")
        # No OG map for unknown → empty
        assert result == {}

    def test_breadcrumb_in_fast_path(self):
        """source_hint='json_ld' fast path still includes breadcrumbs."""
        product = {"@type": "Product", "name": "Fast", "offers": {"price": "100"}}
        bc = {
            "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Home"}],
        }
        p_chunk = _meta_chunk(json.dumps(product), attrs={"type": "application/ld+json"})
        bc_chunk = _meta_chunk(json.dumps(bc), attrs={"type": "application/ld+json"})
        result = extract_metadata([p_chunk, bc_chunk], [], "Product", source_hint="json_ld")
        assert result["name"] == "Fast"
        assert len(result["breadcrumbs"]) == 1


# --- JSON-LD Chunk Caching ---


class TestJsonLdChunkCaching:
    def test_single_parse_multiple_schemas(self):
        """Product + BreadcrumbList in same page → both extracted from single parse."""
        product = {"@type": "Product", "name": "상품", "offers": {"price": "5000"}}
        bc = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home"},
                {"@type": "ListItem", "position": 2, "name": "Electronics"},
            ],
        }
        p_chunk = _meta_chunk(json.dumps(product), attrs={"type": "application/ld+json"})
        bc_chunk = _meta_chunk(json.dumps(bc), attrs={"type": "application/ld+json"})
        result = extract_metadata([p_chunk, bc_chunk], [], "Product")
        assert result["name"] == "상품"
        assert result["price"] == 5000.0
        assert len(result["breadcrumbs"]) == 2
        assert result["breadcrumbs"][0]["name"] == "Home"

    def test_invalid_image_url_rejected(self):
        """data: and javascript: URIs are blocked by _is_valid_url."""
        data = {"@type": "Product", "name": "위험 상품", "image": "javascript:alert(1)"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert "image_url" not in result
        assert result["name"] == "위험 상품"

    def test_relative_image_url_rejected(self):
        """Relative URLs are not accepted — only http(s) and protocol-relative."""
        data = {"@type": "Product", "name": "상대경로", "image": "/images/foo.jpg"}
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert "image_url" not in result

    def test_malformed_skipped_others_parsed(self):
        """Malformed JSON-LD chunk skipped, valid ones still parsed."""
        bad_chunk = _meta_chunk("{bad json!!", attrs={"type": "application/ld+json"})
        good_data = {"@type": "Product", "name": "유효한 상품"}
        good_chunk = _meta_chunk(json.dumps(good_data), attrs={"type": "application/ld+json"})
        result = extract_metadata([bad_chunk, good_chunk], [], "Product")
        assert result["name"] == "유효한 상품"


# --- _to_float European number format ---


class TestToFloat:
    def test_us_format(self):
        assert _to_float("1,500.99") == 1500.99

    def test_european_format(self):
        """'1.500,99' (European) should parse as 1500.99."""
        assert _to_float("1.500,99") == 1500.99

    def test_european_integer(self):
        """'1.500' (3 digits after period) → European thousands = 1500."""
        assert _to_float("1.500") == 1500

    def test_decimal_non_three_digits(self):
        """Non-3 trailing digits after period → decimal point."""
        assert _to_float("1.5") == 1.5
        assert _to_float("3.14") == 3.14
        assert _to_float("29.99") == 29.99

    def test_multiple_periods_thousands(self):
        """Multiple periods → all are thousands separators."""
        assert _to_float("1.500.000") == 1500000

    def test_single_period_three_digits_thousands(self):
        """Single period with exactly 3 trailing digits → thousands."""
        assert _to_float("1.000") == 1000

    def test_numeric_passthrough(self):
        """Numeric inputs bypass string heuristics."""
        assert _to_float(37.774) == 37.774
        assert _to_float(1500) == 1500.0

    def test_bool_not_numeric_shortcut(self):
        """Booleans bypass numeric fast path and fail string conversion."""
        assert _to_float(True) is None
        assert _to_float(False) is None

    def test_zero(self):
        assert _to_float(0) == 0.0

    def test_none(self):
        assert _to_float(None) is None

    def test_negative_european(self):
        assert _to_float("-1.234,56") == -1234.56

    def test_plain_integer_string(self):
        assert _to_float("42") == 42.0

    def test_garbage_returns_none(self):
        assert _to_float("abc") is None

    def test_ambiguous_comma_only(self):
        """'1,5' (no dot) is ambiguous — treated as US (comma removed) → 15."""
        assert _to_float("1,5") == 15.0

    def test_ambiguous_comma_thousands_only(self):
        """'1,500' (no dot) → US thousands separator → 1500."""
        assert _to_float("1,500") == 1500.0


# --- _to_int rounding ---


class TestToInt:
    def test_rounds_up(self):
        """4.7 should round to 5, not truncate to 4."""
        assert _to_int("4.7") == 5

    def test_rounds_down(self):
        assert _to_int("4.3") == 4

    def test_banker_rounding_half_even(self):
        """Python 3 round() uses banker's rounding: 0.5 rounds to even."""
        assert _to_int("2.5") == 2
        assert _to_int("3.5") == 4

    def test_integer_string(self):
        assert _to_int("120") == 120

    def test_zero(self):
        assert _to_int(0) == 0

    def test_none(self):
        assert _to_int(None) is None

    def test_float_input(self):
        assert _to_int(4.9) == 5


# --- _extract_image_url ImageObject dict ---


class TestExtractImageUrl:
    def test_string_url(self):
        assert _extract_image_url({"image": "https://example.com/img.jpg"}) == "https://example.com/img.jpg"

    def test_list_url(self):
        assert (
            _extract_image_url({"image": ["https://example.com/a.jpg", "https://example.com/b.jpg"]})
            == "https://example.com/a.jpg"
        )

    def test_imageobject_dict_url(self):
        """ImageObject with 'url' field."""
        assert (
            _extract_image_url({"image": {"@type": "ImageObject", "url": "https://example.com/img.jpg"}})
            == "https://example.com/img.jpg"
        )

    def test_imageobject_dict_contenturl(self):
        """ImageObject with 'contentUrl' field."""
        assert (
            _extract_image_url({"image": {"@type": "ImageObject", "contentUrl": "https://example.com/img.jpg"}})
            == "https://example.com/img.jpg"
        )

    def test_imageobject_url_preferred_over_contenturl(self):
        """When both 'url' and 'contentUrl' exist, 'url' wins."""
        result = _extract_image_url(
            {
                "image": {
                    "url": "https://example.com/primary.jpg",
                    "contentUrl": "https://example.com/fallback.jpg",
                }
            }
        )
        assert result == "https://example.com/primary.jpg"

    def test_imageobject_invalid_url_rejected(self):
        """ImageObject with javascript: URL is rejected."""
        assert _extract_image_url({"image": {"url": "javascript:alert(1)"}}) is None

    def test_empty_dict(self):
        """ImageObject with no url/contentUrl returns None."""
        assert _extract_image_url({"image": {}}) is None

    def test_no_image_key(self):
        assert _extract_image_url({}) is None

    def test_empty_list(self):
        assert _extract_image_url({"image": []}) is None

    def test_imageobject_empty_url_falls_to_contenturl(self):
        """ImageObject with url=None should fall through to contentUrl."""
        result = _extract_image_url({"image": {"url": None, "contentUrl": "https://example.com/fallback.jpg"}})
        assert result == "https://example.com/fallback.jpg"

    def test_list_first_element_is_dict(self):
        """List containing a dict as first element — dict is not a string, rejected by _is_valid_url."""
        assert _extract_image_url({"image": [{"url": "https://example.com/img.jpg"}]}) is None

    def test_imageobject_integration(self):
        """Full integration: ImageObject in Product JSON-LD."""
        data = {
            "@type": "Product",
            "name": "Test",
            "image": {"@type": "ImageObject", "url": "https://example.com/product.jpg"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["image_url"] == "https://example.com/product.jpg"


# --- _find_type_in_jsonld recursion depth ---


class TestFindTypeRecursionDepth:
    def test_shallow_found(self):
        """Normal 2-level nesting works fine."""
        data = {"@graph": [{"@type": "Product", "name": "Found"}]}
        assert _find_type_in_jsonld(data, ("Product",)) is not None

    def test_deep_nesting_blocked(self):
        """15-level @graph nesting should be blocked by depth limit."""
        data: dict = {"@type": "Product", "name": "Deep"}
        for _ in range(15):
            data = {"@graph": [data]}
        assert _find_type_in_jsonld(data, ("Product",)) is None

    def test_custom_max_depth(self):
        """Custom max_depth=3 allows dict->@graph(list)->item(dict) traversal."""
        data = {"@graph": [{"@type": "Product", "name": "Shallow"}]}
        assert _find_type_in_jsonld(data, ("Product",), max_depth=3) is not None
        # max_depth=2 is too shallow: dict->list->dict needs 3 levels
        assert _find_type_in_jsonld(data, ("Product",), max_depth=2) is None

    def test_default_depth_sufficient_for_4_levels(self):
        """Default depth=5 handles data -> @graph (list) -> item -> type (4 levels)."""
        data = {"@graph": [{"@graph": [{"@type": "Product", "name": "Nested"}]}]}
        assert _find_type_in_jsonld(data, ("Product",)) is not None

    def test_list_nesting_also_limited(self):
        """Deep list nesting is also bounded."""
        data: list | dict = {"@type": "Product", "name": "Deep"}
        for _ in range(15):
            data = [data]
        assert _find_type_in_jsonld(data, ("Product",)) is None


# --- Zero-price handling ---


class TestPriceZero:
    def test_aggregate_offer_lowprice_zero_int(self):
        """lowPrice=0 (int) should not fall through to price."""
        result = _extract_price_from_offers(
            {
                "@type": "AggregateOffer",
                "lowPrice": 0,
                "price": "100",
            }
        )
        assert result["price"] == 0.0

    def test_aggregate_offer_lowprice_zero_string(self):
        """lowPrice='0' (string) should not fall through to price."""
        result = _extract_price_from_offers(
            {
                "@type": "AggregateOffer",
                "lowPrice": "0",
                "price": "100",
            }
        )
        assert result["price"] == 0.0

    def test_inner_offers_price_zero(self):
        """Inner offers price=0 should be used, not discarded."""
        result = _extract_price_from_offers(
            {
                "@type": "AggregateOffer",
                "lowPrice": "100",
                "offers": [{"price": 0}],
            }
        )
        assert result["price"] == 0.0

    def test_none_lowprice_falls_through(self):
        """lowPrice=None falls through to price field."""
        result = _extract_price_from_offers(
            {
                "@type": "AggregateOffer",
                "price": "50",
            }
        )
        assert result["price"] == 50.0

    def test_inner_offers_none_keeps_outer(self):
        """Inner offers with None price keeps outer lowPrice."""
        result = _extract_price_from_offers(
            {
                "@type": "AggregateOffer",
                "lowPrice": "100",
                "offers": [{}],
            }
        )
        assert result["price"] == 100.0

    def test_zero_price_integration(self):
        """Full integration: free product via extract_metadata."""
        data = {
            "@type": "Product",
            "name": "Free Item",
            "offers": {"@type": "AggregateOffer", "lowPrice": 0, "priceCurrency": "USD"},
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["price"] == 0.0


# --- Sanitize bypass fields ---


class TestSanitizeBypass:
    """Verify that previously unsanitized fields now strip ANSI, role prefixes, zero-width chars."""

    def test_currency_sanitized(self):
        result = _extract_price_from_offers(
            {
                "@type": "Offer",
                "price": "10",
                "priceCurrency": "\x1b[31mUSD\x1b[0m",
            }
        )
        assert result["currency"] == "USD"

    def test_currency_none_excluded(self):
        """None priceCurrency should not appear in result."""
        result = _extract_price_from_offers(
            {
                "@type": "Offer",
                "price": "10",
            }
        )
        assert "currency" not in result

    def test_currency_empty_excluded(self):
        """Empty string priceCurrency should not appear in result."""
        result = _extract_price_from_offers(
            {
                "@type": "Offer",
                "price": "10",
                "priceCurrency": "",
            }
        )
        assert "currency" not in result

    def test_date_published_sanitized(self):
        data = {
            "@type": "NewsArticle",
            "headline": "Test",
            "datePublished": "\x1b[31m2026-01-01\x1b[0m",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "NewsArticle")
        assert result["date_published"] == "2026-01-01"

    def test_upload_date_sanitized(self):
        data = {
            "@type": "VideoObject",
            "name": "Video",
            "uploadDate": "\u200b2026-01-01\u200b",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "VideoObject")
        assert result["upload_date"] == "2026-01-01"

    def test_duration_sanitized(self):
        data = {
            "@type": "VideoObject",
            "name": "Video",
            "duration": "Assistant: PT10M\x1b[0m",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "VideoObject")
        # Role prefix "Assistant:" and ANSI stripped
        assert "\x1b" not in result["duration"]
        assert "Assistant:" not in result["duration"]

    def test_breadcrumb_name_item_sanitized(self):
        data = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "item": {"name": "\x1b[31mHome\x1b[0m", "@id": "https://example.com/"},
                },
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["breadcrumbs"][0]["name"] == "Home"

    def test_breadcrumb_name_element_sanitized(self):
        data = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "\u200bCategory\u200b"},
            ],
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Product")
        assert result["breadcrumbs"][0]["name"] == "Category"

    def test_start_date_sanitized(self):
        data = {
            "@type": "Event",
            "name": "Test Event",
            "startDate": "\x1b[31m2026-07-01\x1b[0m",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["start_date"] == "2026-07-01"

    def test_end_date_sanitized(self):
        data = {
            "@type": "Event",
            "name": "Test Event",
            "endDate": "\u200b2026-07-02\u200b",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "Event")
        assert result["end_date"] == "2026-07-02"

    def test_telephone_sanitized(self):
        data = {
            "@type": "LocalBusiness",
            "name": "Shop",
            "telephone": "\x1b[31m+1-555-1234\x1b[0m",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert result["telephone"] == "+1-555-1234"

    def test_price_range_sanitized(self):
        data = {
            "@type": "LocalBusiness",
            "name": "Shop",
            "priceRange": "\u200b$$\u200b",
        }
        chunk = _meta_chunk(json.dumps(data), attrs={"type": "application/ld+json"})
        result = extract_metadata([chunk], [], "LocalBusiness")
        assert result["price_range"] == "$$"


# --- OG image URL validation ---


class TestOgImageValidation:
    def test_valid_https_accepted(self):
        og_chunk = _og_meta_chunk({"og:image": "https://example.com/img.jpg"})
        result = extract_metadata([og_chunk], [], "Product")
        assert result["image_url"] == "https://example.com/img.jpg"

    def test_javascript_rejected(self):
        og_chunk = _og_meta_chunk({"og:image": "javascript:alert(1)"})
        result = extract_metadata([og_chunk], [], "Product")
        assert "image_url" not in result

    def test_data_uri_rejected(self):
        og_chunk = _og_meta_chunk({"og:image": "data:image/png;base64,abc"})
        result = extract_metadata([og_chunk], [], "Product")
        assert "image_url" not in result

    def test_relative_rejected(self):
        og_chunk = _og_meta_chunk({"og:image": "/images/photo.jpg"})
        result = extract_metadata([og_chunk], [], "Product")
        assert "image_url" not in result

    def test_protocol_relative_accepted(self):
        og_chunk = _og_meta_chunk({"og:image": "//cdn.example.com/img.jpg"})
        result = extract_metadata([og_chunk], [], "Product")
        assert result["image_url"] == "//cdn.example.com/img.jpg"

    def test_thumbnail_validated(self):
        og_chunk = _og_meta_chunk({"og:image": "https://example.com/thumb.jpg"})
        result = extract_metadata([og_chunk], [], "VideoObject")
        assert result["thumbnail_url"] == "https://example.com/thumb.jpg"

    def test_thumbnail_javascript_rejected(self):
        og_chunk = _og_meta_chunk({"og:image": "javascript:void(0)"})
        result = extract_metadata([og_chunk], [], "VideoObject")
        assert "thumbnail_url" not in result

    def test_html_entities_decoded_in_url(self):
        """HTML entities like &amp; in OG image URLs are decoded."""
        og_chunk = _og_meta_chunk({"og:image": "https://cdn.example.com/img.jpg?w=100&amp;h=200"})
        result = extract_metadata([og_chunk], [], "Product")
        assert result["image_url"] == "https://cdn.example.com/img.jpg?w=100&h=200"

    def test_non_image_fields_still_sanitized(self):
        """Non-image OG fields still go through sanitize_text."""
        og_chunk = _og_meta_chunk({"og:title": "\x1b[31mTitle\x1b[0m"})
        result = extract_metadata([og_chunk], [], "Product")
        assert result["name"] == "Title"


# --- _parse_h1 sanitized ---


class TestParseH1Sanitized:
    def test_basic_pass(self):
        chunks = [_heading_chunk("h1", "Hello World Title")]
        assert _parse_h1(chunks) == "Hello World Title"

    def test_ansi_stripped(self):
        chunks = [_heading_chunk("h1", "\x1b[31mRed Title Here\x1b[0m")]
        result = _parse_h1(chunks)
        assert result == "Red Title Here"

    def test_role_prefix_stripped(self):
        chunks = [_heading_chunk("h1", "Assistant: Injected heading text")]
        result = _parse_h1(chunks)
        assert "Assistant:" not in result

    def test_zero_width_stripped(self):
        chunks = [_heading_chunk("h1", "\u200bHidden\u200b Characters Here")]
        result = _parse_h1(chunks)
        assert "\u200b" not in result

    def test_too_short_rejected(self):
        chunks = [_heading_chunk("h1", "AB")]
        assert _parse_h1(chunks) is None

    def test_max_length_respected(self):
        """Titles at boundary (just under 300) still work."""
        title = "A" * 299
        chunks = [_heading_chunk("h1", title)]
        result = _parse_h1(chunks)
        assert result is not None
        assert len(result) <= 300

    def test_integration_via_extract_metadata(self):
        """H1 fallback in extract_metadata also sanitized."""
        h1 = _heading_chunk("h1", "\x1b[31mSanitized H1 Title\x1b[0m")
        result = extract_metadata([], [h1], "Product")
        assert result["name"] == "Sanitized H1 Title"


# --- VideoObject OG bug (Fix 1) ---


class TestVideoObjectOgBug:
    """og:site_name should NOT be mapped to channel for VideoObject."""

    def test_og_site_name_not_mapped_to_channel(self):
        """og:site_name='YouTube' must not appear as channel."""
        og = _og_meta_chunk({"og:site_name": "YouTube", "og:title": "My Video"})
        result = extract_metadata([og], [], "VideoObject")
        assert "channel" not in result
        assert result["name"] == "My Video"

    def test_og_site_name_vimeo(self):
        """og:site_name='Vimeo' must not appear as channel."""
        og = _og_meta_chunk({"og:site_name": "Vimeo", "og:title": "Art Video"})
        result = extract_metadata([og], [], "VideoObject")
        assert "channel" not in result

    def test_jsonld_author_overrides_og_site_name(self):
        """JSON-LD author.name wins, not 'YouTube' from og:site_name."""
        jsonld = {
            "@type": "VideoObject",
            "name": "My Video",
            "author": {"@type": "Person", "name": "RealCreator"},
        }
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        og = _og_meta_chunk({"og:site_name": "YouTube"})
        result = extract_metadata([ld_chunk, og], [], "VideoObject")
        assert result["channel"] == "RealCreator"

    def test_no_author_means_no_channel(self):
        """No JSON-LD author + og:site_name → channel absent."""
        jsonld = {"@type": "VideoObject", "name": "My Video"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        og = _og_meta_chunk({"og:site_name": "YouTube"})
        result = extract_metadata([ld_chunk, og], [], "VideoObject")
        assert "channel" not in result

    def test_og_still_provides_other_fields(self):
        """og:title, og:description, og:image still work for VideoObject."""
        og = _og_meta_chunk(
            {
                "og:title": "Video Title",
                "og:description": "A great video",
                "og:image": "https://example.com/thumb.jpg",
            }
        )
        result = extract_metadata([og], [], "VideoObject")
        assert result["name"] == "Video Title"
        assert result["description"] == "A great video"
        assert result["thumbnail_url"] == "https://example.com/thumb.jpg"


# --- VideoObject itemprop (Fix 2) ---


class TestVideoObjectItemprop:
    """itemprop='author' should provide channel for VideoObject."""

    def test_itemprop_author_provides_channel(self):
        """itemprop='author' → channel."""
        chunk = _heading_chunk("span", "CreatorName", attrs={"itemprop": "author"})
        result = extract_metadata([], [chunk], "VideoObject")
        assert result["channel"] == "CreatorName"

    def test_jsonld_channel_priority_over_itemprop(self):
        """JSON-LD author wins over itemprop author."""
        jsonld = {
            "@type": "VideoObject",
            "name": "Video",
            "author": {"@type": "Person", "name": "JsonLdAuthor"},
        }
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        itemprop_chunk = _heading_chunk("span", "ItempropAuthor", attrs={"itemprop": "author"})
        result = extract_metadata([ld_chunk], [itemprop_chunk], "VideoObject")
        assert result["channel"] == "JsonLdAuthor"


# --- Video DOM fallback (Fix 3) ---


class TestVideoDomFallback:
    """DOM class-name fallback for video metadata."""

    @pytest.mark.parametrize(
        "class_name",
        ["channel-name", "owner-name", "ytd-channel-name", "uploader"],
    )
    def test_channel_from_class(self, class_name):
        """Class-name match → channel."""
        chunk = _heading_chunk("span", "CreatorXYZ", attrs={"class": class_name})
        result = _extract_video_meta_from_dom([chunk])
        assert result["channel"] == "CreatorXYZ"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1,234,567 views", 1234567),
            ("500 views", 500),
            ("조회수 2,345", 2345),
            ("1,000회 조회", 1000),
        ],
    )
    def test_view_count_patterns(self, text, expected):
        """Text pattern → view_count."""
        chunk = _heading_chunk("span", text)
        result = _extract_video_meta_from_dom([chunk])
        assert result["view_count"] == expected

    @pytest.mark.parametrize(
        "class_name",
        ["ytp-time-duration", "video-time"],
    )
    def test_duration_from_class(self, class_name):
        """Duration class-name match → duration."""
        chunk = _heading_chunk("span", "12:34", attrs={"class": class_name})
        result = _extract_video_meta_from_dom([chunk])
        assert result["duration"] == "12:34"

    def test_jsonld_priority_over_dom(self):
        """JSON-LD fields not overwritten by DOM."""
        jsonld = {
            "@type": "VideoObject",
            "name": "Video",
            "author": {"@type": "Person", "name": "JsonLdChannel"},
            "duration": "PT10M",
        }
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        dom_channel = _heading_chunk("span", "DomChannel", attrs={"class": "channel-name"})
        dom_duration = _heading_chunk("span", "5:00", attrs={"class": "ytp-time-duration"})
        result = extract_metadata([ld_chunk], [dom_channel, dom_duration], "VideoObject")
        assert result["channel"] == "JsonLdChannel"
        assert result["duration"] == "PT10M"

    def test_dom_fills_gaps(self):
        """DOM fills fields missing from JSON-LD."""
        jsonld = {"@type": "VideoObject", "name": "Video"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        dom_channel = _heading_chunk("span", "DomCreator", attrs={"class": "channel-name"})
        dom_views = _heading_chunk("span", "10,000 views")
        result = extract_metadata([ld_chunk], [dom_channel, dom_views], "VideoObject")
        assert result["channel"] == "DomCreator"
        assert result["view_count"] == 10000

    def test_empty_chunks_no_crash(self):
        """Empty input → empty result."""
        result = _extract_video_meta_from_dom([])
        assert result == {}

    def test_not_applied_to_product(self):
        """VideoObject-only, not Product."""
        dom_channel = _heading_chunk("span", "SomeChannel", attrs={"class": "channel-name"})
        result = extract_metadata([], [dom_channel], "Product")
        assert "channel" not in result


# --- VideoObject integration ---


class TestVideoObjectIntegration:
    """End-to-end scenarios combining JSON-LD + OG + DOM."""

    def test_full_youtube_with_jsonld(self):
        """Complete JSON-LD + OG + DOM."""
        jsonld = {
            "@type": "VideoObject",
            "name": "How to Code",
            "description": "A tutorial",
            "author": {"@type": "Person", "name": "DevChannel"},
            "uploadDate": "2026-01-15",
            "duration": "PT15M30S",
            "thumbnailUrl": "https://img.youtube.com/thumb.jpg",
            "interactionStatistic": [
                {
                    "@type": "InteractionCounter",
                    "interactionType": {"@type": "WatchAction"},
                    "userInteractionCount": "1500000",
                },
            ],
        }
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        og = _og_meta_chunk({"og:site_name": "YouTube", "og:title": "How to Code"})
        result = extract_metadata([ld_chunk, og], [], "VideoObject")
        assert result["name"] == "How to Code"
        assert result["channel"] == "DevChannel"
        assert result["view_count"] == 1500000
        assert "channel" in result and result["channel"] != "YouTube"

    def test_og_only_no_jsonld(self):
        """OG-only page → channel absent (og:site_name no longer mapped)."""
        og = _og_meta_chunk(
            {
                "og:title": "Some Video",
                "og:site_name": "YouTube",
                "og:description": "desc",
            }
        )
        result = extract_metadata([og], [], "VideoObject")
        assert result["name"] == "Some Video"
        assert "channel" not in result

    def test_author_as_organization(self):
        """{"@type": "Organization", "name": "..."} works as channel."""
        jsonld = {
            "@type": "VideoObject",
            "name": "Corp Video",
            "author": {"@type": "Organization", "name": "BigCorp Media"},
        }
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        result = extract_metadata([ld_chunk], [], "VideoObject")
        assert result["channel"] == "BigCorp Media"

    def test_author_as_plain_string(self):
        """'author': 'PlainName' works as channel."""
        jsonld = {
            "@type": "VideoObject",
            "name": "Simple Video",
            "author": "PlainName",
        }
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        result = extract_metadata([ld_chunk], [], "VideoObject")
        assert result["channel"] == "PlainName"


# ---------------------------------------------------------------------------
# Amazon Price Extraction — nested HTML, pruned_html fallback, aria-label
# ---------------------------------------------------------------------------


class TestAmazonPriceExtraction:
    """Verify price extraction from Amazon-style nested HTML structures."""

    def test_nested_price_from_pruned_html(self):
        """a-price with nested a-offscreen span → price via pruned_html."""
        html = '<div><span class="a-price"><span class="a-offscreen">$249.00</span></span></div>'
        jsonld = {"@type": "Product", "name": "Test Product"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        result = extract_metadata([ld_chunk], [], "Product", pruned_html=html)
        assert result["price"] == 249.0

    def test_nested_price_no_offscreen_class(self):
        """Generic nesting: a-price with plain inner span."""
        html = '<div><span class="a-price"><span>$249.00</span></span></div>'
        result = extract_metadata([], [], "Product", pruned_html=html)
        assert result["price"] == 249.0

    def test_jsonld_price_wins_over_pruned_html(self):
        """JSON-LD price takes priority over pruned_html price."""
        html = '<span class="a-price"><span class="a-offscreen">$49.99</span></span>'
        jsonld = {"@type": "Product", "name": "Widget", "offers": {"price": "29.99"}}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        result = extract_metadata([ld_chunk], [], "Product", pruned_html=html)
        assert result["price"] == 29.99

    def test_optimistic_path_pruned_html_fallback(self):
        """source_hint='json_ld' + no price in JSON-LD → finds via pruned_html."""
        html = '<span class="a-price"><span class="a-offscreen">$199.00</span></span>'
        jsonld = {"@type": "Product", "name": "Gadget"}
        ld_chunk = _meta_chunk(json.dumps(jsonld), attrs={"type": "application/ld+json"})
        result = extract_metadata([ld_chunk], [], "Product", source_hint="json_ld", pruned_html=html)
        assert result["price"] == 199.0

    def test_aria_label_price_extraction(self):
        """aria-label on heading_chunk with empty text → price extracted."""
        chunk = _heading_chunk("span", "", attrs={"class": "a-price", "aria-label": "$249.00", "itemprop": "price"})
        result = extract_metadata([], [chunk], "Product")
        assert result["price"] == 249.0

    def test_data_attribute_price_extraction(self):
        """data-a-price attribute on heading_chunk with empty text → price extracted."""
        chunk = _heading_chunk("span", "", attrs={"class": "a-price", "data-a-price": "$199.99", "itemprop": "price"})
        result = extract_metadata([], [chunk], "Product")
        assert result["price"] == 199.99

    def test_pruned_html_none_no_crash(self):
        """Default None pruned_html → graceful, no crash."""
        result = extract_metadata([], [], "Product")
        assert "price" not in result

    def test_pruned_html_empty_string_no_crash(self):
        """Empty string pruned_html → graceful."""
        result = extract_metadata([], [], "Product", pruned_html="")
        assert "price" not in result

    def test_shipping_price_filtered_in_html(self):
        """Shipping-related prices in pruned_html should be excluded."""
        html = '<span class="a-price shipping">$5.99 shipping</span>'
        result = extract_metadata([], [], "Product", pruned_html=html)
        assert "price" not in result

    def test_non_product_schema_ignores_pruned_html(self):
        """Non-Product schema skips price fallback from pruned_html."""
        html = '<span class="a-price"><span class="a-offscreen">$249.00</span></span>'
        result = extract_metadata([], [], "NewsArticle", pruned_html=html)
        assert "price" not in result

    def test_extract_price_from_html_aria_label(self):
        """lxml function finds price from aria-label on price-classed element."""
        html = '<div><span class="a-price" aria-label="$99.99"></span></div>'
        assert _extract_price_from_html(html) == 99.99
