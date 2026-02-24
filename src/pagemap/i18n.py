# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Internationalization: detection keywords + locale-specific rendering.

2-Layer architecture:
  Layer 1 (Detection): universal keyword tuples — all languages merged for
      single-pass regex matching. No locale parameter needed.
  Layer 2 (Rendering): LocaleConfig dataclass — locale-specific labels,
      templates, and formatting for AI agent output.

Supported locales: en (default), ko, ja, fr, de, zh, es, it, pt, nl
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
    # zh
    "CNY",
    "RMB",
    "人民币",
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
    # zh
    "评分",
    "评价",
    "好评",
    # es
    "valoración",
    "estrellas",
    # it
    "valutazione",
    "stelle",
    # pt
    "avaliação",
    "estrelas",
    # nl
    "beoordeling",
    "sterren",
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
    # zh
    "条评论",
    "个评价",
    "条评价",
    # es
    "reseña",
    "reseñas",
    "opinión",
    "opiniones",
    # it
    "recensione",
    "recensioni",
    # pt
    "avaliação",
    "avaliações",
    # nl
    "beoordeling",
    "beoordelingen",
    "recensie",
    "recensies",
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
    # zh
    "记者",
    "编辑",
    # es
    "periodista",
    "redactor",
    # it
    "giornalista",
    "redattore",
    # pt
    "jornalista",
    "redator",
    # nl
    "journalist",
    "redacteur",
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
    # zh
    "电话",
    "地址",
    "邮箱",
    # es
    "teléfono",
    "dirección",
    "correo",
    # it
    "telefono",
    "indirizzo",
    "email",
    # pt
    "telefone",
    "endereço",
    "e-mail",
    # nl
    "telefoon",
    "adres",
    "e-mail",
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
    # zh
    "品牌",
    "厂商",
    # es
    "marca",
    "fabricante",
    # it
    "marca",
    "produttore",
    # pt
    "marca",
    "fabricante",
    # nl
    "merk",
    "fabrikant",
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
    # zh
    "部门",
    "机构",
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
    # zh
    "功能",
    "特点",
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
    # zh
    "价格",
    "费用",
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
    # zh
    "搜索结果",
    "个商品",
    "条宝贝",
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
    # zh
    "热销",
    "新品",
    "推荐",
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
    # zh
    "筛选",
    "排序",
    "分类",
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
    # zh
    "下一页",
    "下一步",
    # es
    "Siguiente",
    "Página siguiente",
    # it
    "Successivo",
    "Pagina successiva",
    # pt
    "Próximo",
    "Próxima página",
    # nl
    "Volgende",
    "Volgende pagina",
)

PREV_BUTTON_TERMS: tuple[str, ...] = (
    # ko
    "이전",
    "이전 페이지",
    # en
    "Previous",
    "previous",
    "Prev",
    "prev",
    # ja
    "前へ",
    "前のページ",
    # fr
    "Précédent",
    "Page précédente",
    # de
    "Zurück",
    "Vorherige Seite",
    # zh
    "上一页",
    "上一步",
    # es
    "Anterior",
    "Página anterior",
    # it
    "Precedente",
    "Pagina precedente",
    # pt
    "Anterior",
    "Página anterior",
    # nl
    "Vorige",
    "Vorige pagina",
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
    # zh
    "加载更多",
    "查看更多",
    # es
    "Ver más",
    "Cargar más",
    # it
    "Carica altro",
    "Mostra altro",
    # pt
    "Ver mais",
    "Carregar mais",
    # nl
    "Meer laden",
    "Meer tonen",
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
    # zh
    "原价",
    "折扣价",
    "促销价",
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
    # zh
    "尺码",
    "颜色",
    "规格",
)

AVAILABILITY_TERMS: tuple[str, ...] = (
    # ko
    "재고",
    "품절",
    "매진",
    "입고",
    "예약",
    # en
    "in stock",
    "out of stock",
    "sold out",
    "available",
    "unavailable",
    "limited",
    "pre-order",
    # ja
    "在庫",
    "品切れ",
    "完売",
    "予約",
    # fr
    "en stock",
    "épuisé",
    "disponible",
    "indisponible",
    # de
    "auf lager",
    "ausverkauft",
    "verfügbar",
    "nicht verfügbar",
    # zh
    "库存",
    "缺货",
    "已售罄",
    "预订",
    "现货",
)

DISCOUNT_TERMS: tuple[str, ...] = (
    # ko
    "할인",
    "세일",
    # en
    "off",
    "sale",
    "discount",
    # ja
    "引き",
    "割引",
    # fr
    "remise",
    "réduction",
    # de
    "rabatt",
    "Preisnachlass",
    # zh
    "折扣",
    "优惠",
    "满减",
)

SHIPPING_TERMS: tuple[str, ...] = (
    # ko
    "무료배송",
    "무료 배송",
    "배송비",
    "당일배송",
    "내일 도착",
    # en
    "free shipping",
    "free delivery",
    "ships free",
    "delivery",
    # ja
    "送料無料",
    "送料",
    "配送",
    # fr
    "livraison gratuite",
    "livraison",
    # de
    "kostenloser versand",
    "versandkostenfrei",
    "lieferung",
    # zh
    "包邮",
    "运费",
    "快递",
    "免运费",
)

LOGIN_TERMS: tuple[str, ...] = (
    # ko
    "로그인",
    "로그 인",
    # en
    "Sign in",
    "Log in",
    "Login",
    # ja
    "ログイン",
    "サインイン",
    # fr
    "Se connecter",
    "Connexion",
    # de
    "Anmelden",
    "Einloggen",
    # zh
    "登录",
    "登入",
)

CHECKOUT_TERMS: tuple[str, ...] = (
    # ko
    "결제",
    "결제하기",
    "주문",
    "주문하기",
    "배송지",
    # en
    "Checkout",
    "Place Order",
    "Place order",
    "Payment",
    # ja
    "チェックアウト",
    "注文する",
    "お支払い",
    # fr
    "Paiement",
    "Commander",
    "Passer commande",
    # de
    "Kasse",
    "Bestellen",
    "Zahlung",
    # zh
    "结账",
    "支付",
    "下单",
    "付款",
)

FAQ_TERMS: tuple[str, ...] = (
    # ko
    "자주 묻는 질문",
    "FAQ",
    "도움말",
    "고객센터",
    # en
    "Frequently Asked",
    "Help Center",
    "Help centre",
    "Support",
    # ja
    "よくある質問",
    "ヘルプセンター",
    # fr
    "Questions fréquentes",
    "Foire aux questions",
    "Centre d'aide",
    # de
    "Häufig gestellte Fragen",
    "Hilfe-Center",
    "Hilfe",
    # zh
    "常见问题",
    "帮助中心",
)

FORM_FIELD_TERMS: tuple[str, ...] = (
    # ko
    "필수",
    "필수 입력",
    "입력해 주세요",
    # en
    "Required",
    "required",
    "Please enter",
    "Please fill",
    # ja
    "必須",
    "入力してください",
    # fr
    "Obligatoire",
    "Veuillez saisir",
    # de
    "Pflichtfeld",
    "Bitte eingeben",
    # zh
    "必填",
    "请输入",
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
    label_original_price: str
    label_discount: str
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
        label_original_price="원가",
        label_discount="할인",
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
        label_original_price="Original price",
        label_discount="Discount",
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
        label_original_price="定価",
        label_discount="割引",
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
        label_original_price="Prix d'origine",
        label_discount="Remise",
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
        label_original_price="Originalpreis",
        label_discount="Rabatt",
        label_pagination="Seitennavigation",
        label_next_available="Weiter verfügbar",
        label_page_suffix="Seiten",
        overflow_template="+{n} weitere",
        review_template="({count} Bewertungen)",
        default_currency="EUR",
        date_ymd_suffixes=(),
    ),
    "zh": LocaleConfig(
        code="zh",
        label_title="标题",
        label_rating="评分",
        label_brand="品牌",
        label_original_price="原价",
        label_discount="折扣",
        label_pagination="分页",
        label_next_available="有下一页",
        label_page_suffix="页",
        overflow_template="还有{n}个",
        review_template="({count}条评价)",
        default_currency="CNY",
        date_ymd_suffixes=("年", "月", "日"),
    ),
    "es": LocaleConfig(
        code="es",
        label_title="Título",
        label_rating="Valoración",
        label_brand="Marca",
        label_original_price="Precio original",
        label_discount="Descuento",
        label_pagination="Paginación",
        label_next_available="Siguiente disponible",
        label_page_suffix="páginas",
        overflow_template="+{n} más",
        review_template="({count} reseñas)",
        default_currency="EUR",
        date_ymd_suffixes=(),
    ),
    "it": LocaleConfig(
        code="it",
        label_title="Titolo",
        label_rating="Valutazione",
        label_brand="Marca",
        label_original_price="Prezzo originale",
        label_discount="Sconto",
        label_pagination="Paginazione",
        label_next_available="Successivo disponibile",
        label_page_suffix="pagine",
        overflow_template="+{n} altri",
        review_template="({count} recensioni)",
        default_currency="EUR",
        date_ymd_suffixes=(),
    ),
    "pt": LocaleConfig(
        code="pt",
        label_title="Título",
        label_rating="Avaliação",
        label_brand="Marca",
        label_original_price="Preço original",
        label_discount="Desconto",
        label_pagination="Paginação",
        label_next_available="Próximo disponível",
        label_page_suffix="páginas",
        overflow_template="+{n} mais",
        review_template="({count} avaliações)",
        default_currency="BRL",
        date_ymd_suffixes=(),
    ),
    "nl": LocaleConfig(
        code="nl",
        label_title="Titel",
        label_rating="Beoordeling",
        label_brand="Merk",
        label_original_price="Oorspronkelijke prijs",
        label_discount="Korting",
        label_pagination="Paginering",
        label_next_available="Volgende beschikbaar",
        label_page_suffix="pagina's",
        overflow_template="+{n} meer",
        review_template="({count} beoordelingen)",
        default_currency="EUR",
        date_ymd_suffixes=(),
    ),
}

DEFAULT_LOCALE = "en"


def get_locale(code: str | None = None) -> LocaleConfig:
    """Return LocaleConfig for *code*; ``None`` falls back to DEFAULT_LOCALE."""
    return _LOCALES.get(code or DEFAULT_LOCALE, _LOCALES[DEFAULT_LOCALE])


# ---------------------------------------------------------------------------
# URL-based locale auto-detection
# ---------------------------------------------------------------------------

# Path segments like /ja/, /fr/, /de/
_PATH_LOCALE_SEGMENTS = {"ja", "fr", "de", "en", "ko", "zh", "es", "it", "pt", "nl"}

# Domain / TLD → locale mapping (checked in order: exact domain, then TLD)
_DOMAIN_LOCALE: dict[str, str] = {
    # Korean exact domains
    "coupang.com": "ko",
    "musinsa.com": "ko",
    "29cm.co.kr": "ko",
    "ssfshop.com": "ko",
    "wconcept.co.kr": "ko",
    "thehandsome.com": "ko",
    # Chinese exact domains (Simplified Chinese — zh-Hans)
    "taobao.com": "zh",
    "tmall.com": "zh",
    "jd.com": "zh",
    "pinduoduo.com": "zh",
    "xiaohongshu.com": "zh",
    "douyin.com": "zh",
    "bilibili.com": "zh",
    "suning.com": "zh",
    # TLD-based
    ".co.kr": "ko",
    ".kr": "ko",
    ".co.jp": "ja",
    ".jp": "ja",
    ".fr": "fr",
    ".de": "de",
    ".co.uk": "en",
    # Southern/Western European TLDs
    ".es": "es",
    ".it": "it",
    ".pt": "pt",
    ".nl": "nl",
    # Chinese TLDs
    ".com.cn": "zh",
    ".cn": "zh",
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


# ---------------------------------------------------------------------------
# Accept-Language header mapping
# ---------------------------------------------------------------------------

_LOCALE_TO_ACCEPT_LANGUAGE: dict[str, str] = {
    "ko": "ko-KR,ko;q=0.9,en;q=0.8",
    "en": "en-US,en;q=0.9",
    "ja": "ja-JP,ja;q=0.9,en;q=0.8",
    "fr": "fr-FR,fr;q=0.9,en;q=0.8",
    "de": "de-DE,de;q=0.9,en;q=0.8",
    "zh": "zh-CN,zh;q=0.9,en;q=0.8",
    "es": "es-ES,es;q=0.9,en;q=0.8",
    "it": "it-IT,it;q=0.9,en;q=0.8",
    "pt": "pt-BR,pt;q=0.9,en;q=0.8",
    "nl": "nl-NL,nl;q=0.9,en;q=0.8",
}


def accept_language_for_url(url: str) -> str:
    """Return Accept-Language header value appropriate for the given URL.

    Uses detect_locale() to determine the site's language, then maps
    to a proper Accept-Language string with quality weights.
    Falls back to English (project default) for unknown locales.
    """
    locale = detect_locale(url)
    return _LOCALE_TO_ACCEPT_LANGUAGE.get(locale, _LOCALE_TO_ACCEPT_LANGUAGE["en"])
