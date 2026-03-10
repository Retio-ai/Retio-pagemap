# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ecommerce rendering in serializer (Phase A0, A1).

Covers: ## Ecommerce section in to_agent_prompt(), page_state JSON key,
ecommerce diff mode, empty ecommerce data.
"""

from __future__ import annotations

import json

from pagemap import Interactable, PageMap
from pagemap.diagnostics import DiagnosticResult, PageFailureState, PageStateDiagnosis
from pagemap.serializer import (
    _render_ecommerce_section,
    to_agent_prompt,
    to_agent_prompt_diff,
    to_json,
)


def _make_page_map(**overrides) -> PageMap:
    defaults = {
        "url": "https://example.com/product/123",
        "title": "Test Product",
        "page_type": "product_detail",
        "interactables": [],
        "pruned_context": "",
        "pruned_tokens": 0,
        "generation_ms": 42.0,
        "images": [],
        "metadata": {},
    }
    defaults.update(overrides)
    return PageMap(**defaults)


def _make_interactable(**overrides) -> Interactable:
    defaults = {
        "ref": 1,
        "role": "button",
        "name": "Buy Now",
        "affordance": "click",
        "region": "main",
        "tier": 1,
    }
    defaults.update(overrides)
    return Interactable(**defaults)


# ── Ecommerce Section Rendering ───────────────────────────────────


class TestEcommerceSearchRendering:
    def test_search_results_section(self):
        ecom = {
            "query": "shoes",
            "total_results": "1,234",
            "cards": [
                {"name": "Nike Air Max", "price": 129.99},
                {"name": "Adidas Ultra", "price": 149.99, "is_sponsored": True},
            ],
        }
        lines = _render_ecommerce_section(ecom, "search_results")
        text = "\n".join(lines)
        assert "## Ecommerce" in text
        assert "Query: shoes" in text
        assert "Results: 1,234" in text
        assert "Cards: 2" in text
        assert "Nike Air Max" in text
        assert "[AD]" in text

    def test_search_empty_cards(self):
        ecom = {"query": "nothing", "cards": []}
        lines = _render_ecommerce_section(ecom, "search_results")
        text = "\n".join(lines)
        assert "Cards: 0" in text

    def test_search_truncation_5_cards(self):
        ecom = {
            "cards": [{"name": f"Product {i}", "price": i * 10} for i in range(8)],
        }
        lines = _render_ecommerce_section(ecom, "search_results")
        text = "\n".join(lines)
        assert "...+3 more" in text


class TestEcommerceListingRendering:
    def test_listing_section(self):
        ecom = {
            "category": "Women's Jackets",
            "cards": [
                {"name": "Leather Jacket", "price": 189000},
            ],
        }
        lines = _render_ecommerce_section(ecom, "listing")
        text = "\n".join(lines)
        assert "Category: Women's Jackets" in text
        assert "Cards: 1" in text
        assert "Leather Jacket" in text


class TestEcommerceProductRendering:
    def test_product_detail_section(self):
        ecom = {
            "name": "오버핏 레더 자켓",
            "price": 189000,
            "currency": "KRW",
            "original_price": 259000,
            "discount_pct": 27,
            "brand": "TestBrand",
            "rating": 4.6,
            "review_count": 847,
            "availability": "in_stock",
            "options": [
                {"label": "Size", "type": "size", "values": ["S", "M", "L"], "selected": "M"},
            ],
            "cart": {
                "add_to_cart_ref": 5,
                "buy_now_ref": 6,
                "prerequisites": [],
            },
        }
        lines = _render_ecommerce_section(ecom, "product_detail")
        text = "\n".join(lines)
        assert "## Ecommerce" in text
        assert "Name: 오버핏 레더 자켓" in text
        assert "189000 KRW" in text
        assert "was 259000" in text
        assert "-27%" in text
        assert "Brand: TestBrand" in text
        assert "Rating: 4.6/5 (847 reviews)" in text
        assert "Availability: in_stock" in text
        assert "Size: S,M,L" in text
        assert "[selected: M]" in text
        assert "Add: [5]" in text
        assert "Buy: [6]" in text

    def test_product_minimal(self):
        ecom = {"name": "Simple Product", "price": 10000}
        lines = _render_ecommerce_section(ecom, "product_detail")
        text = "\n".join(lines)
        assert "Name: Simple Product" in text
        assert "10000" in text

    def test_product_with_prerequisites(self):
        ecom = {
            "name": "Product",
            "cart": {"add_to_cart_ref": 1, "prerequisites": ["Select Size"]},
        }
        lines = _render_ecommerce_section(ecom, "product_detail")
        text = "\n".join(lines)
        assert "Prereqs: Select Size" in text


class TestEcommerceEmpty:
    def test_empty_ecom_returns_empty(self):
        lines = _render_ecommerce_section({}, "product_detail")
        assert lines == []

    def test_unknown_page_type_returns_empty(self):
        lines = _render_ecommerce_section({"data": "stuff"}, "article")
        assert lines == []

    def test_none_robust(self):
        # Should not crash
        _render_ecommerce_section({"cards": None}, "search_results")
        # May or may not have content, but should not crash


# ── Ecommerce in to_agent_prompt() ────────────────────────────────


class TestEcommerceInPrompt:
    def test_ecommerce_section_rendered(self):
        ecom = {
            "name": "Test Product",
            "price": 99.99,
            "currency": "USD",
            "cart": {"add_to_cart_ref": 5},
        }
        pm = _make_page_map(
            metadata={"ecommerce": ecom},
            page_type="product_detail",
        )
        prompt = to_agent_prompt(pm)
        assert "## Ecommerce" in prompt
        assert "Name: Test Product" in prompt
        assert "99.99 USD" in prompt

    def test_no_ecommerce_section_when_empty(self):
        pm = _make_page_map(metadata={})
        prompt = to_agent_prompt(pm)
        assert "## Ecommerce" not in prompt

    def test_ecommerce_section_before_actions(self):
        ecom = {"name": "Product", "price": 100}
        items = [_make_interactable(ref=1)]
        pm = _make_page_map(
            metadata={"ecommerce": ecom},
            page_type="product_detail",
            interactables=items,
        )
        prompt = to_agent_prompt(pm)
        ecom_pos = prompt.find("## Ecommerce")
        actions_pos = prompt.find("## Actions")
        assert ecom_pos < actions_pos

    def test_ecommerce_section_after_diagnostics(self):
        ecom = {"query": "test", "cards": []}
        diag = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.EMPTY_RESULTS,
                confidence=0.8,
                signals=(),
            )
        )
        pm = _make_page_map(
            metadata={"ecommerce": ecom},
            page_type="search_results",
            diagnostics=diag,
        )
        prompt = to_agent_prompt(pm)
        diag_pos = prompt.find("## Diagnostics")
        ecom_pos = prompt.find("## Ecommerce")
        assert diag_pos < ecom_pos


# ── page_state JSON key ───────────────────────────────────────────


class TestPageStateJsonKey:
    def test_page_state_in_json(self):
        diag = DiagnosticResult(
            page_state=PageStateDiagnosis(
                state=PageFailureState.BOT_BLOCKED,
                confidence=0.95,
                signals=("pattern",),
                detail="Cloudflare challenge",
            )
        )
        pm = _make_page_map(diagnostics=diag)
        data = json.loads(to_json(pm))
        assert "page_state" in data
        assert data["page_state"]["barrier"] == "bot_blocked"
        assert data["page_state"]["confidence"] == 0.95
        assert data["page_state"]["detail"] == "Cloudflare challenge"

    def test_no_page_state_when_no_diagnostics(self):
        pm = _make_page_map()
        data = json.loads(to_json(pm))
        assert "page_state" not in data

    def test_no_page_state_when_no_failure(self):
        diag = DiagnosticResult()  # No page_state
        pm = _make_page_map(diagnostics=diag)
        data = json.loads(to_json(pm))
        assert "page_state" not in data


# ── Ecommerce Diff Mode ───────────────────────────────────────────


class TestEcommerceDiff:
    def test_ecommerce_unchanged_marker(self):
        items = [_make_interactable(ref=i) for i in range(1, 10)]
        ecom = {"name": "Product", "price": 100}
        old = _make_page_map(
            metadata={"ecommerce": ecom},
            page_type="product_detail",
            interactables=items,
            pruned_context="Long content " * 30,
        )
        new = _make_page_map(
            metadata={"ecommerce": ecom},
            page_type="product_detail",
            interactables=items,
            pruned_context="Long content updated " * 30,
        )
        diff = to_agent_prompt_diff(old, new, savings_threshold=0.0)
        assert diff is not None
        assert "## Ecommerce — unchanged" in diff

    def test_ecommerce_updated_marker(self):
        items = [_make_interactable(ref=i) for i in range(1, 10)]
        old_ecom = {"name": "Product", "price": 100}
        new_ecom = {"name": "Product", "price": 120}
        old = _make_page_map(
            metadata={"ecommerce": old_ecom},
            page_type="product_detail",
            interactables=items,
            pruned_context="Content " * 30,
        )
        new = _make_page_map(
            metadata={"ecommerce": new_ecom},
            page_type="product_detail",
            interactables=items,
            pruned_context="Content " * 30,
        )
        diff = to_agent_prompt_diff(old, new, savings_threshold=0.0)
        assert diff is not None
        assert "## Ecommerce (updated)" in diff

    def test_ecommerce_change_in_summary(self):
        items = [_make_interactable(ref=i) for i in range(1, 10)]
        old = _make_page_map(
            metadata={"ecommerce": {"price": 100}},
            page_type="product_detail",
            interactables=items,
            pruned_context="Content " * 30,
        )
        new = _make_page_map(
            metadata={"ecommerce": {"price": 120}},
            page_type="product_detail",
            interactables=items,
            pruned_context="Content " * 30,
        )
        diff = to_agent_prompt_diff(old, new, savings_threshold=0.0)
        assert diff is not None
        assert "Ecommerce: updated" in diff

    def test_no_ecommerce_no_section_in_diff(self):
        items = [_make_interactable(ref=i) for i in range(1, 10)]
        old = _make_page_map(interactables=items, pruned_context="C " * 50)
        new = _make_page_map(interactables=items, pruned_context="D " * 50)
        diff = to_agent_prompt_diff(old, new, savings_threshold=0.0)
        if diff:
            assert "## Ecommerce" not in diff
