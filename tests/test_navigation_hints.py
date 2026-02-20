"""Tests for navigation hints (pagination + filter metadata).

Tests:
- extract_pagination_structured: HTML pattern extraction
- _build_navigation_hints: ref matching, page_type filtering, filter refs
- Serializer integration: JSON + agent prompt rendering
"""

from __future__ import annotations

from pagemap import Interactable
from pagemap.i18n import get_locale
from pagemap.page_map_builder import _build_navigation_hints
from pagemap.pruned_context_builder import extract_pagination_structured

# ── Helpers ──────────────────────────────────────────────────────────


def _make_interactable(ref: int, name: str, region: str = "main") -> Interactable:
    return Interactable(
        ref=ref,
        role="button",
        name=name,
        affordance="click",
        region=region,
        tier=1,
    )


# ── TestExtractPaginationStructured ──────────────────────────────────


class TestExtractPaginationStructured:
    """Test structured pagination extraction from HTML."""

    def test_page_x_of_y_english(self):
        html = "<div>Page 3 of 25</div>"
        result = extract_pagination_structured(html)
        assert result["current_page"] == 3
        assert result["total_pages"] == 25

    def test_page_x_slash_y_korean(self):
        html = "<div>페이지 1 / 10</div>"
        result = extract_pagination_structured(html)
        assert result["current_page"] == 1
        assert result["total_pages"] == 10

    def test_page_x_slash_y_japanese(self):
        html = "<div>3 / 20 ページ</div>"
        result = extract_pagination_structured(html)
        assert result["current_page"] == 3
        assert result["total_pages"] == 20

    def test_page_x_slash_y_german(self):
        html = "<div>Seite 2 von 15</div>"
        result = extract_pagination_structured(html)
        assert result["current_page"] == 2
        assert result["total_pages"] == 15

    def test_total_items_korean(self):
        html = "<div>총 500건</div>"
        result = extract_pagination_structured(html)
        assert result["total_items"] == "총 500건"

    def test_total_items_english(self):
        html = "<div>1,234 results</div>"
        result = extract_pagination_structured(html)
        assert "1,234 results" in result["total_items"]

    def test_has_next_button(self):
        html = '<a aria-label="Next">Next</a>'
        result = extract_pagination_structured(html)
        assert result.get("has_next") is True

    def test_has_next_korean(self):
        html = "<button>다음</button>"
        result = extract_pagination_structured(html)
        assert result.get("has_next") is True

    def test_has_prev_button(self):
        html = '<a aria-label="Previous">Previous</a>'
        result = extract_pagination_structured(html)
        assert result.get("has_prev") is True

    def test_has_prev_korean(self):
        html = "<button>이전</button>"
        result = extract_pagination_structured(html)
        assert result.get("has_prev") is True

    def test_no_pagination_returns_empty(self):
        html = "<div>Hello world</div>"
        result = extract_pagination_structured(html)
        assert result == {}

    def test_url_page_param(self):
        html = '<a href="?page=5">5</a><a href="?page=10">10</a>'
        result = extract_pagination_structured(html)
        assert result["total_pages"] == 10

    def test_load_more_detected_as_next(self):
        html = "<button>더보기</button>"
        result = extract_pagination_structured(html)
        assert result.get("has_next") is True

    def test_combined_pagination(self):
        html = "<div>총 100건</div><div>Page 2 of 10</div><button>다음</button><button>이전</button>"
        result = extract_pagination_structured(html)
        assert result["current_page"] == 2
        assert result["total_pages"] == 10
        assert result["total_items"] == "총 100건"
        assert result["has_next"] is True
        assert result["has_prev"] is True

    def test_false_values_omitted(self):
        """has_next/has_prev should be absent (not False) when not detected."""
        html = "<div>Page 1 of 5</div>"
        result = extract_pagination_structured(html)
        assert "has_next" not in result
        assert "has_prev" not in result

    def test_locale_parameter_accepted(self):
        """Verify lc parameter is accepted (used for future locale-specific logic)."""
        lc = get_locale("en")
        html = "<div>Page 1 of 5</div>"
        result = extract_pagination_structured(html, lc=lc)
        assert result["current_page"] == 1


# ── TestMatchPaginationRefs ──────────────────────────────────────────


class TestMatchPaginationRefs:
    """Test interactable name matching for next/prev/load_more refs."""

    def test_next_ref_korean(self):
        items = [_make_interactable(1, "이전"), _make_interactable(2, "다음")]
        hints = _build_navigation_hints(items, "<div>Page 1 of 5</div>", "search_results")
        assert hints["pagination"]["next_ref"] == 2

    def test_next_ref_english(self):
        items = [_make_interactable(1, "Previous"), _make_interactable(2, "Next")]
        hints = _build_navigation_hints(items, "<div>Page 1 of 5</div>", "search_results")
        assert hints["pagination"]["next_ref"] == 2

    def test_next_ref_japanese(self):
        items = [_make_interactable(1, "前へ"), _make_interactable(2, "次へ")]
        hints = _build_navigation_hints(items, "<div>Page 1 of 5</div>", "listing")
        assert hints["pagination"]["next_ref"] == 2

    def test_prev_ref_matched(self):
        items = [_make_interactable(1, "이전 페이지"), _make_interactable(2, "다음 페이지")]
        hints = _build_navigation_hints(items, "<div>Page 2 of 5</div>", "search_results")
        assert hints["pagination"]["prev_ref"] == 1

    def test_load_more_ref(self):
        items = [_make_interactable(1, "더보기")]
        hints = _build_navigation_hints(items, "", "listing")
        assert hints["pagination"]["load_more_ref"] == 1

    def test_case_insensitive_matching(self):
        items = [_make_interactable(1, "NEXT PAGE")]
        # "next" is substring of "NEXT PAGE" (lowered)
        hints = _build_navigation_hints(items, "", "search_results")
        assert hints["pagination"]["next_ref"] == 1

    def test_first_match_wins(self):
        items = [
            _make_interactable(1, "다음 (page 2)"),
            _make_interactable(2, "다음 (page 3)"),
        ]
        hints = _build_navigation_hints(items, "", "search_results")
        assert hints["pagination"]["next_ref"] == 1

    def test_no_match_no_ref(self):
        items = [_make_interactable(1, "Submit"), _make_interactable(2, "Cancel")]
        hints = _build_navigation_hints(items, "", "listing")
        pag = hints.get("pagination", {})
        assert "next_ref" not in pag
        assert "prev_ref" not in pag


# ── TestFilterHints ──────────────────────────────────────────────────


class TestFilterHints:
    """Test complementary region detection for filter refs."""

    def test_complementary_region_detected(self):
        items = [
            _make_interactable(1, "카테고리", region="complementary"),
            _make_interactable(2, "가격 필터", region="complementary"),
            _make_interactable(3, "Buy Now", region="main"),
        ]
        hints = _build_navigation_hints(items, "", "search_results")
        assert hints["filters"]["filter_refs"] == [1, 2]

    def test_max_10_filter_refs(self):
        items = [_make_interactable(i, f"Filter {i}", region="complementary") for i in range(1, 15)]
        hints = _build_navigation_hints(items, "", "listing")
        assert len(hints["filters"]["filter_refs"]) == 10

    def test_no_complementary_no_filters(self):
        items = [_make_interactable(1, "Buy", region="main")]
        hints = _build_navigation_hints(items, "", "search_results")
        assert "filters" not in hints


# ── TestBuildNavigationHints ─────────────────────────────────────────


class TestBuildNavigationHints:
    """Integration tests for _build_navigation_hints."""

    def test_non_listing_page_returns_empty(self):
        items = [_make_interactable(1, "다음")]
        assert _build_navigation_hints(items, "", "product_detail") == {}

    def test_article_page_returns_empty(self):
        items = [_make_interactable(1, "Next")]
        assert _build_navigation_hints(items, "", "article") == {}

    def test_unknown_page_returns_empty(self):
        items = [_make_interactable(1, "Next")]
        assert _build_navigation_hints(items, "", "unknown") == {}

    def test_search_results_page_produces_hints(self):
        items = [_make_interactable(1, "다음")]
        hints = _build_navigation_hints(items, "", "search_results")
        assert "pagination" in hints

    def test_listing_page_produces_hints(self):
        items = [_make_interactable(1, "Next")]
        hints = _build_navigation_hints(items, "", "listing")
        assert "pagination" in hints

    def test_empty_hints_when_no_pagination_or_filters(self):
        items = [_make_interactable(1, "Buy", region="main")]
        hints = _build_navigation_hints(items, "<div>Hello</div>", "search_results")
        assert hints == {}

    def test_full_integration(self):
        items = [
            _make_interactable(1, "카테고리 필터", region="complementary"),
            _make_interactable(2, "가격대", region="complementary"),
            _make_interactable(3, "이전"),
            _make_interactable(4, "다음"),
        ]
        html = "<div>Page 2 of 10</div><div>총 100건</div><button>다음</button><button>이전</button>"
        hints = _build_navigation_hints(items, html, "search_results")

        assert hints["pagination"]["current_page"] == 2
        assert hints["pagination"]["total_pages"] == 10
        assert hints["pagination"]["next_ref"] == 4
        assert hints["pagination"]["prev_ref"] == 3
        assert hints["filters"]["filter_refs"] == [1, 2]
