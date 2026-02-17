"""Tests for pruned_context_builder i18n integration.

Covers:
- Locale-specific output labels (title, rating, brand, pagination)
- Universal keyword detection (ja/fr/de/en keywords in search/listing)
- build_pruned_context locale parameter backward compat (None → "ko")
- Pagination i18n output
"""

from __future__ import annotations

import json

import pytest

from pagemap.i18n import get_locale
from pagemap.pruned_context_builder import (
    PRICE_PATTERN,
    RATING_PATTERN,
    _extract_pagination_info,
    _serialize_cards,
    build_pruned_context,
)

# ---------------------------------------------------------------------------
# PRICE_PATTERN — multilingual
# ---------------------------------------------------------------------------


class TestPricePatternMultilingual:
    @pytest.mark.parametrize(
        "text",
        [
            "₩13,900",
            "13,900원",
            "3,990円",
            "¥3,990",
            "£29.99",
            "€49.99",
            "$29.99",
            "189,000",
            "定価",
            "セール価格",
            "通常価格",
            "prix",
            "solde",
            "Originalpreis",
            "Sonderpreis",
            "정가",
            "할인가",
            "판매가",
            "regular price",
            "sale price",
            "original price",
            "list price",
        ],
    )
    def test_matches(self, text):
        assert PRICE_PATTERN.search(text)


# ---------------------------------------------------------------------------
# RATING_PATTERN — multilingual
# ---------------------------------------------------------------------------


class TestRatingPatternMultilingual:
    @pytest.mark.parametrize(
        "text",
        [
            "★★★★☆",
            "평점 4.5",
            "4.5점",
            "리뷰 42",
            "評価 4.2",
            "レビュー",
            "étoile",
            "Bewertung 4.5",
            "Sterne",
        ],
    )
    def test_matches(self, text):
        assert RATING_PATTERN.search(text)

    def test_note_removed(self):
        """'note' removed from RATING_PATTERN to avoid English 'note' conflict."""
        assert not RATING_PATTERN.search("note")


# ---------------------------------------------------------------------------
# _serialize_cards — locale-specific overflow
# ---------------------------------------------------------------------------


class TestSerializeCardsI18n:
    def test_overflow_ko(self):
        cards = [{"name": f"Product {i}", "price_text": f"{i},000원"} for i in range(20)]
        result = _serialize_cards(cards, max_cards=5, lc=get_locale("ko"))
        assert "외 15건" in result

    def test_overflow_en(self):
        cards = [{"name": f"Product {i}", "price_text": f"${i}.00"} for i in range(20)]
        result = _serialize_cards(cards, max_cards=5, lc=get_locale("en"))
        assert "+15 more" in result

    def test_overflow_ja(self):
        cards = [{"name": f"Product {i}", "price_text": f"{i},000円"} for i in range(20)]
        result = _serialize_cards(cards, max_cards=5, lc=get_locale("ja"))
        assert "他15件" in result

    def test_overflow_fr(self):
        cards = [{"name": f"Product {i}", "price_text": f"€{i}.00"} for i in range(20)]
        result = _serialize_cards(cards, max_cards=5, lc=get_locale("fr"))
        assert "+15 de plus" in result

    def test_overflow_de(self):
        cards = [{"name": f"Product {i}", "price_text": f"€{i}.00"} for i in range(20)]
        result = _serialize_cards(cards, max_cards=5, lc=get_locale("de"))
        assert "+15 weitere" in result


# ---------------------------------------------------------------------------
# _extract_pagination_info — locale-specific labels
# ---------------------------------------------------------------------------


class TestPaginationI18n:
    def test_ko_default(self):
        html = '<a href="?page=10">10</a><a>다음</a>'
        result = _extract_pagination_info(html)
        assert "페이지네이션" in result
        assert "페이지" in result
        assert "다음 있음" in result

    def test_en_locale(self):
        lc = get_locale("en")
        html = '<a href="?page=10">10</a><a>Next</a>'
        result = _extract_pagination_info(html, lc=lc)
        assert "Pagination" in result
        assert "pages" in result
        assert "Next available" in result

    def test_ja_locale(self):
        lc = get_locale("ja")
        html = '<a href="?page=10">10</a><a>次へ</a>'
        result = _extract_pagination_info(html, lc=lc)
        assert "ページネーション" in result
        assert "ページ" in result
        assert "次あり" in result

    def test_fr_locale(self):
        lc = get_locale("fr")
        html = '<a href="?page=10">10</a><a>Suivant</a>'
        result = _extract_pagination_info(html, lc=lc)
        assert "Pagination" in result
        assert "Suivant disponible" in result

    def test_de_locale(self):
        lc = get_locale("de")
        html = '<a href="?page=10">10</a><a>Weiter</a>'
        result = _extract_pagination_info(html, lc=lc)
        assert "Seitennavigation" in result
        assert "Weiter verfügbar" in result

    # Multilingual next button detection
    @pytest.mark.parametrize(
        "html",
        [
            "<a>次へ</a>",
            "<a>次のページ</a>",
            "<button>もっと見る</button>",
            "<button>さらに表示</button>",
            "<a>Suivant</a>",
            "<a>Page suivante</a>",
            "<button>Voir plus</button>",
            "<a>Weiter</a>",
            "<a>Nächste Seite</a>",
            "<button>Mehr laden</button>",
            "<button>Mehr anzeigen</button>",
        ],
    )
    def test_multilingual_next_detected(self, html):
        result = _extract_pagination_info(html)
        assert result, f"Expected pagination info for: {html}"

    # Multilingual total count
    @pytest.mark.parametrize(
        "html,expected_fragment",
        [
            ("<div>120件の商品</div>", "120件の商品"),
            ("<div>500 résultats</div>", "500 résultats"),
            ("<div>350 Ergebnisse</div>", "350 Ergebnisse"),
            ("<div>200 Produkte</div>", "200 Produkte"),
        ],
    )
    def test_multilingual_total_count(self, html, expected_fragment):
        result = _extract_pagination_info(html)
        assert expected_fragment in result

    # Page X of Y — multilingual
    def test_page_of_ja(self):
        html = "<div>3/20ページ</div>"
        result = _extract_pagination_info(html)
        assert "20" in result

    def test_seite_von_de(self):
        html = "<div>Seite 3 von 20</div>"
        result = _extract_pagination_info(html)
        assert "20" in result


# ---------------------------------------------------------------------------
# build_pruned_context — locale parameter
# ---------------------------------------------------------------------------


class TestBuildPrunedContextLocale:
    def _make_product_html(self) -> str:
        json_ld = json.dumps(
            {
                "@type": "Product",
                "name": "Nike Air Max 90",
                "offers": {"@type": "Offer", "price": "189000", "priceCurrency": "KRW"},
                "brand": {"@type": "Brand", "name": "Nike"},
                "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.5", "reviewCount": "42"},
            }
        )
        return f"""<!DOCTYPE html>
<html><head>
<script type="application/ld+json">{json_ld}</script>
</head><body><main>
<h1>Nike Air Max 90</h1>
<div>189,000원</div>
</main></body></html>"""

    def test_default_locale_ko(self):
        """locale=None → Korean labels (backward compat)."""
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product")
        assert "제목:" in context
        assert "평점:" in context
        assert "브랜드:" in context

    def test_locale_en(self):
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product", locale="en")
        assert "Title:" in context
        assert "Rating:" in context
        assert "Brand:" in context

    def test_locale_ja(self):
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product", locale="ja")
        assert "タイトル:" in context
        assert "評価:" in context
        assert "ブランド:" in context

    def test_locale_fr(self):
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product", locale="fr")
        assert "Titre:" in context
        assert "Note:" in context
        assert "Marque:" in context

    def test_locale_de(self):
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product", locale="de")
        assert "Titel:" in context
        assert "Bewertung:" in context
        assert "Marke:" in context

    def test_review_template_en(self):
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product", locale="en")
        assert "(42 reviews)" in context

    def test_review_template_ja(self):
        html = self._make_product_html()
        context, _, _ = build_pruned_context(html, page_type="product_detail", schema_name="Product", locale="ja")
        assert "(42件のレビュー)" in context

    def test_article_title_locale(self):
        html = """<!DOCTYPE html><html><head></head><body><main>
        <h1>Breaking News: Market Update</h1>
        <p>2026-02-15</p>
        <p>The stock market saw significant movement today following the announcement of new trade policies.</p>
        </main></body></html>"""
        context, _, _ = build_pruned_context(html, page_type="article", schema_name="NewsArticle", locale="en")
        assert "Title:" in context

    def test_listing_pagination_locale(self):
        items_json = json.dumps(
            {
                "@type": "ItemList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "item": {
                            "@type": "Product",
                            "name": "Product 1",
                            "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD"},
                        },
                    }
                ],
            }
        )
        html = f"""<!DOCTYPE html>
<html><head>
<script type="application/ld+json">{items_json}</script>
</head><body><main>
<h1>Best Sellers</h1>
<a href="?page=10">10</a>
<a>Next</a>
</main></body></html>"""
        context, _, _ = build_pruned_context(html, page_type="listing", schema_name="Product", locale="en")
        assert "Pagination" in context
        assert "Next available" in context
