# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared normalization functions for evaluation and pruning.

Extracted from evaluate.py to avoid circular imports.
Used by both evaluate.py (F1 scoring) and pruning2/pipeline.py (recall measurement).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalize_str(s: str) -> str:
    """Normalize string for comparison: strip, collapse whitespace, lower."""
    return re.sub(r"\s+", " ", str(s).strip()).lower()


_CURRENCY_SYMBOLS = ("₩", "$", "¥", "€", "£")
_CURRENCY_SUFFIXES = ("원", "円", "元")


def normalize_numeric(v) -> float | None:
    """Parse a numeric value, stripping commas and currency symbols/suffixes."""
    if v is None:
        return None
    s = str(v)
    for sym in _CURRENCY_SYMBOLS + _CURRENCY_SUFFIXES:
        s = s.replace(sym, "")
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def normalize_date(s: str) -> str | None:
    """Extract date portion from various Korean/ISO formats."""
    if not s:
        return None
    s = str(s).strip()
    # ISO: 2026-02-11 or 2026-02-11T...
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # Korean: 2026년 2월 11일
    m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # Japanese: 2026年2月11日
    m = re.match(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # Dot: 2026.02.11
    m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


# --- Currency inference + formatting ---

_DOMAIN_CURRENCY: dict[str, str] = {
    # Korean exact domains
    "coupang.com": "KRW",
    "musinsa.com": "KRW",
    "29cm.co.kr": "KRW",
    "ssfshop.com": "KRW",
    "wconcept.co.kr": "KRW",
    "thehandsome.com": "KRW",
    "ssg.com": "KRW",
    "gmarket.co.kr": "KRW",
    "auction.co.kr": "KRW",
    "11st.co.kr": "KRW",
    # Chinese exact domains
    "taobao.com": "CNY",
    "tmall.com": "CNY",
    "jd.com": "CNY",
    "pinduoduo.com": "CNY",
    "suning.com": "CNY",
    # Other exact domains
    "amazon.in": "INR",
    "flipkart.com": "INR",
    "myntra.com": "INR",
    "rakuten.co.jp": "JPY",
    "mercadolibre.com.br": "BRL",
    "americanas.com.br": "BRL",
    "magazineluiza.com.br": "BRL",
    "hepsiburada.com": "TRY",
    "trendyol.com": "TRY",
    "lazada.sg": "SGD",
    "shopee.sg": "SGD",
    "zalando.de": "EUR",
    "zalando.fr": "EUR",
    "asos.com": "GBP",
    "farfetch.com": "USD",
    # TLD-based
    ".co.kr": "KRW",
    ".kr": "KRW",
    ".co.jp": "JPY",
    ".jp": "JPY",
    ".co.uk": "GBP",
    ".uk": "GBP",
    # Europe
    ".fr": "EUR",
    ".de": "EUR",
    ".es": "EUR",
    ".it": "EUR",
    ".nl": "EUR",
    # Nordics / Switzerland
    ".se": "SEK",
    ".no": "NOK",
    ".dk": "DKK",
    ".ch": "CHF",
    # Asia
    ".in": "INR",
    ".com.cn": "CNY",
    ".cn": "CNY",
    ".com.tw": "TWD",
    ".tw": "TWD",
    ".co.th": "THB",
    ".th": "THB",
    ".vn": "VND",
    ".com.my": "MYR",
    ".my": "MYR",
    ".sg": "SGD",
    ".ph": "PHP",
    ".co.id": "IDR",
    ".id": "IDR",
    # Americas
    ".com.br": "BRL",
    ".br": "BRL",
    ".com.mx": "MXN",
    ".mx": "MXN",
    ".com.ar": "ARS",
    ".ar": "ARS",
    # Pacific
    ".com.au": "AUD",
    ".au": "AUD",
    ".co.nz": "NZD",
    ".ca": "CAD",
    # Turkey
    ".com.tr": "TRY",
    ".tr": "TRY",
    # Default
    ".com": "USD",
}


def infer_currency(url: str) -> str:
    """Infer ISO 4217 currency code from URL domain."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    # Exact domain match first
    for domain, code in _DOMAIN_CURRENCY.items():
        if not domain.startswith(".") and (host == domain or host.endswith("." + domain)):
            return code
    # TLD fallback
    for tld, code in _DOMAIN_CURRENCY.items():
        if tld.startswith(".") and host.endswith(tld):
            return code
    return "USD"


def format_price(amount: float, currency: str = "KRW") -> str:
    """Format price with currency-specific notation."""
    if currency in ("KRW", "JPY", "VND", "IDR"):
        formatted = f"{int(amount):,}"
        suffix = {"KRW": "원", "JPY": "円", "VND": "₫", "IDR": ""}.get(currency, "")
        return f"{formatted}{suffix}" if suffix else f"{formatted} {currency}"
    if currency == "INR":
        return _format_inr(amount)
    if currency == "USD":
        return f"${amount:,.2f}"
    if currency == "EUR":
        return f"€{amount:,.2f}"
    if currency == "GBP":
        return f"£{amount:,.2f}"
    if currency == "CHF":
        return f"CHF {amount:,.2f}"
    if currency in ("SEK", "NOK", "DKK"):
        return f"{amount:,.2f} kr"
    if currency in ("AUD", "CAD", "NZD"):
        return f"${amount:,.2f} {currency}"
    if currency == "BRL":
        return f"R${amount:,.2f}"
    if currency == "TRY":
        return f"₺{amount:,.2f}"
    if currency == "CNY":
        return f"¥{amount:,.2f}"
    if currency == "TWD":
        return f"NT${int(amount):,}"
    if currency == "THB":
        return f"฿{amount:,.2f}"
    if currency == "MYR":
        return f"RM{amount:,.2f}"
    if currency == "SGD":
        return f"S${amount:,.2f}"
    if currency == "PHP":
        return f"₱{amount:,.2f}"
    if currency == "MXN":
        return f"MX${amount:,.2f}"
    return f"{amount:,.0f}"


def _format_inr(amount: float) -> str:
    """Format amount using Indian numbering (lakh/crore grouping)."""
    negative = amount < 0
    amount = abs(amount)
    s = f"{amount:.2f}"
    integer_part, decimal_part = s.split(".")
    integer_part = integer_part.replace(",", "")
    if len(integer_part) <= 3:
        formatted = f"₹{integer_part}.{decimal_part}"
        return f"-{formatted}" if negative else formatted
    last3 = integer_part[-3:]
    rest = integer_part[:-3]
    groups = []
    while rest:
        groups.append(rest[-2:])
        rest = rest[:-2]
    groups.reverse()
    grouped = ",".join(groups) + "," + last3
    formatted = f"₹{grouped}.{decimal_part}"
    return f"-{formatted}" if negative else formatted


# --- PriceResult dataclasses ---


@dataclass(frozen=True, slots=True)
class PriceResult:
    """A single parsed price."""

    amount: float
    currency: str  # ISO 4217
    confidence: float  # 0.0-1.0
    price_type: str  # "exact"|"from"|"range_low"|"range_high"|"free"|"unavailable"
    raw_text: str


@dataclass(frozen=True, slots=True)
class PriceParseResult:
    """Result of parsing a price string, possibly with range/cross-listing."""

    prices: tuple[PriceResult, ...]
    is_range: bool
    is_cross_listed: bool
    original_text: str


# --- Currency detection ---

# Multi-character symbols (must be checked before single-char '$')
_MULTI_CHAR_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("R$", "BRL"),
    ("S$", "SGD"),
    ("HK$", "HKD"),
    ("A$", "AUD"),
    ("C$", "CAD"),
    ("NZ$", "NZD"),
    ("NT$", "TWD"),
    ("RM", "MYR"),
    ("MX$", "MXN"),
)

_SINGLE_CHAR_SYMBOLS: dict[str, str] = {
    "₩": "KRW",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "₺": "TRY",
    "₫": "VND",
    "₱": "PHP",
    "฿": "THB",
}

_ISO_CODE_MAP: dict[str, str] = {
    "USD": "USD",
    "EUR": "EUR",
    "GBP": "GBP",
    "JPY": "JPY",
    "KRW": "KRW",
    "CNY": "CNY",
    "RMB": "CNY",
    "CHF": "CHF",
    "SEK": "SEK",
    "NOK": "NOK",
    "DKK": "DKK",
    "AUD": "AUD",
    "CAD": "CAD",
    "NZD": "NZD",
    "INR": "INR",
    "BRL": "BRL",
    "TRY": "TRY",
    "TWD": "TWD",
    "THB": "THB",
    "VND": "VND",
    "MYR": "MYR",
    "SGD": "SGD",
    "PHP": "PHP",
    "IDR": "IDR",
    "MXN": "MXN",
    "ARS": "ARS",
    "HKD": "HKD",
    "PLN": "PLN",
    "CZK": "CZK",
    "HUF": "HUF",
    "ZAR": "ZAR",
    "SAR": "SAR",
    "AED": "AED",
}

# Ambiguous symbols resolved by URL hint
_AMBIGUOUS_SYMBOLS: dict[str, dict[str, str]] = {
    "$": {
        ".com.au": "AUD",
        ".au": "AUD",
        ".ca": "CAD",
        ".co.nz": "NZD",
        ".sg": "SGD",
        ".com.mx": "MXN",
        ".mx": "MXN",
        ".com.ar": "ARS",
        ".ar": "ARS",
        "_default": "USD",
    },
    "¥": {
        ".co.jp": "JPY",
        ".jp": "JPY",
        ".com.cn": "CNY",
        ".cn": "CNY",
        "taobao.com": "CNY",
        "tmall.com": "CNY",
        "jd.com": "CNY",
        "_default": "JPY",
    },
    "kr": {
        ".se": "SEK",
        ".no": "NOK",
        ".dk": "DKK",
        "_default": "SEK",
    },
}

_SUFFIX_CURRENCY: dict[str, str] = {
    "원": "KRW",
    "円": "JPY",
    "元": "CNY",
    "人民币": "CNY",
}

_ISO_CODE_RE = re.compile(r"\b(" + "|".join(sorted(_ISO_CODE_MAP.keys(), key=len, reverse=True)) + r")\b")


def detect_currency_from_text(text: str, url_hint: str = "") -> tuple[str, float]:
    """Detect currency from price text, optionally using URL to disambiguate.

    Returns (iso_4217_code, confidence).
    """
    from urllib.parse import urlparse

    host = urlparse(url_hint).hostname or "" if url_hint else ""

    # 1. Multi-character symbols (highest confidence)
    for sym, code in _MULTI_CHAR_SYMBOLS:
        if sym in text:
            return code, 0.95

    # 2. Single-character unique symbols
    for sym, code in _SINGLE_CHAR_SYMBOLS.items():
        if sym in text:
            return code, 0.95

    # 3. Currency suffixes
    for suffix, code in _SUFFIX_CURRENCY.items():
        if suffix in text:
            return code, 0.90

    # 4. ISO code match
    m = _ISO_CODE_RE.search(text)
    if m:
        return _ISO_CODE_MAP[m.group(1)], 0.90

    # 5. Ambiguous symbols — resolve by URL hint
    for sym, mapping in _AMBIGUOUS_SYMBOLS.items():
        if sym in text:
            # Try exact domain match
            for domain, code in mapping.items():
                if (
                    domain != "_default"
                    and not domain.startswith(".")
                    and (host == domain or host.endswith("." + domain))
                ):
                    return code, 0.85
            # Try TLD match
            for tld, code in mapping.items():
                if tld.startswith(".") and host.endswith(tld):
                    return code, 0.80
            return mapping["_default"], 0.60

    # 6. Fallback to URL inference
    if url_hint:
        return infer_currency(url_hint), 0.40

    return "USD", 0.20


# --- Locale-aware numeric parsing ---

# Arabic-Indic digit mapping
_ARABIC_INDIC_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
# Extended Arabic-Indic (Persian/Urdu)
_EXTENDED_ARABIC_INDIC_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _parse_numeric_locale_aware(s: str, currency_hint: str = "") -> float | None:
    """Parse a numeric string with locale-aware decimal/grouping separators.

    Supports:
      - US/UK: 1,234.56
      - DE/FR: 1.234,56 or 1 234,56
      - KR/JP: 1,234 (no decimal)
      - IN: 1,23,456.78 (lakh grouping)
      - Arabic-Indic digits
    """
    if not s:
        return None

    # Normalize unicode
    s = unicodedata.normalize("NFKC", s.strip())

    # Translate Arabic-Indic digits
    s = s.translate(_ARABIC_INDIC_MAP)
    s = s.translate(_EXTENDED_ARABIC_INDIC_MAP)

    # Arabic decimal separator (U+066B) → dot
    s = s.replace("\u066b", ".")

    # Remove non-numeric characters except digits, commas, dots, spaces
    cleaned = re.sub(r"[^\d,.\s]", "", s).strip()
    if not cleaned:
        return None

    # Remove thin/non-breaking spaces used as grouping
    cleaned = re.sub(r"[\s\u00a0\u202f]+", "", cleaned)

    # Determine separator convention
    has_dot = "." in cleaned
    has_comma = "," in cleaned

    if has_dot and has_comma:
        # Both present — last separator is the decimal
        last_dot = cleaned.rfind(".")
        last_comma = cleaned.rfind(",")
        if last_comma > last_dot:
            # European: 1.234,56
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US/UK: 1,234.56
            cleaned = cleaned.replace(",", "")
    elif has_comma and not has_dot:
        # Determine if comma is decimal or grouping
        parts = cleaned.split(",")
        if (
            len(parts) == 2
            and len(parts[1]) <= 2
            and currency_hint
            not in (
                "KRW",
                "JPY",
                "VND",
                "IDR",
            )
        ):
            # Likely European decimal: 1234,56
            cleaned = cleaned.replace(",", ".")
        else:
            # Grouping comma: 1,234 or 1,23,456 (Indian)
            cleaned = cleaned.replace(",", "")
    elif has_dot and not has_comma:
        # Dot could be decimal or grouping
        parts = cleaned.split(".")
        if (
            len(parts) == 2
            and len(parts[1]) <= 2
            and currency_hint
            not in (
                "KRW",
                "JPY",
                "VND",
                "IDR",
            )
        ):
            pass  # US decimal: 1234.56
        elif len(parts) > 2:
            # Multiple dots = grouping (1.234.567)
            cleaned = cleaned.replace(".", "")
        elif len(parts) == 2 and len(parts[1]) == 3:
            # Ambiguous: 1.234 — grouping separator
            cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


# --- normalize_price ---

# Regex to strip currency symbols/codes/suffixes from a price string
_STRIP_CURRENCY_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(sym) for sym, _ in _MULTI_CHAR_SYMBOLS)
    + r"|"
    + "|".join(re.escape(sym) for sym in _SINGLE_CHAR_SYMBOLS)
    + r"|"
    + "|".join(re.escape(suf) for suf in _SUFFIX_CURRENCY)
    + r"|[$¥]"
    + r"|kr\b"
    + r"|\b"  # opening \b for first ISO code in the join below
    + r"\b|\b".join(sorted(_ISO_CODE_MAP.keys(), key=len, reverse=True))
    + r"\b"
    + r")",
    re.IGNORECASE,
)

# RTL marks and directional formatting
_RTL_MARKS_RE = re.compile(r"[\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]")

# Range separator pattern
_RANGE_SEP_RE = re.compile(r"\s*(?:~|–|—|\s-\s|\sto\s|\sbis\s|\sà\s)\s*")


def normalize_price(
    text: str,
    url_hint: str = "",
    currency_hint: str = "",
) -> PriceParseResult:
    """Parse a price string into structured PriceParseResult.

    Handles:
      - Free / unavailable detection
      - From-prices ("from $99", "부터 ₩10,000")
      - Price ranges ("₩10,000 ~ ₩20,000", "$10-$20")
      - Cross-currency listings ("$99.99 (약 ₩130,000)")
      - Locale-aware number formats
      - Arabic-Indic numerals
      - RTL marks
    """
    from ..i18n import (
        FREE_PRICE_TERMS,
        FROM_PRICE_RE,
        UNAVAILABLE_PRICE_TERMS,
    )

    if not text or not text.strip():
        return PriceParseResult(prices=(), is_range=False, is_cross_listed=False, original_text=text or "")

    original = text
    # Strip RTL marks
    text = _RTL_MARKS_RE.sub("", text).strip()
    text_lower = text.lower()

    # --- Non-price detection ---
    for term in FREE_PRICE_TERMS:
        if term.lower() in text_lower:
            return PriceParseResult(
                prices=(
                    PriceResult(
                        amount=0.0, currency=currency_hint or "USD", confidence=0.90, price_type="free", raw_text=text
                    ),
                ),
                is_range=False,
                is_cross_listed=False,
                original_text=original,
            )

    for term in UNAVAILABLE_PRICE_TERMS:
        if term.lower() in text_lower:
            return PriceParseResult(
                prices=(
                    PriceResult(
                        amount=0.0,
                        currency=currency_hint or "USD",
                        confidence=0.85,
                        price_type="unavailable",
                        raw_text=text,
                    ),
                ),
                is_range=False,
                is_cross_listed=False,
                original_text=original,
            )

    # --- Range detection (before from-price, since ~ can be both) ---
    range_match = _RANGE_SEP_RE.search(text)
    if range_match:
        low_str = text[: range_match.start()].strip()
        high_str = text[range_match.end() :].strip()

        if low_str and high_str:
            cur_low, conf_low = detect_currency_from_text(low_str, url_hint)
            cur_high, conf_high = detect_currency_from_text(high_str, url_hint)
            cur = currency_hint or cur_low

            num_low = _STRIP_CURRENCY_RE.sub("", low_str).strip()
            num_high = _STRIP_CURRENCY_RE.sub("", high_str).strip()
            amt_low = _parse_numeric_locale_aware(num_low, cur)
            amt_high = _parse_numeric_locale_aware(num_high, cur_high)

            if amt_low is not None and amt_high is not None:
                return PriceParseResult(
                    prices=(
                        PriceResult(
                            amount=amt_low, currency=cur, confidence=conf_low, price_type="range_low", raw_text=low_str
                        ),
                        PriceResult(
                            amount=amt_high,
                            currency=cur_high if cur_high != cur_low else cur,
                            confidence=conf_high,
                            price_type="range_high",
                            raw_text=high_str,
                        ),
                    ),
                    is_range=True,
                    is_cross_listed=False,
                    original_text=original,
                )

    # --- From-price detection ---
    is_from = False
    from_cleaned = text
    _from_m = FROM_PRICE_RE.search(text_lower)
    if _from_m:
        is_from = True
        from_cleaned = text[_from_m.end() :].strip()
        if not from_cleaned:
            from_cleaned = text[: _from_m.start()].strip()

    work_text = from_cleaned if is_from else text

    # --- Cross-currency detection ---
    # Pattern: "$99.99 (약 ₩130,000)" or "$99.99 (≈ £79.99)"
    cross_parts = re.split(r"\s*(?:\(약?\s*|\(≈?\s*|\(~?\s*)", work_text)
    if len(cross_parts) >= 2:
        cross_parts = [p.rstrip(")").strip() for p in cross_parts if p.strip()]
        if len(cross_parts) >= 2:
            results = []
            for part in cross_parts:
                cur, conf = detect_currency_from_text(part, url_hint)
                if currency_hint and not results:
                    cur = currency_hint
                numeric_str = _STRIP_CURRENCY_RE.sub("", part).strip()
                amt = _parse_numeric_locale_aware(numeric_str, cur)
                if amt is not None:
                    results.append(
                        PriceResult(
                            amount=amt,
                            currency=cur,
                            confidence=conf * 0.9,
                            price_type="from" if is_from else "exact",
                            raw_text=part.strip(),
                        )
                    )
            if len(results) >= 2:
                return PriceParseResult(
                    prices=tuple(results),
                    is_range=False,
                    is_cross_listed=True,
                    original_text=original,
                )

    # --- Single price ---
    cur, conf = detect_currency_from_text(work_text, url_hint)
    if currency_hint:
        cur = currency_hint

    numeric_str = _STRIP_CURRENCY_RE.sub("", work_text).strip()
    amt = _parse_numeric_locale_aware(numeric_str, cur)

    if amt is not None:
        return PriceParseResult(
            prices=(
                PriceResult(
                    amount=amt,
                    currency=cur,
                    confidence=conf,
                    price_type="from" if is_from else "exact",
                    raw_text=work_text.strip(),
                ),
            ),
            is_range=False,
            is_cross_listed=False,
            original_text=original,
        )

    # Failed to parse
    return PriceParseResult(prices=(), is_range=False, is_cross_listed=False, original_text=original)
