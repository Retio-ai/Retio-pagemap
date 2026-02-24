"""Tests for listing/search_results pruning enhancements.

Covers:
- Card detection from metadata (JSON-LD ItemList)
- Card detection from chunks (LIST parsing, xpath grouping, adjacent pairing)
- Product card cascade priority
- Deduplication
- Card serialization
- Pagination info extraction (Korean + English patterns)
- Integration: build_pruned_context for listing/search_results
- Regression: product_detail pages unaffected
"""

from __future__ import annotations

import json

from pagemap.pruned_context_builder import (
    _detect_cards_from_chunks,
    _detect_cards_from_metadata,
    _detect_product_cards,
    _extract_pagination_info,
    _serialize_cards,
)
from pagemap.pruning import ChunkType, HtmlChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta_chunk(json_ld: dict) -> HtmlChunk:
    """Create a META HtmlChunk containing JSON-LD."""
    return HtmlChunk(
        xpath="/html/head/script[1]",
        html=f'<script type="application/ld+json">{json.dumps(json_ld)}</script>',
        text=json.dumps(json_ld),
        tag="script",
        chunk_type=ChunkType.META,
        attrs={"type": "application/ld+json"},
    )


def _make_list_chunk(html: str, text: str = "", xpath: str = "/html/body/ul[1]") -> HtmlChunk:
    return HtmlChunk(
        xpath=xpath,
        html=html,
        text=text or "",
        tag="ul",
        chunk_type=ChunkType.LIST,
        parent_xpath="/html/body",
    )


def _make_text_chunk(text: str, xpath: str = "/html/body/div[1]", parent_xpath: str = "/html/body") -> HtmlChunk:
    return HtmlChunk(
        xpath=xpath,
        html=f"<div>{text}</div>",
        text=text,
        tag="div",
        chunk_type=ChunkType.TEXT_BLOCK,
        parent_xpath=parent_xpath,
    )


# ---------------------------------------------------------------------------
# 1. _detect_cards_from_metadata
# ---------------------------------------------------------------------------


class TestDetectCardsFromMetadata:
    def test_itemlist_basic(self):
        metadata = {
            "items": [
                {"name": "Nike Air Max", "price": 189000, "currency": "KRW", "brand": "Nike"},
                {"name": "Adidas Superstar", "price": 129000, "currency": "KRW"},
            ]
        }
        cards = _detect_cards_from_metadata(metadata)
        assert len(cards) == 2
        assert cards[0]["name"] == "Nike Air Max"
        assert cards[0]["price"] == 189000
        assert cards[1]["name"] == "Adidas Superstar"

    def test_no_items_key(self):
        metadata = {"name": "Product Page"}
        assert _detect_cards_from_metadata(metadata) == []

    def test_none_metadata(self):
        assert _detect_cards_from_metadata(None) == []

    def test_empty_items(self):
        assert _detect_cards_from_metadata({"items": []}) == []

    def test_items_without_name_skipped(self):
        metadata = {"items": [{"price": 100}, {"name": "Valid", "price": 200}]}
        cards = _detect_cards_from_metadata(metadata)
        assert len(cards) == 1
        assert cards[0]["name"] == "Valid"


# ---------------------------------------------------------------------------
# 2. _detect_cards_from_chunks
# ---------------------------------------------------------------------------


class TestDetectCardsFromChunks:
    def test_list_chunk_li_parsing(self):
        html = """<ul>
            <li>나이키 에어맥스 90 189,000원</li>
            <li>아디다스 슈퍼스타 129,000원</li>
            <li>뉴발란스 530 109,000원</li>
        </ul>"""
        chunks = [_make_list_chunk(html)]
        cards = _detect_cards_from_chunks(chunks)
        assert len(cards) == 3
        assert "나이키" in cards[0]["name"]
        assert "189,000" in cards[0]["price_text"]

    def test_adjacent_name_price_pairing(self):
        """Strategy 3: adjacent lines where name is followed by price."""
        chunks = [
            _make_text_chunk("나이키 에어맥스 90", xpath="/body/div[1]", parent_xpath="/body/section1"),
            _make_text_chunk("189,000원", xpath="/body/div[2]", parent_xpath="/body/section2"),
            _make_text_chunk("아디다스 슈퍼스타", xpath="/body/div[3]", parent_xpath="/body/section3"),
            _make_text_chunk("129,000원", xpath="/body/div[4]", parent_xpath="/body/section4"),
        ]
        cards = _detect_cards_from_chunks(chunks)
        assert len(cards) >= 2
        assert any("나이키" in c.get("name", "") for c in cards)

    def test_parent_xpath_grouping(self):
        """Strategy 2: chunks grouped by parent_xpath."""
        parent = "/html/body/div/ul"
        chunks = [
            _make_text_chunk("나이키 에어맥스", xpath=f"{parent}/li[1]/a", parent_xpath=parent),
            _make_text_chunk("189,000원", xpath=f"{parent}/li[1]/span", parent_xpath=parent),
            _make_text_chunk("아디다스 스탠스미스", xpath=f"{parent}/li[2]/a", parent_xpath=parent),
            _make_text_chunk("129,000원", xpath=f"{parent}/li[2]/span", parent_xpath=parent),
        ]
        cards = _detect_cards_from_chunks(chunks)
        assert len(cards) >= 2

    def test_empty_chunks(self):
        assert _detect_cards_from_chunks([]) == []

    def test_no_prices_returns_empty(self):
        chunks = [_make_text_chunk("Hello world"), _make_text_chunk("Some text")]
        cards = _detect_cards_from_chunks(chunks)
        assert cards == []


# ---------------------------------------------------------------------------
# 3. _detect_product_cards (cascade)
# ---------------------------------------------------------------------------


class TestDetectProductCards:
    def test_metadata_preferred_over_chunks(self):
        metadata = {
            "items": [
                {"name": "From JSON-LD", "price": 100},
            ]
        }
        chunks = [_make_list_chunk("<ul><li>From Chunk 50,000원</li></ul>")]
        cards = _detect_product_cards(chunks, metadata)
        assert len(cards) == 1
        assert cards[0]["name"] == "From JSON-LD"

    def test_chunks_fallback_when_no_metadata(self):
        html = "<ul><li>상품A 10,000원</li><li>상품B 20,000원</li></ul>"
        chunks = [_make_list_chunk(html)]
        cards = _detect_product_cards(chunks, None)
        assert len(cards) == 2

    def test_deduplication(self):
        metadata = {
            "items": [
                {"name": "Same Product", "price_text": "10,000원"},
                {"name": "Same Product", "price_text": "10,000원"},  # dup
                {"name": "Different Product", "price_text": "20,000원"},
            ]
        }
        cards = _detect_product_cards(None, metadata)
        assert len(cards) == 2

    def test_none_both(self):
        cards = _detect_product_cards(None, None)
        assert cards == []


# ---------------------------------------------------------------------------
# 4. _serialize_cards
# ---------------------------------------------------------------------------


class TestSerializeCards:
    def test_basic_format(self):
        cards = [
            {"name": "Nike Air Max", "price_text": "189,000원", "brand": "Nike"},
            {"name": "Adidas Superstar", "price_text": "129,000원"},
        ]
        result = _serialize_cards(cards)
        assert "1. Nike Air Max | 189,000원 | Nike" in result
        assert "2. Adidas Superstar | 129,000원" in result

    def test_max_cards_limit(self):
        cards = [{"name": f"Product {i}", "price_text": f"{i},000원"} for i in range(20)]
        result = _serialize_cards(cards, max_cards=5)
        assert "5. " in result
        assert "6. " not in result
        assert "+15 more" in result

    def test_price_from_numeric(self):
        cards = [{"name": "상품", "price": 189000, "currency": "KRW"}]
        result = _serialize_cards(cards)
        assert "189,000원" in result

    def test_usd_price(self):
        cards = [{"name": "Product", "price": 99.99, "currency": "USD"}]
        result = _serialize_cards(cards)
        assert "$99.99" in result

    def test_empty_cards(self):
        assert _serialize_cards([]) == ""

    def test_name_only(self):
        cards = [{"name": "Just a name"}]
        result = _serialize_cards(cards)
        assert "1. Just a name" in result


# ---------------------------------------------------------------------------
# 5. _extract_pagination_info
# ---------------------------------------------------------------------------


class TestExtractPaginationInfo:
    # Korean patterns
    def test_korean_total(self):
        html = "<div>총 500건</div>"
        result = _extract_pagination_info(html)
        assert "총 500건" in result
        assert "Pagination" in result

    def test_korean_total_with_space(self):
        html = "<div>총 1,234건</div>"
        result = _extract_pagination_info(html)
        assert "1,234건" in result

    def test_korean_items(self):
        html = "<div>120개의 상품</div>"
        result = _extract_pagination_info(html)
        assert "120개의 상품" in result

    # English patterns
    def test_english_results(self):
        html = "<div>1,523 results</div>"
        result = _extract_pagination_info(html)
        assert "1,523 results" in result

    def test_english_of_pattern(self):
        html = "<div>1-20 of 1,523</div>"
        result = _extract_pagination_info(html)
        assert "1-20 of 1,523" in result

    def test_items_pattern(self):
        html = "<div>500 items</div>"
        result = _extract_pagination_info(html)
        assert "500 items" in result

    # Page parameter URLs
    def test_page_param(self):
        html = '<a href="/search?page=25">25</a>'
        result = _extract_pagination_info(html)
        assert "~25pages" in result

    def test_p_param(self):
        html = '<a href="/list?category=shoes&p=10">10</a>'
        result = _extract_pagination_info(html)
        assert "~10pages" in result

    def test_multiple_page_params_max(self):
        html = '<a href="?page=1">1</a><a href="?page=5">5</a><a href="?page=10">10</a>'
        result = _extract_pagination_info(html)
        assert "~10pages" in result

    # Next button detection
    def test_next_button_korean(self):
        html = '<a class="btn">다음</a>'
        # Need wrapping with > <
        html = '<a class="btn">다음</a>'
        result = _extract_pagination_info(html)
        assert "Next available" in result

    def test_next_button_english(self):
        html = '<a class="pagination-next">Next</a>'
        result = _extract_pagination_info(html)
        assert "Next available" in result

    def test_load_more(self):
        html = "<button>Load more</button>"
        result = _extract_pagination_info(html)
        assert "Next available" in result

    def test_show_more(self):
        html = "<button>Show more</button>"
        result = _extract_pagination_info(html)
        assert "Next available" in result

    def test_aria_label_next(self):
        html = '<a aria-label="Next">→</a>'
        result = _extract_pagination_info(html)
        assert "Next available" in result

    # Page X of Y patterns
    def test_page_of_pattern(self):
        html = "<div>Page 2 of 50</div>"
        result = _extract_pagination_info(html)
        assert "~50pages" in result

    def test_korean_page_pattern(self):
        html = "<div>페이지 3/20</div>"
        result = _extract_pagination_info(html)
        assert "~20pages" in result

    # Combined
    def test_combined_info(self):
        html = """
        <div>총 500건</div>
        <a href="?page=25">25</a>
        <a class="next-btn">다음</a>
        """
        result = _extract_pagination_info(html)
        assert "~25pages" in result
        assert "500건" in result
        assert "Next available" in result

    # No info
    def test_no_pagination(self):
        html = "<div>Hello world</div>"
        result = _extract_pagination_info(html)
        assert result == ""

    def test_empty_html(self):
        assert _extract_pagination_info("") == ""

    # Next class pattern
    def test_next_class(self):
        html = '<a class="pagination-next" href="/page/2">→</a>'
        result = _extract_pagination_info(html)
        assert "Next available" in result


# ---------------------------------------------------------------------------
# 6. Integration: metadata.py ItemList extraction
# ---------------------------------------------------------------------------


class TestMetadataItemList:
    def test_itemlist_json_ld(self):
        from pagemap.metadata import _find_type_in_jsonld, _parse_json_ld_itemlist

        json_ld = {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "item": {
                        "@type": "Product",
                        "name": "Nike Air Max 90",
                        "offers": {"@type": "Offer", "price": "189000", "priceCurrency": "KRW"},
                        "brand": {"@type": "Brand", "name": "Nike"},
                    },
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "item": {
                        "@type": "Product",
                        "name": "Adidas Superstar",
                        "offers": {"@type": "Offer", "price": "129000", "priceCurrency": "KRW"},
                    },
                },
            ],
        }
        assert _find_type_in_jsonld(json_ld, ("ItemList",)) is not None

        items = _parse_json_ld_itemlist([json_ld])
        assert len(items) == 2
        assert items[0]["name"] == "Nike Air Max 90"
        assert items[0]["price"] == 189000.0
        assert items[0]["brand"] == "Nike"
        assert items[0]["position"] == 1

    def test_itemlist_in_graph(self):
        from pagemap.metadata import _find_type_in_jsonld

        json_ld = {
            "@graph": [
                {"@type": "WebPage"},
                {"@type": "ItemList", "itemListElement": []},
            ]
        }
        assert _find_type_in_jsonld(json_ld, ("ItemList",)) is not None

    def test_no_itemlist(self):
        from pagemap.metadata import _find_type_in_jsonld

        assert _find_type_in_jsonld({"@type": "Product"}, ("ItemList",)) is None

    def test_extract_metadata_includes_items(self):
        from pagemap.metadata import extract_metadata

        json_ld = {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "item": {"@type": "Product", "name": "Test Product"},
                }
            ],
        }
        meta_chunks = [_make_meta_chunk(json_ld)]
        result = extract_metadata(meta_chunks, [], "Product")
        assert "items" in result
        assert len(result["items"]) == 1


# ---------------------------------------------------------------------------
# 7. Integration: build_pruned_context
# ---------------------------------------------------------------------------


class TestBuildPrunedContextIntegration:
    """Test that build_pruned_context produces structured output for listing/search pages."""

    def _make_listing_html(self) -> str:
        """Create a minimal listing HTML with product list and pagination."""
        items_json = json.dumps(
            {
                "@type": "ItemList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": i,
                        "item": {
                            "@type": "Product",
                            "name": f"상품 {i}",
                            "offers": {
                                "@type": "Offer",
                                "price": str(i * 10000),
                                "priceCurrency": "KRW",
                            },
                        },
                    }
                    for i in range(1, 6)
                ],
            }
        )
        return f"""<!DOCTYPE html>
<html><head>
<script type="application/ld+json">{items_json}</script>
</head><body>
<main>
<h1>베스트셀러</h1>
<div>총 100건 | 정렬: 인기순</div>
<ul>
  <li>상품 1 - 10,000원</li>
  <li>상품 2 - 20,000원</li>
  <li>상품 3 - 30,000원</li>
  <li>상품 4 - 40,000원</li>
  <li>상품 5 - 50,000원</li>
</ul>
<nav>
  <a href="?page=1">1</a>
  <a href="?page=2">2</a>
  <a href="?page=10">10</a>
  <a aria-label="Next">다음</a>
</nav>
</main>
</body></html>"""

    def test_listing_has_cards(self):
        from pagemap.pruned_context_builder import build_pruned_context

        html = self._make_listing_html()
        context, token_count, metadata = build_pruned_context(html, page_type="listing", schema_name="Product")
        # Should contain numbered product cards
        assert "1." in context
        assert "상품" in context

    def test_listing_has_pagination(self):
        from pagemap.pruned_context_builder import build_pruned_context

        html = self._make_listing_html()
        context, token_count, metadata = build_pruned_context(html, page_type="listing", schema_name="Product")
        assert "Pagination" in context
        assert "Next available" in context

    def test_search_results_has_cards(self):
        from pagemap.pruned_context_builder import build_pruned_context

        html = self._make_listing_html().replace("베스트셀러", "검색결과: 신발")
        context, token_count, metadata = build_pruned_context(html, page_type="search_results", schema_name="Product")
        assert "1." in context
        assert "상품" in context

    def test_search_results_has_pagination(self):
        from pagemap.pruned_context_builder import build_pruned_context

        html = self._make_listing_html().replace("베스트셀러", "검색결과: 신발")
        context, token_count, metadata = build_pruned_context(html, page_type="search_results", schema_name="Product")
        assert "Pagination" in context


# ---------------------------------------------------------------------------
# 8. Regression: product_detail unaffected
# ---------------------------------------------------------------------------


class TestProductDetailRegression:
    def test_product_detail_unchanged(self):
        """product_detail compression should NOT use card detection."""
        from pagemap.pruned_context_builder import build_pruned_context

        html = """<!DOCTYPE html>
<html><head>
<script type="application/ld+json">
{"@type": "Product", "name": "Nike Air Max 90",
 "offers": {"@type": "Offer", "price": "189000", "priceCurrency": "KRW"},
 "brand": {"@type": "Brand", "name": "Nike"}}
</script>
</head><body>
<main>
<h1>Nike Air Max 90</h1>
<div>189,000원</div>
<div>사이즈: 250 255 260</div>
</main>
</body></html>"""
        context, token_count, metadata = build_pruned_context(html, page_type="product_detail", schema_name="Product")
        # Should have metadata-based extraction
        assert "Nike Air Max 90" in context
        assert "189,000" in context
        # Should NOT have pagination
        assert "Pagination" not in context


# ---------------------------------------------------------------------------
# 9. PruningResult.selected_chunks field
# ---------------------------------------------------------------------------


class TestPruningResultSelectedChunks:
    def test_selected_chunks_populated(self):
        from pagemap.pruning.pipeline import prune_page

        html = """<!DOCTYPE html>
<html><head></head><body>
<main><h1>Test</h1><p>Content here</p></main>
</body></html>"""
        result = prune_page(html, "test", "page1", "Product")
        # selected_chunks should be a list (possibly empty if no chunks selected)
        assert isinstance(result.selected_chunks, list)

    def test_default_empty(self):
        from pagemap.pruning.pipeline import PruningResult

        result = PruningResult(site_id="test", page_id="page1")
        assert result.selected_chunks == []
