"""Tests for benchmark failure fixes.

Covers:
1. COS page type detection (/product. pattern)
2. Offline interactable extraction from HTML
3. CSS class-based product detection in pruner
"""

from __future__ import annotations

from pagemap.page_map_builder import (
    _extract_interactables_from_html,
    detect_page_type,
)
from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.pruner import _match_product

# ── Fix 1: COS page type detection ──────────────────────────────────


class TestPageTypeDetection:
    """COS product URLs should be detected as product_detail, not listing."""

    def test_cos_product_url(self):
        url = "https://www.cos.com/ko-kr/women/denim-edit/product.facade-straight-leg-jeans-dusty-blue.1205065015.html"
        assert detect_page_type(url) == "product_detail"

    def test_cos_product_url_with_color(self):
        url = "https://www.cos.com/en-gb/men/menswear/product.relaxed-fit-cotton-shirt-white.0123456789.html"
        assert detect_page_type(url) == "product_detail"

    def test_cos_listing_still_works(self):
        """COS listing pages (without /product.) should stay as listing."""
        url = "https://www.cos.com/ko-kr/women/denim-edit/"
        assert detect_page_type(url) == "listing"

    def test_standard_product_urls_unchanged(self):
        """Existing product URL patterns should still work."""
        cases = [
            ("https://www.coupang.com/vp/products/1234", "product_detail"),
            ("https://www.29cm.co.kr/products/1234", "product_detail"),
            ("https://www.musinsa.com/goods/1234", "product_detail"),
            ("https://www.zara.com/kr/ko/search?searchTerm=jacket", "search_results"),
            ("https://www.nike.com/kr/w/men", "listing"),
        ]
        for url, expected in cases:
            assert detect_page_type(url) == expected, f"Failed for {url}"


# ── Fix 2: Offline interactable extraction ──────────────────────────


class TestOfflineInteractableExtraction:
    """HTML-based static interactable extraction for offline mode."""

    def test_button_extraction(self):
        html = """
        <html><body>
        <button>장바구니 담기</button>
        <button aria-label="구매하기">Buy Now</button>
        </body></html>
        """
        items = _extract_interactables_from_html(html)
        names = [i.name for i in items]
        assert "장바구니 담기" in names
        assert "구매하기" in names

    def test_cta_link_extraction(self):
        html = """
        <html><body>
        <a href="/cart">장바구니에 담기</a>
        <a href="/products/123">일반 상품 링크</a>
        <a href="/wishlist" aria-label="위시리스트">♡</a>
        </body></html>
        """
        items = _extract_interactables_from_html(html)
        names = [i.name for i in items]
        # CTA links kept
        assert "장바구니에 담기" in names
        assert "위시리스트" in names
        # Non-CTA link excluded
        assert "일반 상품 링크" not in names

    def test_input_extraction(self):
        html = """
        <html><body>
        <input type="search" placeholder="검색어를 입력하세요">
        <input type="hidden" name="csrf">
        </body></html>
        """
        items = _extract_interactables_from_html(html)
        assert len(items) == 1
        assert items[0].role == "searchbox"
        assert items[0].affordance == "type"

    def test_select_extraction(self):
        html = """
        <html><body>
        <select aria-label="사이즈 선택">
            <option>S</option>
            <option>M</option>
            <option>L</option>
            <option>XL</option>
        </select>
        </body></html>
        """
        items = _extract_interactables_from_html(html)
        assert len(items) == 1
        assert items[0].role == "combobox"
        assert items[0].options == ["S", "M", "L", "XL"]

    def test_hidden_button_excluded(self):
        html = '<button type="hidden">Hidden</button>'
        items = _extract_interactables_from_html(html)
        assert len(items) == 0

    def test_deduplication(self):
        html = """
        <button>장바구니 담기</button>
        <button>장바구니 담기</button>
        """
        items = _extract_interactables_from_html(html)
        assert len(items) == 1

    def test_english_cta(self):
        html = """
        <a href="/cart">Add to Cart</a>
        <a href="/bag">Add to Bag</a>
        <button>Buy Now</button>
        """
        items = _extract_interactables_from_html(html)
        assert len(items) == 3

    def test_sequential_ref_numbers(self):
        html = """
        <button>Button A</button>
        <button>Button B</button>
        <button>Button C</button>
        """
        items = _extract_interactables_from_html(html)
        refs = [i.ref for i in items]
        assert refs == [1, 2, 3]


# ── Fix 3: CSS class-based product detection in pruner ──────────────


class TestProductClassDetection:
    """Product card detection via CSS class patterns."""

    def _make_chunk(self, text: str, tag: str = "div", class_name: str = "") -> HtmlChunk:
        attrs = {}
        if class_name:
            attrs["class"] = class_name
        return HtmlChunk(
            xpath="/html/body/div",
            html=f"<{tag}>{text}</{tag}>",
            text=text,
            tag=tag,
            chunk_type=ChunkType.TEXT_BLOCK,
            attrs=attrs,
        )

    def test_product_name_class(self):
        """Coupang ProductUnit_productNameV2 pattern."""
        chunk = self._make_chunk(
            "삼양 1963 라면 131g, 4개",
            class_name="ProductUnit_productNameV2__cV9cw",
        )
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "name" in fields

    def test_product_card_class(self):
        """Generic product card container."""
        chunk = self._make_chunk(
            "상품 정보",
            class_name="product-card-info",
        )
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "product_card" in fields

    def test_item_detail_class(self):
        chunk = self._make_chunk(
            "상품 상세",
            class_name="item-detail-summary",
        )
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "product_card" in fields

    def test_goods_name_class(self):
        chunk = self._make_chunk(
            "나이키 에어맥스",
            class_name="goods_name",
        )
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "name" in fields

    def test_price_class_detection(self):
        """Price class should be detected."""
        chunk = self._make_chunk(
            "5,530원",
            class_name="PriceArea_priceArea__NntJz",
        )
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "price" in fields

    def test_no_false_positive_on_unrelated_class(self):
        """Regular classes shouldn't trigger product matching."""
        chunk = self._make_chunk(
            "메뉴 항목",
            class_name="navigation-menu__item",
        )
        matches = _match_product(chunk)
        assert len(matches) == 0

    def test_existing_itemprop_still_works(self):
        """itemprop=name should still match."""
        chunk = HtmlChunk(
            xpath="/html/body/div",
            html="<div>Product Name</div>",
            text="Product Name",
            tag="div",
            chunk_type=ChunkType.TEXT_BLOCK,
            attrs={"itemprop": "name"},
        )
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "name" in fields

    def test_h1_still_works(self):
        """h1 tag should still match name."""
        chunk = self._make_chunk("Product Title", tag="h1")
        matches = _match_product(chunk)
        fields = [f for f, _ in matches]
        assert "name" in fields
