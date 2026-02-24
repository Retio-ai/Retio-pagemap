# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-module pipeline integration tests.

v0.6.0 revealed failures when modules passed individually but broke at seams:
- AOM filter removed product grids → pruned_context_builder got empty content
- Locale settings didn't propagate → mixed languages in output
- Classifier misclassification → wrong compressor → fields dropped

These tests exercise cross-module chains with synthetic HTML.
"""

from __future__ import annotations

from pagemap import Interactable, PageMap
from pagemap.page_map_builder import _budget_filter_interactables, build_page_map_offline
from pagemap.pruned_context_builder import PRICE_PATTERN, build_pruned_context
from pagemap.pruning.aom_filter import aom_filter
from pagemap.script_filter import Script, filter_lines
from pagemap.serializer import to_agent_prompt
from tests._pruning_helpers import html, parse_doc


def _make_el(
    ref: int,
    role: str,
    name: str,
    region: str = "main",
    tier: int = 1,
    affordance: str = "click",
) -> Interactable:
    """Build an Interactable for testing."""
    return Interactable(
        ref=ref,
        role=role,
        name=name,
        region=region,
        tier=tier,
        affordance=affordance,
    )


# ---------------------------------------------------------------------------
# Chain A: AOM → Pruning → Context Builder
# ---------------------------------------------------------------------------


class TestChainA_AomPruningContext:
    """AOM filter → grid whitelist → pruning → MCG → compression → context."""

    def test_product_grid_high_link_density_survives(self):
        """v0.6.0: Grid-whitelisted product cards with >70% link density removed by AOM → empty context."""
        cards = []
        for i in range(5):
            cards.append(
                f'<a href="/product/{i}" class="card">'
                f'<img src="/img/{i}.jpg">'
                f'<span class="name">프리미엄 가죽 자켓 모델 {i}</span>'
                f'<span class="price">₩{15000 + i * 1000:,}</span>'
                f"</a>"
            )
        body = f'<div class="product-list">{"".join(cards)}</div>'
        fixture = html(body)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/products")

        assert pm.pruned_context, "pruned_context must not be empty"
        assert PRICE_PATTERN.search(pm.pruned_context), "at least one price must survive"
        assert pm.pruned_tokens > 0

    def test_mcg_fires_when_aom_removes_all_content(self):
        """v0.6.0: AOM removed everything → 0 tokens instead of MCG fallback."""
        # Build nav with 30 links to ensure len(raw_html) > 500
        links = "".join(f'<a href="/page/{i}">메뉴 항목 {i}</a>' for i in range(30))
        body = f"<nav>{links}</nav>"
        head = (
            '<meta property="og:title" content="테스트 사이트 타이틀">'
            '<meta property="og:description" content="사이트 설명입니다">'
        )
        fixture = html(body, head=head)

        pm = build_page_map_offline(fixture, url="https://example.com/navonly")

        assert pm.pruned_context, "pruned_context must not be empty (MCG should fire)"
        assert any("minimum content guarantee" in w for w in pm.warnings), f"MCG warning expected, got: {pm.warnings}"
        assert pm.pruned_tokens > 0

    def test_grid_whitelisted_prices_reach_context(self):
        """v0.6.0: Grid survived AOM but lost during chunk decomposition."""
        items = []
        for i in range(3):
            items.append(
                f'<div class="item"><a href="/p/{i}">상품 이름 {i} - 고급 소재</a> ₩{25000 + i * 5000:,}</div>'
            )
        body = f'<main><h1>인기 상품 목록</h1><div class="product-grid">{"".join(items)}</div></main>'
        fixture = html(body)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/popular")

        assert pm.pruned_context, "pruned_context must not be empty"
        # Either the heading or at least one price must be present
        has_heading = "인기 상품" in pm.pruned_context
        has_price = PRICE_PATTERN.search(pm.pruned_context) is not None
        assert has_heading or has_price, f"Expected heading or price in context, got: {pm.pruned_context[:200]}"

    def test_nav_removed_no_false_positive(self):
        """v0.6.0: Grid whitelist accidentally preserved navigation links."""
        body = (
            "<nav>"
            '<a href="/about">회사소개</a>'
            '<a href="/service">서비스</a>'
            '<a href="/contact">문의하기</a>'
            "</nav>"
            "<main><article>"
            "<h1>오버핏 레더 자켓</h1>"
            "<p>가격: ₩259,000</p>"
            "<p>프리미엄 이탈리안 가죽으로 제작된 오버핏 레더 자켓입니다.</p>"
            "</article></main>"
            "<footer>© 2024 사업자등록 123-45-67890</footer>"
        )
        fixture = html(body)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/product/1")

        # Product info must be present
        has_product = "259,000" in pm.pruned_context or "레더 자켓" in pm.pruned_context
        assert has_product, f"Product info missing from context: {pm.pruned_context[:200]}"
        # Nav and footer must be absent
        assert "회사소개" not in pm.pruned_context, "Nav text should be removed"
        assert "사업자등록" not in pm.pruned_context, "Footer text should be removed"

    def test_content_rescue_at_aom_level(self):
        """v0.6.0: Content rescue failed to restore price-bearing elements."""
        # No <main> — only link-heavy cards with prices
        cards = []
        for i in range(3):
            cards.append(
                f'<div class="card"><a href="/p/{i}">상품 {i} 프리미엄 제품 설명</a> ₩{30000 + i * 10000:,}</div>'
            )
        # One card without price
        cards.append('<div class="card"><a href="/more">More info about our collection</a></div>')
        body = f'<div class="items">{"".join(cards)}</div>'
        fixture = html(body)

        doc, tree = parse_doc(fixture)
        stats = aom_filter(doc, schema_name="Product")

        assert stats.content_rescue_count > 0, "Price cards should be rescued"
        remaining = doc.text_content() or ""
        assert PRICE_PATTERN.search(remaining), "Price text should be present after rescue"

    def test_content_rescue_flows_to_pruned_context(self):
        """v0.6.0: Rescued content lost between AOM and final output."""
        cards = []
        for i in range(3):
            cards.append(
                f'<div class="card"><a href="/p/{i}">상품 {i} 프리미엄 제품 설명</a> ₩{30000 + i * 10000:,}</div>'
            )
        cards.append('<div class="card"><a href="/more">More info about our collection</a></div>')
        body = f'<div class="items">{"".join(cards)}</div>'
        fixture = html(body)

        pm = build_page_map_offline(fixture, schema_name="Product")

        assert pm.pruned_context, "pruned_context must not be empty"
        assert pm.pruned_tokens > 0

    def test_aom_removes_header_footer_keeps_main(self):
        """v0.6.0: AOM header/footer removal leaked into main content."""
        body = (
            "<header>"
            '<div class="logo">사이트 로고</div>'
            "<nav>"
            '<a href="/">홈</a><a href="/shop">쇼핑</a><a href="/cart">장바구니</a>'
            "</nav>"
            "</header>"
            "<main><article>"
            "<h1>프리미엄 운동화</h1>"
            "<p>가격: ₩189,000</p>"
            "<p>평점: 4.8 / 5.0 (1,234개 리뷰)</p>"
            "<p>최고급 소재로 제작된 프리미엄 운동화입니다. 편안한 착용감과 세련된 디자인.</p>"
            "</article></main>"
            "<footer>© 2024 사업자등록 987-65-43210</footer>"
        )
        fixture = html(body)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/shoes/1")

        # Product content must survive
        has_product = "운동화" in pm.pruned_context or "189,000" in pm.pruned_context
        assert has_product, f"Product content missing: {pm.pruned_context[:200]}"
        # Header/footer must be removed
        assert "사이트 로고" not in pm.pruned_context, "Header text should be removed"
        assert "사업자등록" not in pm.pruned_context, "Footer text should be removed"


# ---------------------------------------------------------------------------
# Chain B: Locale → Script Filter → Output
# ---------------------------------------------------------------------------


class TestChainB_LocaleScriptOutput:
    """Locale → script filter → lang filter → output."""

    def test_mixed_language_foreign_noise_filtered_e2e(self):
        """v0.6.0: Short foreign UI noise passed through to pruned_context."""
        body = (
            "<main>"
            "<h1>프리미엄 가죽 자켓</h1>"
            "<p>이탈리안 소가죽으로 제작된 고급 오버핏 자켓입니다. 세련된 디자인과 뛰어난 내구성을 자랑합니다.</p>"
            "<p>₩259,000</p>"
            "<p>Oferta especial de temporada</p>"
            "<p>사이즈: S, M, L, XL, XXL</p>"
            "</main>"
        )
        fixture = html(body)

        context, tokens, meta = build_pruned_context(
            fixture,
            page_type="product_detail",
            schema_name="Product",
            enable_lang_filter=True,
        )

        # Korean content preserved
        has_korean = "259,000" in context or "가죽 자켓" in context
        assert has_korean, f"Korean content missing: {context[:200]}"
        # Short Spanish noise removed
        assert "Oferta especial" not in context, "Short foreign noise should be filtered"

    def test_lang_filter_active_by_default_in_full_pipeline(self):
        """v0.6.0: enable_lang_filter not propagated through build_page_map_offline."""
        body = (
            "<main>"
            "<h1>한국어 상품 페이지</h1>"
            "<p>이 상품은 최고급 원단으로 제작되었습니다. 편안하고 세련된 디자인.</p>"
            "<p>₩89,000</p>"
            "<p>Temporary seasonal clearance</p>"
            "</main>"
        )
        fixture = html(body)

        pm = build_page_map_offline(fixture, url="https://shop.kr/product")

        # Korean content preserved
        has_korean = "89,000" in pm.pruned_context or "한국어" in pm.pruned_context
        assert has_korean, f"Korean content missing: {pm.pruned_context[:200]}"
        # Short Latin noise filtered (proves lang filter is active by default)
        assert "Temporary seasonal clearance" not in pm.pruned_context, (
            "Short Latin noise should be filtered by default"
        )

    def test_urls_preserved_despite_foreign_script(self):
        """v0.6.0: URLs with Latin chars incorrectly filtered from Korean pages."""
        lines = [
            "자세한 정보는 https://example.com/product/123 에서 확인하세요",
            "공식 사이트: https://brand.co.kr/about 방문해주세요",
        ]

        result = filter_lines(lines, Script.HANGUL)

        assert result.removed_count == 0, "URL-containing lines should not be removed"
        assert len(result.lines) == 2
        assert "https://example.com" in result.lines[0]
        assert "https://brand.co.kr" in result.lines[1]

    def test_short_strings_and_numbers_passthrough(self):
        """v0.6.0: Brand names, sizes, numeric values stripped by script filter."""
        lines = ["XL", "2.5kg", "Nike", "₩15,000", "100%", "프리미엄 소재"]

        result = filter_lines(lines, Script.HANGUL)

        assert result.removed_count == 0, f"All items should pass through, removed: {result.removed_count}"
        for original in lines:
            # Check original is preserved (possibly with [lang] tag prefix)
            assert any(original in line for line in result.lines), f"'{original}' missing from output: {result.lines}"


# ---------------------------------------------------------------------------
# Chain C: Classifier → Compressor → Builder
# ---------------------------------------------------------------------------


class TestChainC_ClassifierCompressorBuilder:
    """URL+HTML signals → classifier → compressor dispatch → builder."""

    def test_product_detail_extracts_price_name(self):
        """v0.6.0: Wrong compressor triggered → price/name lost."""
        body = (
            "<main>"
            "<h1>오버핏 레더 자켓</h1>"
            "<p>가격: ₩259,000</p>"
            '<p>평점: <span class="rating">4.6</span> (847개 리뷰)</p>'
            "<p>프리미엄 이탈리안 소가죽으로 제작된 고급 오버핏 레더 자켓입니다.</p>"
            '<button type="button">장바구니 담기</button>'
            "</main>"
        )
        head = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Product",'
            '"name":"오버핏 레더 자켓","offers":{"price":"259000","priceCurrency":"KRW"}}'
            "</script>"
        )
        fixture = html(body, head=head)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/products/jacket-1")

        assert pm.page_type == "product_detail", f"Expected product_detail, got {pm.page_type}"
        assert PRICE_PATTERN.search(pm.pruned_context), f"Price missing from context: {pm.pruned_context[:200]}"
        assert "레더 자켓" in pm.pruned_context, f"Product name missing from context: {pm.pruned_context[:200]}"

    def test_article_extracts_body_text(self):
        """v0.6.0: Article compressor returned only headers."""
        body = (
            "<article>"
            "<h1>인공지능 기술 발전 동향</h1>"
            '<time datetime="2024-03-15">2024년 3월 15일</time>'
            "<p>저자: 김철수</p>"
            "<p>최근 인공지능 기술은 놀라운 속도로 발전하고 있습니다. "
            "특히 대규모 언어 모델의 등장으로 다양한 분야에서 혁신이 일어나고 있습니다.</p>"
            "<p>전문가들은 향후 5년 내에 인공지능이 일상생활의 모든 영역에 "
            "깊숙이 침투할 것으로 전망하고 있습니다.</p>"
            "</article>"
        )
        head = '<meta property="og:type" content="article">'
        fixture = html(body, head=head)

        pm = build_page_map_offline(fixture, url="https://news.example.com/article/ai-trends")

        assert pm.page_type == "article", f"Expected article, got {pm.page_type}"
        assert pm.pruned_tokens > 0
        # Body text keyword must be present (not just headers)
        has_body = "언어 모델" in pm.pruned_context or "인공지능" in pm.pruned_context
        assert has_body, f"Article body missing: {pm.pruned_context[:200]}"

    def test_cart_product_not_classified_dashboard(self):
        """v0.6.0: Tables + product info → misclassified as dashboard."""
        body = (
            "<main>"
            "<h1>프리미엄 노트북</h1>"
            "<p>₩1,890,000</p>"
            "<table><thead><tr><th>사양</th><th>값</th></tr></thead>"
            "<tbody>"
            "<tr><td>CPU</td><td>Intel i9</td></tr>"
            "<tr><td>RAM</td><td>32GB</td></tr>"
            "<tr><td>저장장치</td><td>1TB SSD</td></tr>"
            "</tbody></table>"
            '<button type="button">장바구니 담기</button>'
            '<button type="button">바로 구매</button>'
            "</main>"
        )
        head = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Product",'
            '"name":"프리미엄 노트북","offers":{"price":"1890000","priceCurrency":"KRW"}}'
            "</script>"
        )
        fixture = html(body, head=head)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/product/notebook-1")

        assert pm.page_type == "product_detail", f"Expected product_detail, got {pm.page_type}"
        assert PRICE_PATTERN.search(pm.pruned_context), f"Price missing from context: {pm.pruned_context[:200]}"

    def test_news_classification_preserves_content(self):
        """v0.6.0: News classifier-compressor handoff lost body."""
        body = (
            "<article>"
            "<h1>글로벌 경제 전망 보고서</h1>"
            "<p>세계 경제는 다양한 도전에 직면해 있습니다. "
            "인플레이션, 공급망 혼란, 지정학적 긴장 등이 주요 리스크로 지목되고 있습니다.</p>"
            "<p>전문가들은 신중한 통화정책과 재정정책의 균형이 필요하다고 강조합니다.</p>"
            "</article>"
        )
        head = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"NewsArticle",'
            '"headline":"글로벌 경제 전망 보고서"}'
            "</script>"
        )
        fixture = html(body, head=head)

        pm = build_page_map_offline(fixture, url="https://news.example.com/economy/report")

        assert pm.page_type == "news", f"Expected news, got {pm.page_type}"
        has_body = "인플레이션" in pm.pruned_context or "경제" in pm.pruned_context
        assert has_body, f"News body missing: {pm.pruned_context[:200]}"


# ---------------------------------------------------------------------------
# Chain D: Interactable → Noise Filter → Serializer
# ---------------------------------------------------------------------------


class TestChainD_InteractableNoiseSerializer:
    """Budget filter → noise prioritization → serializer."""

    def test_noise_deprioritized_meaningful_preserved(self):
        """v0.6.0: Budget filter dropped meaningful buttons alongside table noise."""
        # 4 meaningful elements
        meaningful = [
            _make_el(1, "searchbox", "상품 검색", region="header", affordance="type"),
            _make_el(2, "button", "장바구니 담기", region="main", affordance="click"),
            _make_el(3, "combobox", "사이즈 선택", region="main", affordance="select"),
            _make_el(4, "link", "상세 정보 보기", region="main", affordance="click"),
        ]
        # 20 unnamed table noise items
        noise = [_make_el(5 + i, "row", "", region="main", tier=2) for i in range(20)]
        elements = meaningful + noise

        filtered = _budget_filter_interactables(elements, pruned_tokens=4900, total_budget=5000)

        # Build a minimal PageMap and serialize
        pm = PageMap(
            url="https://shop.example.com/product/1",
            title="테스트",
            page_type="product_detail",
            interactables=filtered,
            pruned_context="₩259,000",
            pruned_tokens=10,
            generation_ms=0.0,
        )
        prompt = to_agent_prompt(pm)

        # All 4 meaningful elements must survive
        assert "상품 검색" in prompt, "searchbox should survive"
        assert "장바구니 담기" in prompt, "button should survive"
        assert "사이즈 선택" in prompt, "combobox should survive"
        assert "상세 정보 보기" in prompt, "link should survive"
        # Noise must be cut
        assert len(filtered) < len(elements), f"Expected noise reduction: {len(filtered)} should be < {len(elements)}"

    def test_full_pipeline_serializes_correctly(self):
        """v0.6.0: Valid PageMap but serializer missed interactables or pruned_context."""
        body = (
            "<main>"
            "<h1>프리미엄 가죽 자켓</h1>"
            "<p>₩259,000</p>"
            "<p>최고급 이탈리안 소가죽으로 제작된 프리미엄 오버핏 자켓입니다.</p>"
            '<button type="button">장바구니 담기</button>'
            '<input type="search" aria-label="상품 검색" placeholder="검색">'
            '<select aria-label="사이즈 선택">'
            "<option>S</option><option>M</option><option>L</option>"
            "</select>"
            "</main>"
        )
        fixture = html(body)

        pm = build_page_map_offline(fixture, url="https://shop.example.com/product/jacket")
        prompt = to_agent_prompt(pm)

        assert "## Actions" in prompt, "Actions section missing"
        assert "## Info" in prompt, "Info section missing"
        assert "장바구니 담기" in prompt, "Button not serialized"
        assert "URL:" in prompt, "URL header missing"
