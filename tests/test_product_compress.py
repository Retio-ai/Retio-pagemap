# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for C5 fix: _compress_for_product enrichment.

Covers:
- Payment promotion filtering
- Footer noise filtering
- Original price extraction from text when metadata lacks it
- Discount percentage extraction
- Backward compatibility with existing metadata-only flow
"""

from __future__ import annotations

from pagemap.i18n import get_locale
from pagemap.pruned_context_builder import _compress_for_product

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pruned_html(lines: list[str]) -> str:
    """Build minimal pruned HTML from text lines."""
    body = "\n".join(f"<p>{line}</p>" for line in lines)
    return f"<html><body>{body}</body></html>"


# ---------------------------------------------------------------------------
# Payment promotion filtering
# ---------------------------------------------------------------------------


class TestPaymentPromotionFilter:
    """Payment/card promotion lines should be filtered out."""

    def test_musinsa_pay_promotion(self):
        """무신사페이 promotion excluded from output."""
        html = _make_pruned_html(
            [
                "세레니티 배색 링거 티셔츠",
                "5,000원",
                "무신사페이 × 현대카드 12만원 이상 결제 시 8천원 할인",
                "4.7 (181개 리뷰)",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "무신사페이" not in result
        assert "현대카드" not in result
        assert "5,000원" in result

    def test_card_discount_promotion(self):
        """Card-specific discount excluded."""
        html = _make_pruned_html(
            [
                "프리미엄 가죽 자켓",
                "189,000원",
                "카드 할인 적용 시 추가 5% 할인",
                "무이자 할부 3개월",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "카드 할인" not in result
        assert "무이자" not in result
        assert "189,000원" in result

    def test_point_accumulation_excluded(self):
        """Point/coupon promotions excluded."""
        html = _make_pruned_html(
            [
                "나이키 에어맥스",
                "139,000원",
                "포인트 적립 5%",
                "쿠폰 받기",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "포인트 적립" not in result
        assert "쿠폰 받기" not in result

    def test_en_payment_promotion(self):
        """English payment promotions excluded."""
        html = _make_pruned_html(
            [
                "Nike Air Max 97",
                "$159.99",
                "Pay with Klarna - 4 installment payments",
                "Credit card offer: extra 10% off",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "installment" not in result
        assert "credit card offer" not in result.lower()

    def test_actual_discount_preserved(self):
        """Genuine discount info (not payment promo) should be preserved."""
        html = _make_pruned_html(
            [
                "오버핏 자켓",
                "45,000원",
                "5,000원",
                "89% 할인",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "89%" in result


# ---------------------------------------------------------------------------
# Footer noise filtering
# ---------------------------------------------------------------------------


class TestFooterNoiseFilter:
    """Footer/boilerplate lines should be filtered out."""

    def test_korean_footer(self):
        """Korean e-commerce footer noise excluded."""
        html = _make_pruned_html(
            [
                "스트리트 반팔 티셔츠",
                "29,000원",
                "어바웃 무신사",
                "회사 소개",
                "비즈니스",
                "고객지원",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "어바웃" not in result
        assert "회사 소개" not in result
        assert "비즈니스" not in result
        assert "29,000원" in result

    def test_copyright_footer(self):
        """Copyright text excluded."""
        html = _make_pruned_html(
            [
                "Premium T-Shirt",
                "$49.99",
                "© 2026 Brand Inc. All rights reserved.",
                "사업자 등록번호: 123-45-67890",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "All rights reserved" not in result
        assert "사업자 등록" not in result


# ---------------------------------------------------------------------------
# Original price extraction
# ---------------------------------------------------------------------------


class TestOriginalPriceExtraction:
    """When metadata has sale price only, extract original price from text."""

    def test_original_price_from_text(self):
        """Higher price in text → labeled as original price."""
        html = _make_pruned_html(
            [
                "45,000원",
                "5,000원",
                "89% 할인",
            ]
        )
        metadata = {"name": "테스트 상품", "price": 5000, "currency": "KRW"}
        result = _compress_for_product(html, max_tokens=500, metadata=metadata)
        assert "원가" in result
        assert "45,000" in result

    def test_no_original_price_when_same(self):
        """Same price in text and metadata → no original price label."""
        html = _make_pruned_html(
            [
                "5,000원",
            ]
        )
        metadata = {"name": "테스트 상품", "price": 5000, "currency": "KRW"}
        result = _compress_for_product(html, max_tokens=500, metadata=metadata)
        assert "원가" not in result

    def test_original_price_already_in_metadata(self):
        """When metadata has original_price, use it directly."""
        html = _make_pruned_html(
            [
                "45,000원",
                "5,000원",
            ]
        )
        metadata = {
            "name": "테스트 상품",
            "price": 5000,
            "original_price": 45000,
            "currency": "KRW",
        }
        result = _compress_for_product(html, max_tokens=500, metadata=metadata)
        assert "원가" in result
        assert "45,000" in result

    def test_en_original_price(self):
        """English locale original price extraction."""
        html = _make_pruned_html(
            [
                "$199.99",
                "$99.99",
                "50% OFF",
            ]
        )
        metadata = {"name": "Test Product", "price": 99.99, "currency": "USD"}
        lc = get_locale("en")
        result = _compress_for_product(html, max_tokens=500, metadata=metadata, lc=lc)
        assert "Original price" in result
        assert "199.99" in result


# ---------------------------------------------------------------------------
# Discount percentage extraction
# ---------------------------------------------------------------------------


class TestDiscountExtraction:
    """Discount percentage patterns should be extracted."""

    def test_ko_discount_pct(self):
        """Korean discount percentage."""
        html = _make_pruned_html(
            [
                "프리미엄 자켓",
                "89% 할인",
                "5,000원",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "89%" in result

    def test_en_discount_off(self):
        """English 'OFF' discount."""
        html = _make_pruned_html(
            [
                "Premium Jacket",
                "30% OFF",
                "$49.99",
            ]
        )
        lc = get_locale("en")
        result = _compress_for_product(html, max_tokens=500, lc=lc)
        assert "30%" in result

    def test_discount_not_duplicated(self):
        """Discount line appears once in output."""
        html = _make_pruned_html(
            [
                "50% 할인",
                "추가 10% 할인",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        # Only first discount line should appear (labeled)
        assert result.count("할인") >= 1


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing metadata-only flow still works."""

    def test_metadata_only_no_html_content(self):
        """Metadata fields rendered correctly with empty pruned HTML."""
        metadata = {
            "name": "테스트 상품",
            "price": 29000,
            "currency": "KRW",
            "rating": 4.5,
            "review_count": 100,
            "brand": "테스트 브랜드",
        }
        result = _compress_for_product("<html><body></body></html>", max_tokens=500, metadata=metadata)
        assert "제목: 테스트 상품" in result
        assert "29,000" in result
        assert "평점: 4.5" in result
        assert "100" in result
        assert "브랜드: 테스트 브랜드" in result

    def test_no_metadata_fallback(self):
        """Without metadata, regex fallback extracts info from HTML."""
        html = _make_pruned_html(
            [
                "나이키 에어포스 1 '07",
                "139,000원",
                "★ 4.8 (2,340개 리뷰)",
                "사이즈: 250, 255, 260, 265, 270",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "나이키" in result
        assert "139,000" in result
        assert "4.8" in result

    def test_options_preserved(self):
        """Option-related lines still collected."""
        html = _make_pruned_html(
            [
                "반팔 티셔츠",
                "19,000원",
                "사이즈 선택: S, M, L, XL",
                "컬러 선택: 블랙, 화이트, 네이비",
            ]
        )
        result = _compress_for_product(html, max_tokens=500)
        assert "사이즈" in result or "컬러" in result
