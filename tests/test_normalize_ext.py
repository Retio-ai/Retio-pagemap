"""Tests for normalize.py extensions: format_price, infer_currency, normalize_numeric, normalize_date."""

from __future__ import annotations

import pytest

from pagemap.preprocessing.normalize import (
    format_price,
    infer_currency,
    normalize_date,
    normalize_numeric,
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
        ],
    )
    def test_infer_currency(self, url, expected):
        assert infer_currency(url) == expected

    def test_unknown_domain_defaults_to_krw(self):
        assert infer_currency("https://unknown.example.org/page") == "KRW"


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
