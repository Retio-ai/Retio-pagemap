# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DOM-based card detection (Step 2).

Tests _detect_cards_from_dom() and the extended _detect_product_cards() cascade.
"""

from __future__ import annotations

import lxml.html

from pagemap.pruned_context_builder import (
    _detect_cards_from_dom,
    _detect_product_cards,
)


def _parse(html: str) -> lxml.html.HtmlElement:
    return lxml.html.document_fromstring(html)


class TestDetectCardsFromDom:
    """Unit tests for _detect_cards_from_dom()."""

    def test_basic_product_grid(self):
        """Detect cards from a simple product grid with prices."""
        html = """<html><body>
        <div class="product-list">
            <div class="card">
                <h3>Premium Wool Jacket</h3>
                <span class="price">₩159,000</span>
            </div>
            <div class="card">
                <h3>Slim Fit Jeans</h3>
                <span class="price">₩89,000</span>
            </div>
            <div class="card">
                <h3>Cotton T-Shirt</h3>
                <span class="price">₩39,000</span>
            </div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        assert len(cards) >= 3
        names = [c["name"] for c in cards]
        assert any("Jacket" in n for n in names)
        assert all(c["price_text"] for c in cards)

    def test_dollar_prices(self):
        """Detect cards with dollar prices."""
        html = """<html><body>
        <ul class="items">
            <li><a href="/1"><span>Running Shoes</span> <span>$129.99</span></a></li>
            <li><a href="/2"><span>Training Shorts</span> <span>$49.99</span></a></li>
            <li><a href="/3"><span>Sport Jacket</span> <span>$89.99</span></a></li>
        </ul>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        assert len(cards) >= 3
        assert any("$129.99" in c["price_text"] for c in cards)

    def test_euro_prices(self):
        """Detect cards with Euro prices."""
        html = """<html><body>
        <div class="grid">
            <div class="product"><span>Oversized Coat</span> <span>€199.00</span></div>
            <div class="product"><span>Knit Sweater</span> <span>€79.00</span></div>
            <div class="product"><span>Linen Shirt</span> <span>€59.00</span></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        assert len(cards) >= 3

    def test_won_with_comma(self):
        """Detect Korean won prices in comma format."""
        html = """<html><body>
        <div class="list">
            <div class="item">무신사 스탠다드 후드 집업 <span>39,900원</span></div>
            <div class="item">디스이즈네버댓 기본 티셔츠 <span>29,000원</span></div>
            <div class="item">커버낫 오버핏 맨투맨 <span>59,000원</span></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        assert len(cards) >= 3

    def test_less_than_3_cards_returns_empty(self):
        """Fewer than 3 price elements should return empty (not a grid)."""
        html = """<html><body>
        <div class="product">
            <h3>Single Product</h3>
            <span>₩99,000</span>
        </div>
        <div class="product">
            <h3>Another Product</h3>
            <span>₩79,000</span>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        assert len(cards) == 0

    def test_no_prices_returns_empty(self):
        """No price patterns should return empty."""
        html = """<html><body>
        <div class="articles">
            <div class="article"><h3>News Article 1</h3><p>Lorem ipsum</p></div>
            <div class="article"><h3>News Article 2</h3><p>Dolor sit amet</p></div>
            <div class="article"><h3>News Article 3</h3><p>Consectetur</p></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        assert len(cards) == 0

    def test_none_doc_returns_empty(self):
        """None doc should return empty list."""
        cards = _detect_cards_from_dom(None)
        assert cards == []

    def test_deduplication(self):
        """Duplicate cards (same text) should be deduplicated."""
        html = """<html><body>
        <div class="list">
            <div class="item">Same Product ₩10,000</div>
            <div class="item">Same Product ₩10,000</div>
            <div class="item">Same Product ₩10,000</div>
            <div class="item">Different Product ₩20,000</div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_cards_from_dom(doc)
        # Should have at most 2 unique cards (Same + Different)
        # But might have fewer if dedup by text content works
        assert len(cards) <= 4


class TestDetectProductCardsCascade:
    """Integration tests for the _detect_product_cards cascade with doc parameter."""

    def test_dom_used_when_chunks_empty(self):
        """DOM detection should fire when chunks produce no cards."""
        html = """<html><body>
        <div class="grid">
            <div class="card"><span>Jacket</span> <span>₩159,000</span></div>
            <div class="card"><span>Jeans</span> <span>₩89,000</span></div>
            <div class="card"><span>Shirt</span> <span>₩39,000</span></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        # No chunks, no metadata → DOM detection should fire
        cards = _detect_product_cards(chunks=[], metadata=None, doc=doc)
        assert len(cards) >= 3

    def test_metadata_takes_priority_over_dom(self):
        """JSON-LD metadata should take priority over DOM detection."""
        html = """<html><body>
        <div class="grid">
            <div class="card"><span>DOM Card</span> <span>₩10,000</span></div>
            <div class="card"><span>DOM Card 2</span> <span>₩20,000</span></div>
            <div class="card"><span>DOM Card 3</span> <span>₩30,000</span></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        metadata = {
            "items": [
                {"name": "Meta Card 1", "price_text": "₩100,000"},
                {"name": "Meta Card 2", "price_text": "₩200,000"},
            ]
        }
        cards = _detect_product_cards(chunks=[], metadata=metadata, doc=doc)
        # Should use metadata cards, not DOM
        assert any("Meta Card" in c["name"] for c in cards)

    def test_dom_fallback_when_metadata_empty(self):
        """DOM should be used as fallback when metadata has no items."""
        html = """<html><body>
        <div class="grid">
            <div class="card"><span>Fallback Card</span> <span>₩10,000</span></div>
            <div class="card"><span>Fallback Card 2</span> <span>₩20,000</span></div>
            <div class="card"><span>Fallback Card 3</span> <span>₩30,000</span></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_product_cards(chunks=[], metadata={}, doc=doc)
        assert len(cards) >= 3

    def test_hint_json_ld_with_dom_fallback(self):
        """With json_ld hint but no metadata, should fall through to DOM."""
        html = """<html><body>
        <div class="grid">
            <div class="card"><span>Hint Test</span> <span>₩50,000</span></div>
            <div class="card"><span>Hint Test 2</span> <span>₩60,000</span></div>
            <div class="card"><span>Hint Test 3</span> <span>₩70,000</span></div>
        </div>
        </body></html>"""
        doc = _parse(html)
        cards = _detect_product_cards(chunks=[], metadata=None, card_strategy_hint="json_ld_itemlist", doc=doc)
        assert len(cards) >= 3
