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
