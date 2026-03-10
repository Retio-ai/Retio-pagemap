"""Integration tests: normalize_price across 30 ecommerce site price formats."""

from __future__ import annotations

import pytest

from pagemap.preprocessing.normalize import normalize_price


class TestSitePriceFormats:
    """Test real-world price strings from 30+ ecommerce sites."""

    @pytest.mark.parametrize(
        "site,text,url,expected_amount,expected_currency",
        [
            # Korean sites
            ("coupang", "159,000원", "https://www.coupang.com/vp/products/123", 159000.0, "KRW"),
            ("coupang", "₩13,900", "https://www.coupang.com/vp/products/456", 13900.0, "KRW"),
            ("musinsa", "89,000원", "https://www.musinsa.com/app/goods/123", 89000.0, "KRW"),
            ("ssg", "₩45,900", "https://www.ssg.com/item/123", 45900.0, "KRW"),
            ("gmarket", "29,800원", "https://item.gmarket.co.kr/Item?id=123", 29800.0, "KRW"),
            # US sites
            ("amazon_us", "$29.99", "https://www.amazon.com/dp/123", 29.99, "USD"),
            ("amazon_us", "$1,234.56", "https://www.amazon.com/dp/456", 1234.56, "USD"),
            ("walmart", "$19.97", "https://www.walmart.com/ip/123", 19.97, "USD"),
            ("ebay", "$149.99", "https://www.ebay.com/itm/123", 149.99, "USD"),
            ("nordstrom", "$89.50", "https://www.nordstrom.com/s/123", 89.50, "USD"),
            # UK sites
            ("asos", "£45.00", "https://www.asos.com/product/123", 45.00, "GBP"),
            # European sites
            ("zalando_de", "€89,95", "https://www.zalando.de/product/123", 89.95, "EUR"),
            ("zalando_fr", "€79,99", "https://www.zalando.fr/product/123", 79.99, "EUR"),
            # Japanese sites
            ("rakuten", "¥3,980", "https://www.rakuten.co.jp/item/123", 3980.0, "JPY"),
            ("rakuten", "3,980円", "https://www.rakuten.co.jp/item/456", 3980.0, "JPY"),
            # Chinese sites
            ("taobao", "¥199", "https://item.taobao.com/item.htm?id=123", 199.0, "CNY"),
            ("jd", "¥2,999", "https://item.jd.com/123.html", 2999.0, "CNY"),
            # Indian sites
            ("flipkart", "₹1,499", "https://www.flipkart.com/item/123", 1499.0, "INR"),
            ("amazon_in", "₹12,999", "https://www.amazon.in/dp/123", 12999.0, "INR"),
            # Brazilian sites
            ("americanas", "R$199,90", "https://www.americanas.com.br/item/123", 199.90, "BRL"),
            ("magazineluiza", "R$1.234,56", "https://www.magazineluiza.com.br/item/123", 1234.56, "BRL"),
            # Turkish sites
            ("hepsiburada", "₺1.299,00", "https://www.hepsiburada.com/item/123", 1299.00, "TRY"),
            ("trendyol", "₺599,99", "https://www.trendyol.com/item/123", 599.99, "TRY"),
            # Southeast Asian sites
            ("lazada_sg", "S$49.90", "https://www.lazada.sg/item/123", 49.90, "SGD"),
            # International sites with USD
            ("farfetch", "$1,295", "https://www.farfetch.com/item/123", 1295.0, "USD"),
            ("aliexpress", "US $12.99", "https://www.aliexpress.com/item/123", 12.99, "USD"),
            # Swiss franc
            ("swiss_store", "CHF 89.90", "https://store.example.ch/item/123", 89.90, "CHF"),
        ],
        ids=lambda val: str(val) if not isinstance(val, float) else "",
    )
    def test_site_price(self, site, text, url, expected_amount, expected_currency):
        result = normalize_price(text, url_hint=url)
        assert len(result.prices) >= 1, f"Failed to parse price for {site}: {text!r}"
        assert result.prices[0].amount == pytest.approx(expected_amount, rel=1e-2), (
            f"Wrong amount for {site}: expected {expected_amount}, got {result.prices[0].amount}"
        )
        assert result.prices[0].currency == expected_currency, (
            f"Wrong currency for {site}: expected {expected_currency}, got {result.prices[0].currency}"
        )


class TestSitePriceRanges:
    @pytest.mark.parametrize(
        "site,text,url,expected_low,expected_high",
        [
            ("coupang_range", "₩10,000 ~ ₩30,000", "https://coupang.com/item", 10000.0, 30000.0),
            ("amazon_range", "$25 – $50", "https://amazon.com/item", 25.0, 50.0),
            ("zalando_range", "€39,95 – €79,95", "https://zalando.de/item", 39.95, 79.95),
        ],
    )
    def test_site_range(self, site, text, url, expected_low, expected_high):
        result = normalize_price(text, url_hint=url)
        assert result.is_range is True
        assert result.prices[0].amount == pytest.approx(expected_low, rel=1e-2)
        assert result.prices[1].amount == pytest.approx(expected_high, rel=1e-2)


class TestSiteFreeAndUnavailable:
    @pytest.mark.parametrize(
        "site,text,expected_type",
        [
            ("us_free", "Free", "free"),
            ("kr_free", "무료", "free"),
            ("jp_free", "無料", "free"),
            ("de_free", "kostenlos", "free"),
            ("kr_soldout", "품절", "unavailable"),
            ("us_soldout", "Sold out", "unavailable"),
            ("jp_soldout", "売り切れ", "unavailable"),
            ("fr_soldout", "épuisé", "unavailable"),
        ],
    )
    def test_site_nonprice(self, site, text, expected_type):
        result = normalize_price(text)
        assert len(result.prices) == 1
        assert result.prices[0].price_type == expected_type


class TestSiteFromPrices:
    @pytest.mark.parametrize(
        "site,text,url,expected_amount",
        [
            ("us_from", "from $99", "https://amazon.com/item", 99.0),
            ("kr_from", "부터 ₩50,000", "https://coupang.com/item", 50000.0),
            ("de_from", "ab €49,90", "https://store.example.de/item", 49.90),
            ("fr_from", "à partir de €29,99", "https://store.example.fr/item", 29.99),
        ],
    )
    def test_site_from_price(self, site, text, url, expected_amount):
        result = normalize_price(text, url_hint=url)
        assert len(result.prices) >= 1
        assert result.prices[0].price_type == "from"
        assert result.prices[0].amount == pytest.approx(expected_amount, rel=1e-2)


class TestPriceParseResultStructure:
    def test_empty_input(self):
        result = normalize_price("")
        assert result.prices == ()
        assert result.is_range is False

    def test_whitespace_input(self):
        result = normalize_price("   ")
        assert result.prices == ()

    def test_unparseable_text(self):
        result = normalize_price("hello world no price here")
        assert result.prices == ()

    def test_original_text_preserved(self):
        result = normalize_price("$29.99", url_hint="https://amazon.com/item")
        assert result.original_text == "$29.99"

    def test_price_result_fields(self):
        result = normalize_price("$99.99", url_hint="https://amazon.com/item")
        assert len(result.prices) == 1
        p = result.prices[0]
        assert p.amount == pytest.approx(99.99)
        assert p.currency == "USD"
        assert p.confidence > 0
        assert p.price_type == "exact"
        assert p.raw_text
