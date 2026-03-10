"""Tests for normalize.py extensions: format_price, infer_currency, normalize_numeric, normalize_date, normalize_price."""

from __future__ import annotations

import pytest

from pagemap.preprocessing.normalize import (
    detect_currency_from_text,
    format_price,
    infer_currency,
    normalize_date,
    normalize_numeric,
    normalize_price,
)


class TestFormatPrice:
    @pytest.mark.parametrize(
        "amount,currency,expected",
        [
            (159000.0, "KRW", "159,000원"),
            (13900.0, "KRW", "13,900원"),
            (29.99, "USD", "$29.99"),
            (1980.0, "JPY", "1,980円"),
            (49.99, "EUR", "€49.99"),
            (29.99, "GBP", "£29.99"),
            (1000.0, "KRW", "1,000원"),
            (0.0, "KRW", "0원"),
            # New currencies
            (45.00, "CHF", "CHF 45.00"),
            (299.00, "SEK", "299.00 kr"),
            (199.00, "NOK", "199.00 kr"),
            (149.00, "DKK", "149.00 kr"),
            (59.95, "AUD", "$59.95 AUD"),
            (39.99, "CAD", "$39.99 CAD"),
            (49.99, "NZD", "$49.99 NZD"),
        ],
    )
    def test_format_price(self, amount, currency, expected):
        assert format_price(amount, currency) == expected

    def test_default_currency_is_krw(self):
        assert format_price(10000.0) == "10,000원"


class TestInferCurrency:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.coupang.com/vp/products/123", "KRW"),
            ("https://www.amazon.com/dp/123", "USD"),
            ("https://www.musinsa.com/app/goods/123", "KRW"),
            ("https://store.example.co.kr/item/1", "KRW"),
            ("https://store.example.co.jp/item/1", "JPY"),
            ("https://store.example.co.uk/item/1", "GBP"),
            ("https://29cm.co.kr/product/123", "KRW"),
            # New TLD mappings
            ("https://store.example.fr/item/1", "EUR"),
            ("https://store.example.de/item/1", "EUR"),
            ("https://store.example.es/item/1", "EUR"),
            ("https://store.example.it/item/1", "EUR"),
            ("https://store.example.nl/item/1", "EUR"),
            ("https://store.example.se/item/1", "SEK"),
            ("https://store.example.no/item/1", "NOK"),
            ("https://store.example.dk/item/1", "DKK"),
            ("https://store.example.ch/item/1", "CHF"),
            ("https://store.example.com.au/item/1", "AUD"),
            ("https://store.example.co.nz/item/1", "NZD"),
            ("https://store.example.ca/item/1", "CAD"),
            # Substring false-positive prevention (notjd.com must NOT match jd.com→CNY)
            ("https://notjd.com/item", "USD"),
            ("https://fakecoupang.com/item", "USD"),
        ],
    )
    def test_infer_currency(self, url, expected):
        assert infer_currency(url) == expected

    def test_unknown_domain_defaults_to_usd(self):
        assert infer_currency("https://unknown.example.org/page") == "USD"


class TestNormalizeNumeric:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("13,900", 13900.0),
            ("13900", 13900.0),
            ("₩13,900", 13900.0),
            ("$29.99", 29.99),
            ("159,000원", 159000.0),
            ("¥1,980", 1980.0),
            ("€49.99", 49.99),
            ("£29.99", 29.99),
            (None, None),
            ("invalid", None),
        ],
    )
    def test_normalize_numeric(self, value, expected):
        assert normalize_numeric(value) == expected


class TestNormalizeDate:
    @pytest.mark.parametrize(
        "date_str,expected",
        [
            # ISO
            ("2026-02-11", "2026-02-11"),
            ("2026-2-1", "2026-02-01"),
            ("2026-02-11T10:00:00", "2026-02-11"),
            # Korean
            ("2026년 2월 11일", "2026-02-11"),
            ("2026년2월11일", "2026-02-11"),
            # Japanese
            ("2026年2月11日", "2026-02-11"),
            ("2026年 2月 11日", "2026-02-11"),
            ("2026年12月1日", "2026-12-01"),
            # Dot
            ("2026.02.11", "2026-02-11"),
            ("2026.2.1", "2026-02-01"),
            # No match
            ("", None),
            ("hello", "hello"),
        ],
    )
    def test_normalize_date(self, date_str, expected):
        assert normalize_date(date_str) == expected


class TestDetectCurrencyFromText:
    @pytest.mark.parametrize(
        "text,url,expected_currency",
        [
            # Multi-char symbols
            ("R$199,90", "https://americanas.com.br/item", "BRL"),
            ("S$49.99", "https://lazada.sg/item", "SGD"),
            ("HK$399", "https://example.com.hk/item", "HKD"),
            ("NT$1,299", "https://example.com.tw/item", "TWD"),
            ("RM89.90", "https://example.com.my/item", "MYR"),
            ("MX$999", "https://example.com.mx/item", "MXN"),
            # Single-char symbols
            ("₩159,000", "", "KRW"),
            ("€49.99", "", "EUR"),
            ("£29.99", "", "GBP"),
            ("₹1,499", "", "INR"),
            ("₺299.90", "", "TRY"),
            ("₫450,000", "", "VND"),
            ("₱2,499", "", "PHP"),
            ("฿990", "", "THB"),
            # Suffixes
            ("159,000원", "", "KRW"),
            ("1,980円", "", "JPY"),
            # ISO codes
            ("USD 29.99", "", "USD"),
            ("CHF 45.00", "", "CHF"),
            # Ambiguous $ resolved by URL
            ("$59.95", "https://store.example.com.au/item", "AUD"),
            ("$39.99", "https://store.example.ca/item", "CAD"),
            ("$29.99", "https://www.amazon.com/item", "USD"),
            # Ambiguous ¥ resolved by URL
            ("¥1,980", "https://store.example.co.jp/item", "JPY"),
            ("¥199", "https://taobao.com/item", "CNY"),
            # Ambiguous kr resolved by URL
            ("299 kr", "https://store.example.se/item", "SEK"),
            ("199 kr", "https://store.example.no/item", "NOK"),
        ],
    )
    def test_detect_currency(self, text, url, expected_currency):
        currency, confidence = detect_currency_from_text(text, url)
        assert currency == expected_currency
        assert 0.0 < confidence <= 1.0


class TestNormalizePriceLocaleFormats:
    @pytest.mark.parametrize(
        "text,url,expected_amount,expected_currency",
        [
            # US format
            ("$1,234.56", "https://amazon.com/item", 1234.56, "USD"),
            ("$29.99", "https://amazon.com/item", 29.99, "USD"),
            # German format (dot grouping, comma decimal)
            ("€1.234,56", "https://store.example.de/item", 1234.56, "EUR"),
            # French format (space grouping, comma decimal)
            ("€1 234,56", "https://store.example.fr/item", 1234.56, "EUR"),
            # Korean format (no decimal)
            ("₩159,000", "https://coupang.com/item", 159000.0, "KRW"),
            ("159,000원", "https://coupang.com/item", 159000.0, "KRW"),
            # Japanese format
            ("¥1,980", "https://amazon.co.jp/item", 1980.0, "JPY"),
            ("1,980円", "https://amazon.co.jp/item", 1980.0, "JPY"),
            # Indian format (lakh grouping)
            ("₹1,23,456.78", "https://flipkart.com/item", 123456.78, "INR"),
            # Brazilian format
            ("R$1.234,56", "https://americanas.com.br/item", 1234.56, "BRL"),
            # Turkish format
            ("₺1.234,56", "https://hepsiburada.com/item", 1234.56, "TRY"),
            # Simple integer
            ("$100", "https://amazon.com/item", 100.0, "USD"),
            ("₩10000", "https://coupang.com/item", 10000.0, "KRW"),
        ],
    )
    def test_locale_formats(self, text, url, expected_amount, expected_currency):
        result = normalize_price(text, url_hint=url)
        assert len(result.prices) >= 1
        assert result.prices[0].amount == pytest.approx(expected_amount, rel=1e-2)
        assert result.prices[0].currency == expected_currency


class TestNormalizePriceRanges:
    @pytest.mark.parametrize(
        "text,url,low,high",
        [
            ("₩10,000 ~ ₩20,000", "https://coupang.com/item", 10000.0, 20000.0),
            ("$10 – $20", "https://amazon.com/item", 10.0, 20.0),
            ("€50 - €100", "https://store.example.de/item", 50.0, 100.0),
            ("£30 — £60", "https://store.example.co.uk/item", 30.0, 60.0),
        ],
    )
    def test_price_ranges(self, text, url, low, high):
        result = normalize_price(text, url_hint=url)
        assert result.is_range is True
        assert len(result.prices) == 2
        assert result.prices[0].amount == pytest.approx(low, rel=1e-2)
        assert result.prices[0].price_type == "range_low"
        assert result.prices[1].amount == pytest.approx(high, rel=1e-2)
        assert result.prices[1].price_type == "range_high"


class TestNormalizePriceFromPrice:
    @pytest.mark.parametrize(
        "text,url,expected_amount,expected_type",
        [
            ("from $99", "https://amazon.com/item", 99.0, "from"),
            ("부터 ₩10,000", "https://coupang.com/item", 10000.0, "from"),
            ("ab €99", "https://store.example.de/item", 99.0, "from"),
            ("à partir de €50", "https://store.example.fr/item", 50.0, "from"),
            ("desde $199", "https://store.example.es/item", 199.0, "from"),
            ("starting at $49.99", "https://amazon.com/item", 49.99, "from"),
        ],
    )
    def test_from_prices(self, text, url, expected_amount, expected_type):
        result = normalize_price(text, url_hint=url)
        assert len(result.prices) >= 1
        assert result.prices[0].amount == pytest.approx(expected_amount, rel=1e-2)
        assert result.prices[0].price_type == expected_type

    def test_from_price_no_false_positive_about(self):
        """'about' should not trigger 'ab' from-price term."""
        result = normalize_price("about $29.99", url_hint="https://amazon.com/item")
        assert len(result.prices) >= 1
        assert result.prices[0].price_type == "exact"

    def test_from_price_no_false_positive_data(self):
        """'data' should not trigger 'da' from-price term."""
        result = normalize_price("data plan $9.99", url_hint="https://amazon.com/item")
        assert len(result.prices) >= 1
        assert result.prices[0].price_type == "exact"

    def test_from_price_legitimate_ab(self):
        """German 'ab' as standalone word should still trigger from-price."""
        result = normalize_price("ab €29,99", url_hint="https://store.example.de/item")
        assert len(result.prices) >= 1
        assert result.prices[0].price_type == "from"


class TestNormalizePriceCrossListed:
    def test_cross_listed_usd_krw(self):
        result = normalize_price("$99.99 (약 ₩130,000)", url_hint="https://amazon.com/item")
        assert result.is_cross_listed is True
        assert len(result.prices) == 2
        assert result.prices[0].amount == pytest.approx(99.99, rel=1e-2)
        assert result.prices[1].amount == pytest.approx(130000.0, rel=1e-2)

    def test_cross_listed_eur_gbp(self):
        result = normalize_price("€89.99 (≈ £79.99)", url_hint="https://store.example.de/item")
        assert result.is_cross_listed is True
        assert len(result.prices) == 2


class TestNormalizePriceNonPrice:
    @pytest.mark.parametrize(
        "text,expected_type",
        [
            ("Free", "free"),
            ("free", "free"),
            ("무료", "free"),
            ("無料", "free"),
            ("gratuit", "free"),
            ("gratis", "free"),
            ("Sold out", "unavailable"),
            ("품절", "unavailable"),
            ("売り切れ", "unavailable"),
            ("épuisé", "unavailable"),
            ("ausverkauft", "unavailable"),
            ("out of stock", "unavailable"),
        ],
    )
    def test_non_price_detection(self, text, expected_type):
        result = normalize_price(text)
        assert len(result.prices) == 1
        assert result.prices[0].price_type == expected_type
        assert result.prices[0].amount == 0.0


class TestNormalizePriceRTL:
    def test_arabic_indic_digits(self):
        result = normalize_price("٩٩٫٩٩ USD", url_hint="https://example.com")
        assert len(result.prices) >= 1
        assert result.prices[0].amount == pytest.approx(99.99, rel=1e-2)

    def test_rtl_marks_stripped(self):
        text = "\u200f$29.99\u200f"
        result = normalize_price(text, url_hint="https://amazon.com/item")
        assert len(result.prices) >= 1
        assert result.prices[0].amount == pytest.approx(29.99, rel=1e-2)


class TestFormatPriceExtended:
    @pytest.mark.parametrize(
        "amount,currency,expected",
        [
            (199.90, "BRL", "R$199.90"),
            (299.90, "TRY", "₺299.90"),
            (199.00, "CNY", "¥199.00"),
            (1299, "TWD", "NT$1,299"),
            (990.00, "THB", "฿990.00"),
            (89.90, "MYR", "RM89.90"),
            (49.99, "SGD", "S$49.99"),
            (2499.00, "PHP", "₱2,499.00"),
            (999.00, "MXN", "MX$999.00"),
            # Indian lakh grouping
            (123456.78, "INR", "₹1,23,456.78"),
            (1500.00, "INR", "₹1,500.00"),
            (99.00, "INR", "₹99.00"),
        ],
    )
    def test_format_price_extended(self, amount, currency, expected):
        assert format_price(amount, currency) == expected
