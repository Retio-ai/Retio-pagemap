"""Tests for pagemap.i18n — LocaleConfig, detect_locale, get_locale, keyword tuples."""

from __future__ import annotations

import pytest

from pagemap.i18n import (
    BRAND_TERMS,
    CONTACT_TERMS,
    DEFAULT_LOCALE,
    DEPARTMENT_TERMS,
    FEATURE_TERMS,
    FILTER_TERMS,
    LISTING_TERMS,
    LOAD_MORE_TERMS,
    NEXT_BUTTON_TERMS,
    OPTION_TERMS,
    PRICE_LABEL_TERMS,
    PRICE_TERMS,
    PRICING_TERMS,
    RATING_TERMS,
    REPORTER_TERMS,
    REVIEW_COUNT_TERMS,
    SEARCH_RESULT_TERMS,
    SHIPPING_TERMS,
    detect_locale,
    get_locale,
)

# ---------------------------------------------------------------------------
# LocaleConfig
# ---------------------------------------------------------------------------


class TestLocaleConfig:
    def test_default_locale_is_en(self):
        assert DEFAULT_LOCALE == "en"

    def test_get_locale_none_returns_en(self):
        lc = get_locale(None)
        assert lc.code == "en"
        assert lc.label_title == "Title"

    def test_get_locale_ko(self):
        lc = get_locale("ko")
        assert lc.code == "ko"
        assert lc.default_currency == "KRW"

    def test_get_locale_en(self):
        lc = get_locale("en")
        assert lc.code == "en"
        assert lc.label_title == "Title"
        assert lc.default_currency == "USD"

    def test_get_locale_ja(self):
        lc = get_locale("ja")
        assert lc.code == "ja"
        assert lc.label_title == "\u30bf\u30a4\u30c8\u30eb"
        assert lc.default_currency == "JPY"
        assert lc.date_ymd_suffixes == ("\u5e74", "\u6708", "\u65e5")

    def test_get_locale_fr(self):
        lc = get_locale("fr")
        assert lc.code == "fr"
        assert lc.label_title == "Titre"
        assert lc.default_currency == "EUR"

    def test_get_locale_de(self):
        lc = get_locale("de")
        assert lc.code == "de"
        assert lc.label_title == "Titel"
        assert lc.default_currency == "EUR"

    def test_unknown_locale_falls_back_to_en(self):
        lc = get_locale("xx")
        assert lc.code == "en"

    def test_locale_config_is_frozen(self):
        lc = get_locale("en")
        with pytest.raises(AttributeError):
            lc.code = "ja"  # type: ignore[misc]

    def test_overflow_template(self):
        lc = get_locale("ko")
        assert lc.overflow_template.format(n=5) == "\uc678 5\uac74"

    def test_review_template(self):
        lc = get_locale("en")
        assert lc.review_template.format(count=42) == "(42 reviews)"


# ---------------------------------------------------------------------------
# detect_locale
# ---------------------------------------------------------------------------


class TestDetectLocale:
    # Path segment detection
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.zara.com/jp/ja/", "ja"),
            ("https://www.uniqlo.com/jp/ja/products/123", "ja"),
            ("https://www.nike.com/fr/w/shoes", "fr"),
            ("https://www.zara.com/de/de/woman", "de"),
            ("https://www.hm.com/en/products/123", "en"),
            ("https://www.cos.com/ko/men/shoes", "ko"),
        ],
    )
    def test_path_segment(self, url, expected):
        assert detect_locale(url) == expected

    # Subdomain detection
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://ja.zara.com/", "ja"),
            ("https://fr.nike.com/shoes", "fr"),
            ("https://de.example.com/page", "de"),
        ],
    )
    def test_subdomain(self, url, expected):
        assert detect_locale(url) == expected

    # Exact domain detection
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.coupang.com/vp/products/123", "ko"),
            ("https://www.musinsa.com/app/goods/123", "ko"),
        ],
    )
    def test_exact_domain(self, url, expected):
        assert detect_locale(url) == expected

    # TLD detection
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://store.example.co.kr/item/1", "ko"),
            ("https://store.example.co.jp/item/1", "ja"),
            ("https://store.example.fr/item/1", "fr"),
            ("https://store.example.de/item/1", "de"),
            ("https://store.example.co.uk/item/1", "en"),
            ("https://www.amazon.com/dp/123", "en"),
        ],
    )
    def test_tld(self, url, expected):
        assert detect_locale(url) == expected

    def test_unknown_falls_back_to_default(self):
        assert detect_locale("https://unknown.example.org/page") == DEFAULT_LOCALE

    def test_path_takes_priority_over_tld(self):
        # French path on .com domain
        assert detect_locale("https://www.nike.com/fr/shoes") == "fr"

    def test_path_takes_priority_over_subdomain(self):
        # /ja/ path even if no ja subdomain
        assert detect_locale("https://www.zara.com/jp/ja/products") == "ja"


# ---------------------------------------------------------------------------
# Keyword tuples — sanity checks
# ---------------------------------------------------------------------------


class TestKeywordTuples:
    @pytest.mark.parametrize(
        "terms",
        [
            PRICE_TERMS,
            RATING_TERMS,
            REVIEW_COUNT_TERMS,
            REPORTER_TERMS,
            CONTACT_TERMS,
            BRAND_TERMS,
            DEPARTMENT_TERMS,
            FEATURE_TERMS,
            PRICING_TERMS,
            SEARCH_RESULT_TERMS,
            LISTING_TERMS,
            FILTER_TERMS,
            NEXT_BUTTON_TERMS,
            LOAD_MORE_TERMS,
            PRICE_LABEL_TERMS,
            OPTION_TERMS,
        ],
    )
    def test_all_terms_are_tuples(self, terms):
        assert isinstance(terms, tuple)
        assert len(terms) > 0

    @pytest.mark.parametrize(
        "terms",
        [
            PRICE_TERMS,
            RATING_TERMS,
            REVIEW_COUNT_TERMS,
            REPORTER_TERMS,
            CONTACT_TERMS,
            BRAND_TERMS,
            DEPARTMENT_TERMS,
            FEATURE_TERMS,
            PRICING_TERMS,
            SEARCH_RESULT_TERMS,
            LISTING_TERMS,
            FILTER_TERMS,
            NEXT_BUTTON_TERMS,
            LOAD_MORE_TERMS,
            PRICE_LABEL_TERMS,
            OPTION_TERMS,
        ],
    )
    def test_no_regex_quantifiers(self, terms):
        """Terms must not contain regex quantifiers (?, *, +) intended as patterns."""
        import re

        # Compile all terms through re.escape — must not raise
        pattern = "|".join(re.escape(t) for t in terms)
        re.compile(pattern)
        # Check no quantifier-like chars that suggest someone wrote a regex pattern
        for term in terms:
            assert "?" not in term, f"Term has regex quantifier: {term!r}"
            assert not term.startswith("*"), f"Term starts with *: {term!r}"
            assert not term.startswith("+"), f"Term starts with +: {term!r}"

    def test_price_terms_has_multilingual(self):
        assert "\u20a9" in PRICE_TERMS  # KRW symbol
        assert "$" in PRICE_TERMS
        assert "\u00a5" in PRICE_TERMS  # JPY symbol
        assert "\u20ac" in PRICE_TERMS  # EUR symbol
        assert "CHF" in PRICE_TERMS

    def test_rating_terms_has_ja(self):
        assert "\u8a55\u4fa1" in RATING_TERMS

    def test_department_terms_has_multilingual(self):
        assert "省" in DEPARTMENT_TERMS  # ja
        assert "ministère" in DEPARTMENT_TERMS  # fr
        assert "Ministerium" in DEPARTMENT_TERMS  # de

    def test_option_terms_has_multilingual(self):
        assert "사이즈" in OPTION_TERMS  # ko
        assert "size" in OPTION_TERMS  # en
        assert "サイズ" in OPTION_TERMS  # ja
        assert "taille" in OPTION_TERMS  # fr
        assert "Größe" in OPTION_TERMS  # de

    def test_price_label_terms_has_english(self):
        assert "regular price" in PRICE_LABEL_TERMS
        assert "sale price" in PRICE_LABEL_TERMS
        assert "original price" in PRICE_LABEL_TERMS
        assert "list price" in PRICE_LABEL_TERMS


# ---------------------------------------------------------------------------
# Chinese (zh) locale tests
# ---------------------------------------------------------------------------


class TestZhLocale:
    def test_get_locale_zh(self):
        lc = get_locale("zh")
        assert lc.code == "zh"
        assert lc.label_title == "标题"
        assert lc.default_currency == "CNY"
        assert lc.date_ymd_suffixes == ("年", "月", "日")

    def test_detect_locale_zh_path(self):
        assert detect_locale("https://www.example.com/zh/products") == "zh"

    def test_detect_locale_zh_subdomain(self):
        assert detect_locale("https://zh.example.com/page") == "zh"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.taobao.com/item/123",
            "https://www.jd.com/product/456",
            "https://www.douyin.com/video/789",
        ],
    )
    def test_detect_locale_zh_exact_domain(self, url):
        assert detect_locale(url) == "zh"

    def test_detect_locale_zh_tld(self):
        assert detect_locale("https://store.example.cn/item/1") == "zh"

    def test_detect_locale_zh_comcn(self):
        assert detect_locale("https://store.example.com.cn/item/1") == "zh"

    def test_price_terms_has_zh(self):
        assert "CNY" in PRICE_TERMS
        assert "RMB" in PRICE_TERMS

    def test_rating_terms_has_zh(self):
        assert "评分" in RATING_TERMS

    def test_option_terms_has_zh(self):
        assert "颜色" in OPTION_TERMS
        assert "尺码" in OPTION_TERMS

    def test_shipping_terms_has_zh(self):
        assert "包邮" in SHIPPING_TERMS

    def test_cjk_multiplier_zh(self):
        from pagemap.page_map_builder import _CJK_TOKEN_MULTIPLIERS

        assert _CJK_TOKEN_MULTIPLIERS["zh"] == 1.6


# ---------------------------------------------------------------------------
# Accept-Language for URL
# ---------------------------------------------------------------------------


class TestAcceptLanguageForUrl:
    def test_amazon_com_returns_english(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://www.amazon.com/dp/B08N5WRWNW")
        assert result == "en-US,en;q=0.9"

    def test_coupang_returns_korean(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://www.coupang.com/vp/products/123")
        assert result == "ko-KR,ko;q=0.9,en;q=0.8"

    def test_amazon_co_jp_returns_japanese(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://www.amazon.co.jp/dp/B08N5WRWNW")
        assert result == "ja-JP,ja;q=0.9,en;q=0.8"

    def test_nike_fr_path_returns_french(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://www.nike.com/fr/w/shoes")
        assert result == "fr-FR,fr;q=0.9,en;q=0.8"

    def test_zara_de_subdomain_returns_german(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://www.zara.com/de/")
        assert result == "de-DE,de;q=0.9,en;q=0.8"

    def test_unknown_domain_falls_back_to_english(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://some-random-site.xyz/page")
        assert result == "en-US,en;q=0.9"

    def test_spanish_tld_returns_spanish(self):
        from pagemap.i18n import accept_language_for_url

        result = accept_language_for_url("https://www.example.es/products")
        assert "es" in result
