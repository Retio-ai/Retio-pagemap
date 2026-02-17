"""Internationalization: detection keywords + locale-specific rendering.

2-Layer architecture:
  Layer 1 (Detection): universal keyword tuples — all languages merged for
      single-pass regex matching. No locale parameter needed.
  Layer 2 (Rendering): LocaleConfig dataclass — locale-specific labels,
      templates, and formatting for AI agent output.

Supported locales: ko (default), en, ja, fr, de
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Layer 1 — Universal Detection Terms (pure literal strings, no regex meta)
# ---------------------------------------------------------------------------

PRICE_TERMS: tuple[str, ...] = (
    # symbols / codes
    "₩",
    "$",
    "¥",
    "€",
    "£",
    "CHF",
    "SEK",
    "AUD",
    "CAD",
    "NZD",
    "DKK",
    "NOK",
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "KRW",
    "kr",
    "R$",
    # suffixes
    "원",
    "円",
    "元",
)

RATING_TERMS: tuple[str, ...] = (
    # ko
    "★",
    "평점",
    "별점",
    # en
    "stars",
    "rating",
    "rated",
    # ja
    "評価",
    "レビュー",
    # fr
    "étoile",
    # de
    "Bewertung",
    "Sterne",
)

REVIEW_COUNT_TERMS: tuple[str, ...] = (
    # ko
    "개",
    "건",
    "리뷰",
    # en
    "review",
    "reviews",
    # ja
    "レビュー",
    "件",
    # fr
    "avis",
    # de
    "Bewertung",
    "Bewertungen",
    "Rezension",
)

REPORTER_TERMS: tuple[str, ...] = (
    # ko
    "기자",
    "기고",
    "편집",
    "취재",
    # en
    "reporter",
    # ja
    "記者",
    "編集",
    # fr
    "journaliste",
    "rédacteur",
    # de
    "Reporter",
    "Journalist",
    "Redakteur",
)

CONTACT_TERMS: tuple[str, ...] = (
    # ko
    "전화",
    "연락처",
    "주소",
    "팩스",
    "이메일",
    # en
    "tel",
    "address",
    "fax",
    "email",
    # ja
    "電話",
    "住所",
    # fr
    "téléphone",
    "adresse",
    "courriel",
    # de
    "Telefon",
    "Kontakt",
)

BRAND_TERMS: tuple[str, ...] = (
    # ko
    "브랜드",
    "제조사",
    # en
    "brand",
    "manufacturer",
    # ja
    "ブランド",
    "メーカー",
    # fr
    "marque",
    "fabricant",
    # de
    "Marke",
    "Hersteller",
)

DEPARTMENT_TERMS: tuple[str, ...] = (
    # ko
    "기관",
    "부처",
    "청",
    "위원회",
    "처",
    "원",
    # en
    "department",
    "ministry",
    # ja
    "省",
    "庁",
    "委員会",
    # fr
    "ministère",
    "département",
    # de
    "Ministerium",
    "Behörde",
    "Amt",
)

FEATURE_TERMS: tuple[str, ...] = (
    # ko
    "기능",
    "특징",
    # en
    "feature",
    # ja
    "機能",
    "特徴",
    # fr
    "fonctionnalité",
    "caractéristique",
    # de
    "Funktion",
    "Merkmal",
)

PRICING_TERMS: tuple[str, ...] = (
    # ko
    "요금",
    "가격",
    # en
    "price",
    "pricing",
    # symbols
    "₩",
    "$",
    "€",
    # ja
    "価格",
    "料金",
    # fr
    "prix",
    "tarif",
    # de
    "Preis",
    "Preise",
)

SEARCH_RESULT_TERMS: tuple[str, ...] = (
    # ko
    "검색결과",
    "개의 상품",
    "items",
    "건",
    "총 ",
    # en
    "search results",
    "results",
    # ja
    "検索結果",
    "件の商品",
    # fr
    "résultats",
    "produits",
    # de
    "Suchergebnisse",
    "Ergebnisse",
    "Produkte",
)

LISTING_TERMS: tuple[str, ...] = (
    # ko
    "베스트",
    "랭킹",
    "인기",
    "신상품",
    "new arrival",
    "new in",
    # en
    "best",
    "ranking",
    # ja
    "ベスト",
    "ランキング",
    "人気",
    "新着",
    # fr
    "meilleures ventes",
    "nouveautés",
    # de
    "Bestseller",
    "Beliebt",
    "Neuheiten",
)

FILTER_TERMS: tuple[str, ...] = (
    # ko
    "필터",
    "정렬",
    "카테고리",
    # en
    "filter",
    "sort",
    "category",
    # ja
    "フィルター",
    "並び替え",
    "カテゴリー",
    # fr
    "filtre",
    "tri",
    "catégorie",
    # de
    "Filter",
    "Sortieren",
    "Kategorie",
)

NEXT_BUTTON_TERMS: tuple[str, ...] = (
    # ko
    "다음",
    "다음 페이지",
    # en
    "Next",
    "next",
    "Next Page",
    "next page",
    # ja
    "次へ",
    "次のページ",
    # fr
    "Suivant",
    "Page suivante",
    # de
    "Weiter",
    "Nächste Seite",
)

LOAD_MORE_TERMS: tuple[str, ...] = (
    # ko
    "더보기",
    "더 보기",
    # en
    "Load more",
    "Show more",
    "View more",
    # ja
    "もっと見る",
    "さらに表示",
    # fr
    "Voir plus",
    "Charger plus",
    # de
    "Mehr laden",
    "Mehr anzeigen",
)

PRICE_LABEL_TERMS: tuple[str, ...] = (
    # ko
    "정가",
    "할인가",
    "판매가",
    # en
    "regular price",
    "sale price",
    "original price",
    "list price",
    # ja
    "定価",
    "セール価格",
    "通常価格",
    # fr
    "prix",
    "solde",
    # de
    "Originalpreis",
    "Sonderpreis",
)

OPTION_TERMS: tuple[str, ...] = (
    # ko
    "사이즈",
    "컬러",
    "색상",
    "옵션",
    # en
    "size",
    "color",
    "colour",
    "option",
    # ja
    "サイズ",
    "カラー",
    "オプション",
    # fr
    "taille",
    "couleur",
    "option",
    # de
    "Größe",
    "Farbe",
    "Option",
)

# ---------------------------------------------------------------------------
# Layer 2 — LocaleConfig (rendering)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocaleConfig:
    """Locale-specific labels and formatting for AI agent output."""

    code: str
    label_title: str
    label_rating: str
    label_brand: str
    label_pagination: str
    label_next_available: str
    label_page_suffix: str
    overflow_template: str  # "외 {n}건" — use .format(n=...)
    review_template: str  # "({count}개 리뷰)" — use .format(count=...)
    default_currency: str
    date_ymd_suffixes: tuple[str, ...]


_LOCALES: dict[str, LocaleConfig] = {
    "ko": LocaleConfig(
        code="ko",
        label_title="제목",
        label_rating="평점",
        label_brand="브랜드",
        label_pagination="페이지네이션",
        label_next_available="다음 있음",
        label_page_suffix="페이지",
        overflow_template="외 {n}건",
        review_template="({count}개 리뷰)",
        default_currency="KRW",
        date_ymd_suffixes=("년", "월", "일"),
    ),
    "en": LocaleConfig(
        code="en",
        label_title="Title",
        label_rating="Rating",
        label_brand="Brand",
        label_pagination="Pagination",
        label_next_available="Next available",
        label_page_suffix="pages",
        overflow_template="+{n} more",
        review_template="({count} reviews)",
        default_currency="USD",
        date_ymd_suffixes=(),
    ),
    "ja": LocaleConfig(
        code="ja",
        label_title="タイトル",
        label_rating="評価",
        label_brand="ブランド",
        label_pagination="ページネーション",
        label_next_available="次あり",
        label_page_suffix="ページ",
        overflow_template="他{n}件",
        review_template="({count}件のレビュー)",
        default_currency="JPY",
        date_ymd_suffixes=("年", "月", "日"),
    ),
    "fr": LocaleConfig(
        code="fr",
        label_title="Titre",
        label_rating="Note",
        label_brand="Marque",
        label_pagination="Pagination",
        label_next_available="Suivant disponible",
        label_page_suffix="pages",
        overflow_template="+{n} de plus",
        review_template="({count} avis)",
        default_currency="EUR",
        date_ymd_suffixes=(),
    ),
    "de": LocaleConfig(
        code="de",
        label_title="Titel",
        label_rating="Bewertung",
        label_brand="Marke",
        label_pagination="Seitennavigation",
        label_next_available="Weiter verfügbar",
        label_page_suffix="Seiten",
        overflow_template="+{n} weitere",
        review_template="({count} Bewertungen)",
        default_currency="EUR",
        date_ymd_suffixes=(),
    ),
}

DEFAULT_LOCALE = "ko"


def get_locale(code: str | None = None) -> LocaleConfig:
    """Return LocaleConfig for *code*; ``None`` falls back to DEFAULT_LOCALE."""
    return _LOCALES.get(code or DEFAULT_LOCALE, _LOCALES[DEFAULT_LOCALE])


# ---------------------------------------------------------------------------
# URL-based locale auto-detection
# ---------------------------------------------------------------------------

# Path segments like /ja/, /fr/, /de/
_PATH_LOCALE_SEGMENTS = {"ja", "fr", "de", "en", "ko"}

# Domain / TLD → locale mapping (checked in order: exact domain, then TLD)
_DOMAIN_LOCALE: dict[str, str] = {
    # Korean exact domains
    "coupang.com": "ko",
    "musinsa.com": "ko",
    "29cm.co.kr": "ko",
    "ssfshop.com": "ko",
    "wconcept.co.kr": "ko",
    "thehandsome.com": "ko",
    # TLD-based
    ".co.kr": "ko",
    ".kr": "ko",
    ".co.jp": "ja",
    ".jp": "ja",
    ".fr": "fr",
    ".de": "de",
    ".co.uk": "en",
    ".com": "en",
}


def detect_locale(url: str) -> str:
    """Auto-detect locale from URL.

    Priority: path segment > subdomain > exact domain > TLD.
    Returns locale code string (e.g. "ko", "ja", "fr").
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""

    # 1. Path segment: /ja/, /fr/, /de/, /en/, /ko/
    parts = [p for p in path.split("/") if p]
    for part in parts[:2]:  # only check first 2 segments
        if part.lower() in _PATH_LOCALE_SEGMENTS:
            return part.lower()

    # 2. Subdomain: ja.zara.com, fr.nike.com
    sub = host.split(".")[0] if host else ""
    if sub in _PATH_LOCALE_SEGMENTS:
        return sub

    # 3. Exact domain match
    for domain, locale in _DOMAIN_LOCALE.items():
        if not domain.startswith(".") and domain in host:
            return locale

    # 4. TLD fallback
    for tld, locale in _DOMAIN_LOCALE.items():
        if tld.startswith(".") and host.endswith(tld):
            return locale

    return DEFAULT_LOCALE
