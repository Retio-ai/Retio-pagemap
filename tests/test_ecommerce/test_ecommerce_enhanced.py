# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ecommerce engine enhancements — Phases B, C.

Covers: Usercentrics CMP, age gate extended, 2FA/login_type,
pagination refs, sponsored detection, gallery, review snippets,
cart flow_state/confirmation, and Hypothesis fuzz tests.
"""

from __future__ import annotations

import pytest

from pagemap.ecommerce import (
    BarrierResult,
    BarrierType,
    CartAction,
    ListingResult,
    ProductCard,
    ProductResult,
    SearchResult,
)
from pagemap.ecommerce._card_extractor import PaginationRefs, find_pagination_refs
from pagemap.ecommerce.barrier_handler import detect_barriers
from pagemap.ecommerce.cart_engine import (
    _detect_confirmation,
    _detect_flow_state,
    _extract_cart_count,
    analyze_cart_actions,
)
from pagemap.ecommerce.cookie_patterns import detect_cookie_provider
from pagemap.ecommerce.listing_engine import analyze_listing
from pagemap.ecommerce.login_detector import (
    AgeGateInfo,
    detect_age_gate,
    detect_age_gate_extended,
    detect_login_wall,
)
from pagemap.ecommerce.product_engine import (
    _build_selected_variant,
    _extract_gallery_images,
    _extract_review_snippets,
    analyze_product,
)
from pagemap.ecommerce.search_engine import (
    _detect_sponsored,
    _normalize_result_count,
    analyze_search_results,
)

from .conftest import PRODUCT_JSONLD

# ── B1: Usercentrics CMP ──────────────────────────────────────────


class TestUsercentriesCMP:
    def test_usercentrics_detected(self):
        html = '<div class="uc-banner">consent</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "usercentrics"
        assert result.confidence >= 0.85

    def test_uc_consent_detected(self):
        html = '<div id="uc-consent">privacy</div>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "usercentrics"

    def test_usercentrics_in_script(self):
        html = '<script src="https://cdn.usercentrics.com/sdk.js"></script>'
        result = detect_cookie_provider(html.lower())
        assert result is not None
        assert result.provider == "usercentrics"


class TestMultiLocaleAcceptTerms:
    @pytest.mark.parametrize(
        "button_text",
        [
            "Accept All",
            "모두 수락",
            "すべて受け入れる",
            "Tout accepter",
            "Alle akzeptieren",
            "全部接受",
            "Aceptar todo",
        ],
        ids=["en", "ko", "ja", "fr", "de", "zh", "es"],
    )
    def test_accept_terms_match(self, button_text, make_interactable):
        html = '<div id="CybotCookiebotDialog">cookie consent</div>'
        interactables = [
            make_interactable(ref=1, role="button", name=button_text, affordance="click"),
        ]
        result = detect_barriers(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert result.accept_ref == 1


# ── B2: Age Gate Extended ──────────────────────────────────────────


class TestAgeGateExtended:
    def test_click_through_detected(self):
        html = '<div class="age-verification">Are you over 18?</div>'
        info = detect_age_gate_extended(html.lower())
        assert info is not None
        assert info.gate_type == "click_through"
        assert info.confidence >= 0.7

    def test_date_entry_detected(self):
        html = '<div class="age-verification">Enter your birthdate <select name="year">...</select></div>'
        info = detect_age_gate_extended(html.lower())
        assert info is not None
        assert info.gate_type == "date_entry"
        assert info.has_date_picker is True

    def test_backward_compat_detect_age_gate(self):
        """detect_age_gate() still returns tuple[float, tuple[str, ...]]."""
        html = '<div class="age-verification">Are you over 18?</div>'
        confidence, signals = detect_age_gate(html.lower())
        assert confidence >= 0.7
        assert len(signals) > 0

    def test_click_through_with_accept_ref(self, make_interactable):
        html = '<div class="age-gate">Are you over 18?</div>'
        interactables = [
            make_interactable(ref=1, role="button", name="I am over 18", affordance="click"),
        ]
        result = detect_barriers(html, html.lower(), "https://example.com", interactables, "product_detail")
        assert result is not None
        assert result.barrier_type == BarrierType.AGE_VERIFICATION
        assert result.accept_ref == 1
        assert result.auto_dismissible is True
        assert result.gate_type == "click_through"

    def test_date_entry_no_auto_dismiss(self, make_interactable):
        html = '<div class="age-verification">Birth year: <select name="year">...</select></div>'
        result = detect_barriers(html, html.lower(), "https://example.com", [], "product_detail")
        assert result is not None
        assert result.barrier_type == BarrierType.AGE_VERIFICATION
        assert result.auto_dismissible is False
        assert result.gate_type == "date_entry"

    @pytest.mark.parametrize(
        "html_snippet,expected_type",
        [
            pytest.param('<div class="age-check">나이 확인</div>', "click_through", id="korean"),
            pytest.param('<div class="age-verify">年齢確認</div>', "click_through", id="japanese"),
            pytest.param('<div class="age-gate">Are you over 18?</div>', "click_through", id="english"),
            pytest.param(
                '<div class="age-check">생년월일 <input type="date" name="birth"></div>',
                "date_entry",
                id="korean_date",
            ),
        ],
    )
    def test_i18n_age_gate(self, html_snippet, expected_type):
        info = detect_age_gate_extended(html_snippet.lower())
        assert info is not None
        assert info.gate_type == expected_type


# ── B3: 2FA / Login Type ──────────────────────────────────────────


class TestLogin2FA:
    def test_2fa_totp_detected(self, make_interactable):
        html = """
        <form action="/login" class="login-form">
            <input type="email" name="email" placeholder="이메일">
            <input type="password" name="password">
            <input type="number" maxlength="6" placeholder="verification code">
            <button>Login</button>
        </form>
        """
        result = detect_login_wall(html, html.lower(), "https://example.com", [], "unknown")
        assert result is not None
        assert result.has_2fa is True

    def test_2fa_text_detected(self, make_interactable):
        html = """
        <form action="/login" class="login-form">
            <input type="password" name="pw">
            <div>Two-factor authentication required</div>
        </form>
        """
        result = detect_login_wall(html, html.lower(), "https://example.com", [], "unknown")
        assert result is not None
        assert result.has_2fa is True

    def test_no_2fa(self, make_interactable):
        html = """
        <form action="/login" class="login-form">
            <input type="email" name="email">
            <input type="password" name="password">
        </form>
        """
        result = detect_login_wall(html, html.lower(), "https://example.com", [], "unknown")
        assert result is not None
        assert result.has_2fa is False


class TestLoginType:
    def test_social_only(self, make_interactable):
        html = """
        <div class="login-modal" id="login-dialog">
            <form action="/login" class="login-form">
                <a href="https://accounts.google.com/o/oauth2/auth">Google</a>
                <a href="https://kauth.kakao.com/oauth/authorize">Kakao</a>
            </form>
        </div>
        """
        interactables = [make_interactable(ref=1, role="button", name="Sign in")]
        result = detect_login_wall(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert result.login_type == "social_only"

    def test_mixed_login(self, make_interactable):
        html = """
        <div class="login-modal" id="login-dialog">
            <form action="/login" class="login-form">
                <input type="password" name="pw">
                <a href="https://accounts.google.com/o/oauth2/auth">Google</a>
            </form>
        </div>
        """
        interactables = [make_interactable(ref=1, role="button", name="로그인")]
        result = detect_login_wall(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert result.login_type == "mixed"

    def test_password_only(self, make_interactable):
        html = """
        <div class="login-modal" id="login-dialog">
            <form action="/login" class="login-form">
                <input type="email" name="email">
                <input type="password" name="pw">
            </form>
        </div>
        """
        interactables = [make_interactable(ref=1, role="button", name="Log in")]
        result = detect_login_wall(html, html.lower(), "https://example.com", interactables, "unknown")
        assert result is not None
        assert result.login_type == "password"


# ── C0: Pagination Refs ───────────────────────────────────────────


class TestPaginationRefs:
    def test_next_prev_detected(self, make_interactable):
        items = [
            make_interactable(ref=1, role="button", name="Previous page", affordance="click"),
            make_interactable(ref=2, role="button", name="Next page", affordance="click"),
        ]
        pag = find_pagination_refs(items)
        assert pag.next_ref == 2
        assert pag.prev_ref == 1

    def test_load_more_detected(self, make_interactable):
        items = [
            make_interactable(ref=5, role="button", name="Load more", affordance="click"),
        ]
        pag = find_pagination_refs(items)
        assert pag.load_more_ref == 5

    def test_korean_pagination(self, make_interactable):
        items = [
            make_interactable(ref=1, role="button", name="이전", affordance="click"),
            make_interactable(ref=2, role="button", name="다음", affordance="click"),
        ]
        pag = find_pagination_refs(items)
        assert pag.prev_ref == 1
        assert pag.next_ref == 2

    def test_japanese_pagination(self, make_interactable):
        items = [
            make_interactable(ref=1, role="button", name="前へ", affordance="click"),
            make_interactable(ref=2, role="button", name="次へ", affordance="click"),
        ]
        pag = find_pagination_refs(items)
        assert pag.prev_ref == 1
        assert pag.next_ref == 2

    def test_empty_interactables(self):
        pag = find_pagination_refs([])
        assert pag == PaginationRefs()

    def test_ignores_non_click(self, make_interactable):
        items = [
            make_interactable(ref=1, role="textbox", name="Next page", affordance="type"),
        ]
        pag = find_pagination_refs(items)
        assert pag.next_ref is None


# ── C1: Search Engine Enhanced ─────────────────────────────────────


class TestResultCountNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            pytest.param("1,234 results", 1234, id="en_comma"),
            pytest.param("약 1,234개", 1234, id="ko"),
            pytest.param("1 234 résultats", 1234, id="fr_space"),
            pytest.param("500건", 500, id="ko_count"),
            pytest.param("42 results", 42, id="en_simple"),
            pytest.param("", None, id="empty"),
            pytest.param("no results", None, id="no_number"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_result_count(raw) == expected


class TestSponsoredDetection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            pytest.param("Product A Sponsored", True, id="en"),
            pytest.param("상품 광고", True, id="ko"),
            pytest.param("商品 スポンサー", True, id="ja"),
            pytest.param("Normal Product", False, id="none"),
        ],
    )
    def test_detect_sponsored(self, text, expected):
        assert _detect_sponsored(text) == expected


class TestSearchEngineEnhanced:
    def test_pagination_refs_in_result(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="combobox", name="Sort by", affordance="select", options=["Price"]),
            make_interactable(ref=2, role="button", name="Next page", affordance="click"),
            make_interactable(ref=3, role="button", name="Load more", affordance="click"),
        ]
        result = analyze_search_results(
            raw_html="<html><body></body></html>",
            html_lower="<html><body></body></html>",
            interactables=interactables,
            metadata={},
            page_url="https://shop.com/search?q=shoes",
            navigation_hints={},
        )
        assert result.next_ref == 2
        assert result.load_more_ref == 3


# ── C2: Listing Engine Enhanced ────────────────────────────────────


class TestListingEnhanced:
    def test_pagination_refs_in_listing(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="button", name="Next", affordance="click"),
            make_interactable(ref=2, role="button", name="Previous", affordance="click"),
        ]
        result = analyze_listing(
            raw_html="<html><body></body></html>",
            html_lower="<html><body></body></html>",
            interactables=interactables,
            metadata={},
            page_url="https://shop.com/category/shoes",
            navigation_hints={},
        )
        assert result.next_ref == 1
        assert result.prev_ref == 2


# ── C3: Product Engine Enhanced ────────────────────────────────────


class TestGalleryImages:
    def test_jsonld_image_list(self):
        jsonld = {"image": ["https://img.com/1.jpg", "https://img.com/2.jpg"]}
        result = _extract_gallery_images(jsonld, "")
        assert result == ("https://img.com/1.jpg", "https://img.com/2.jpg")

    def test_jsonld_image_string(self):
        jsonld = {"image": "https://img.com/single.jpg"}
        result = _extract_gallery_images(jsonld, "")
        assert result == ("https://img.com/single.jpg",)

    def test_jsonld_image_none(self):
        jsonld = {"name": "Product"}
        result = _extract_gallery_images(jsonld, "")
        assert result == ()

    def test_dom_gallery(self):
        html = """
        <div class="gallery">
            <img src="https://img.com/a.jpg">
            <img src="https://img.com/b.jpg">
        </div>
        """
        result = _extract_gallery_images(None, html)
        assert "https://img.com/a.jpg" in result
        assert "https://img.com/b.jpg" in result

    def test_dedup_images(self):
        jsonld = {"image": ["https://img.com/1.jpg", "https://img.com/1.jpg"]}
        result = _extract_gallery_images(jsonld, "")
        assert len(result) == 1


class TestSelectedVariant:
    def test_selected_options(self):
        from pagemap.ecommerce import OptionGroup

        options = (
            OptionGroup(label="Size", type="size", values=("S", "M", "L"), selected="M"),
            OptionGroup(label="Color", type="color", values=("Red", "Blue"), selected="Blue"),
        )
        result = _build_selected_variant(options)
        assert result == {"Size": "M", "Color": "Blue"}

    def test_no_selections(self):
        from pagemap.ecommerce import OptionGroup

        options = (OptionGroup(label="Size", type="size", values=("S", "M", "L")),)
        result = _build_selected_variant(options)
        assert result is None

    def test_partial_selection(self):
        from pagemap.ecommerce import OptionGroup

        options = (
            OptionGroup(label="Size", type="size", values=("S", "M", "L"), selected="M"),
            OptionGroup(label="Color", type="color", values=("Red", "Blue")),
        )
        result = _build_selected_variant(options)
        assert result == {"Size": "M"}


class TestReviewSnippets:
    def test_extract_reviews(self):
        jsonld = {
            "review": [
                {"reviewBody": "Great product, highly recommended!"},
                {"reviewBody": "Very comfortable to wear."},
                {"reviewBody": "Good quality for the price."},
            ]
        }
        result = _extract_review_snippets(jsonld)
        assert len(result) == 3
        assert "Great product" in result[0]

    def test_empty_reviews(self):
        result = _extract_review_snippets({"name": "Product"})
        assert result == ()

    def test_none_jsonld(self):
        result = _extract_review_snippets(None)
        assert result == ()

    def test_truncation(self):
        jsonld = {"review": [{"reviewBody": "A" * 500}]}
        result = _extract_review_snippets(jsonld)
        assert len(result) == 1
        assert len(result[0]) <= 200


class TestProductEngineEnhanced:
    def test_gallery_in_product_result(self, make_interactable):
        html = PRODUCT_JSONLD + '<div class="gallery"><img src="https://img.com/1.jpg"></div>'
        result = analyze_product(
            raw_html=html,
            html_lower=html.lower(),
            interactables=[],
            metadata={},
            page_url="https://shop.kr/product/1",
        )
        assert len(result.gallery_images) >= 1

    def test_review_snippets_in_product(self, make_interactable):
        jsonld_html = """
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Test",
            "review": [{"reviewBody": "Excellent!"}],
            "offers": {"price": "100", "priceCurrency": "KRW"}
        }
        </script>
        """
        result = analyze_product(
            raw_html=jsonld_html,
            html_lower=jsonld_html.lower(),
            interactables=[],
            metadata={},
            page_url="https://shop.kr/product/2",
        )
        assert len(result.review_snippets) == 1
        assert "Excellent" in result.review_snippets[0]


# ── C4: Cart Engine Enhanced ───────────────────────────────────────


class TestFlowState:
    def test_select_options(self):
        assert _detect_flow_state(("Select Size",), 5) == "select_options"

    def test_ready_to_add(self):
        assert _detect_flow_state((), 5) == "ready_to_add"

    def test_unknown(self):
        assert _detect_flow_state((), None) == "unknown"


class TestCartConfirmation:
    @pytest.mark.parametrize(
        "html,expected",
        [
            pytest.param("added to cart successfully", True, id="en"),
            pytest.param("장바구니에 담았습니다", True, id="ko"),
            pytest.param("カートに入れました", True, id="ja"),
            pytest.param("ajouté au panier", True, id="fr"),
            pytest.param("normal product page", False, id="none"),
        ],
    )
    def test_detect_confirmation(self, html, expected):
        assert _detect_confirmation(html.lower()) == expected


class TestCartCount:
    def test_extract_count(self):
        html = '<span class="cart-count">3</span>'
        assert _extract_cart_count(html.lower()) == 3

    def test_badge_count(self):
        html = '<span class="cart-badge">12</span>'
        assert _extract_cart_count(html.lower()) == 12

    def test_no_count(self):
        html = "<div>normal page</div>"
        assert _extract_cart_count(html.lower()) is None


class TestCartEngineEnhanced:
    def test_flow_state_in_result(self, make_interactable):
        interactables = [
            make_interactable(ref=1, role="button", name="Add to cart", affordance="click"),
        ]
        product = ProductResult(options=())
        result = analyze_cart_actions(
            interactables=interactables,
            html_lower="<html>장바구니에 담았습니다</html>",
            product=product,
        )
        assert result.flow_state == "ready_to_add"
        assert result.confirmation_visible is True
        assert result.add_to_cart_ref == 1


# ── Dataclass backward compatibility ──────────────────────────────


class TestDataclassBackwardCompat:
    def test_product_card_default_sponsored(self):
        card = ProductCard(name="Test")
        assert card.is_sponsored is False

    def test_search_result_default_pagination(self):
        sr = SearchResult()
        assert sr.next_ref is None
        assert sr.prev_ref is None
        assert sr.load_more_ref is None
        assert sr.current_page is None
        assert sr.total_pages is None

    def test_listing_result_default_pagination(self):
        lr = ListingResult()
        assert lr.next_ref is None
        assert lr.prev_ref is None
        assert lr.load_more_ref is None

    def test_product_result_default_gallery(self):
        pr = ProductResult()
        assert pr.gallery_images == ()
        assert pr.selected_variant is None
        assert pr.review_snippets == ()

    def test_cart_action_default_flow(self):
        ca = CartAction()
        assert ca.flow_state == "unknown"
        assert ca.cart_count is None
        assert ca.confirmation_visible is False

    def test_barrier_result_default_gate_type(self):
        br = BarrierResult(
            barrier_type=BarrierType.COOKIE_CONSENT,
            provider="generic",
            auto_dismissible=True,
            accept_ref=None,
            confidence=0.7,
        )
        assert br.gate_type == ""


# ── Hypothesis Fuzz Tests ──────────────────────────────────────────

try:
    from hypothesis import given, settings, strategies as st

    _HAS_HYPOTHESIS = True
except ImportError:
    _HAS_HYPOTHESIS = False


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestEnhancedFuzz:
    @given(html=st.text(min_size=0, max_size=3000))
    @settings(max_examples=100, deadline=2000)
    def test_age_gate_extended_never_crashes(self, html):
        result = detect_age_gate_extended(html.lower())
        assert result is None or isinstance(result, AgeGateInfo)

    @given(html=st.text(min_size=0, max_size=3000))
    @settings(max_examples=100, deadline=2000)
    def test_login_type_never_crashes(self, html):
        result = detect_login_wall(html, html.lower(), "https://example.com", [], "unknown")
        if result is not None:
            assert result.login_type in ("password", "social_only", "mixed")

    @given(raw=st.text(min_size=0, max_size=500))
    @settings(max_examples=100, deadline=2000)
    def test_result_count_never_crashes(self, raw):
        result = _normalize_result_count(raw)
        assert result is None or isinstance(result, int)

    @given(html=st.text(min_size=0, max_size=1000))
    @settings(max_examples=100, deadline=2000)
    def test_cart_flow_never_crashes(self, html):
        result = _detect_confirmation(html.lower())
        assert isinstance(result, bool)

    @given(html=st.text(min_size=0, max_size=2000))
    @settings(max_examples=100, deadline=2000)
    def test_sponsored_never_crashes(self, html):
        result = _detect_sponsored(html)
        assert isinstance(result, bool)
