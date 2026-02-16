"""Tests for pruner.py i18n regex extensions.

Verifies that expanded regex patterns match ja/fr/de/en keywords
while preserving existing ko/en behaviour.
"""

from __future__ import annotations

import pytest

from pagemap.pruning.pruner import (
    _BRAND_RE,
    _CONTACT_RE,
    _DEPARTMENT_RE,
    _FEATURE_RE,
    _PRICE_RE,
    _PRICING_RE,
    _RATING_RE,
    _REPORTER_RE,
    _REVIEW_COUNT_RE,
)

# ---------------------------------------------------------------------------
# PRICE_RE
# ---------------------------------------------------------------------------


class TestPriceRE:
    @pytest.mark.parametrize(
        "text",
        [
            "₩13,900",
            "13,900원",
            "189,000",
            "$29.99",
            "¥3,990",
            "€49.99",
            "£29.99",
            "CHF 45.00",
            "SEK 299",
            "AUD 59.95",
            "CAD 39.99",
        ],
    )
    def test_matches(self, text):
        assert _PRICE_RE.search(text)

    def test_no_match_plain_text(self):
        assert not _PRICE_RE.search("hello world")


# ---------------------------------------------------------------------------
# RATING_RE
# ---------------------------------------------------------------------------


class TestRatingRE:
    @pytest.mark.parametrize(
        "text",
        [
            "★★★★☆",
            "평점 4.5",
            "5 stars",
            "rating 4.5",
            "rated 4.0",
            "評価 4.2",        # ja
            "レビュー",        # ja
            "étoile",          # fr
            "Bewertung 4.5",   # de
            "Sterne",          # de
            "4.5",             # numeric
        ],
    )
    def test_matches(self, text):
        assert _RATING_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "note",     # removed — conflicts with English "note"
            "star",     # removed — "starting", "startup" false positive
        ],
    )
    def test_removed_terms_no_match(self, text):
        """Terms removed from RATING_TERMS should no longer match."""
        assert not _RATING_RE.search(text)


# ---------------------------------------------------------------------------
# REVIEW_COUNT_RE
# ---------------------------------------------------------------------------


class TestReviewCountRE:
    @pytest.mark.parametrize(
        "text",
        [
            "123개",
            "50건",
            "42 reviews",
            "1 review",
            "100리뷰",
            "50 Bewertungen",  # de
            "30 Bewertung",    # de
            "25 avis",         # fr
            "10件",            # ja
        ],
    )
    def test_matches(self, text):
        assert _REVIEW_COUNT_RE.search(text)


# ---------------------------------------------------------------------------
# REPORTER_RE
# ---------------------------------------------------------------------------


class TestReporterRE:
    @pytest.mark.parametrize(
        "text",
        [
            "홍길동 기자",
            "reporter: John",
            "편집: 김영희",
            "記者: 田中",       # ja
            "journaliste",     # fr
            "rédacteur",       # fr
            "Reporter: Max",   # de
            "Journalist",      # de
            "Redakteur",       # de
        ],
    )
    def test_matches(self, text):
        assert _REPORTER_RE.search(text)


# ---------------------------------------------------------------------------
# CONTACT_RE
# ---------------------------------------------------------------------------


class TestContactRE:
    @pytest.mark.parametrize(
        "text",
        [
            "전화: 02-1234",
            "tel: 555-0100",
            "주소: 서울",
            "address: 123 Main St",
            "email: test@example.com",
            "電話: 03-1234",    # ja
            "住所: 東京都",     # ja
            "téléphone",       # fr
            "adresse",         # fr
            "courriel",        # fr
            "Telefon",         # de
            "Kontakt",         # de
        ],
    )
    def test_matches(self, text):
        assert _CONTACT_RE.search(text)


# ---------------------------------------------------------------------------
# BRAND_RE
# ---------------------------------------------------------------------------


class TestBrandRE:
    @pytest.mark.parametrize(
        "text",
        [
            "브랜드: Nike",
            "brand: Nike",
            "제조사: Apple",
            "manufacturer",
            "ブランド: Nike",   # ja
            "メーカー",        # ja
            "marque: Zara",    # fr
            "fabricant",       # fr
            "Marke: Nike",     # de
            "Hersteller",      # de
        ],
    )
    def test_matches(self, text):
        assert _BRAND_RE.search(text)


# ---------------------------------------------------------------------------
# DEPARTMENT_RE — with "원" false positive fix
# ---------------------------------------------------------------------------


class TestDepartmentRE:
    @pytest.mark.parametrize(
        "text",
        [
            "기관",
            "부처",
            "department of education",
            "ministry of health",
            "위원회",
            "국세청",
            "省",              # ja
            "庁",              # ja
            "委員会",          # ja
            "ministère",       # fr
            "département",     # fr
            "Ministerium",     # de
            "Behörde",         # de
            "Amt",             # de
        ],
    )
    def test_matches(self, text):
        assert _DEPARTMENT_RE.search(text)

    def test_standalone_won_matches(self):
        """Korean '원' (institution suffix) in non-price context should match."""
        assert _DEPARTMENT_RE.search("한국은행 금융통화위원회 산하 원")

    @pytest.mark.parametrize(
        "text",
        [
            "189,000원",
            "13900원",
            "3,990원",
        ],
    )
    def test_price_won_does_not_match(self, text):
        """'원' preceded by digits/commas (price) must NOT match."""
        assert not _DEPARTMENT_RE.search(text)


# ---------------------------------------------------------------------------
# FEATURE_RE
# ---------------------------------------------------------------------------


class TestFeatureRE:
    @pytest.mark.parametrize(
        "text",
        [
            "feature list",
            "기능",
            "특징",
            "機能",            # ja
            "特徴",            # ja
            "fonctionnalité",  # fr
            "caractéristique", # fr
            "Funktion",        # de
            "Merkmal",         # de
        ],
    )
    def test_matches(self, text):
        assert _FEATURE_RE.search(text)


# ---------------------------------------------------------------------------
# PRICING_RE
# ---------------------------------------------------------------------------


class TestPricingRE:
    @pytest.mark.parametrize(
        "text",
        [
            "price: $10",
            "pricing table",
            "₩10,000",
            "$29.99",
            "€49.99",
            "요금 안내",
            "가격 비교",
            "価格",            # ja
            "料金",            # ja
            "prix: 49€",       # fr
            "tarif",           # fr
            "Preis: 49€",      # de
            "Preise",          # de
        ],
    )
    def test_matches(self, text):
        assert _PRICING_RE.search(text)
