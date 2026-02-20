# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared normalization functions for evaluation and pruning.

Extracted from evaluate.py to avoid circular imports.
Used by both evaluate.py (F1 scoring) and pruning2/pipeline.py (recall measurement).
"""

from __future__ import annotations

import re


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
    "coupang.com": "KRW",
    "musinsa.com": "KRW",
    "29cm.co.kr": "KRW",
    "ssfshop.com": "KRW",
    "wconcept.co.kr": "KRW",
    "thehandsome.com": "KRW",
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
    # Pacific / Americas
    ".com.au": "AUD",
    ".au": "AUD",
    ".co.nz": "NZD",
    ".ca": "CAD",
    ".com": "USD",
}


def infer_currency(url: str) -> str:
    """Infer ISO 4217 currency code from URL domain."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    # Exact domain match first
    for domain, code in _DOMAIN_CURRENCY.items():
        if not domain.startswith(".") and domain in host:
            return code
    # TLD fallback
    for tld, code in _DOMAIN_CURRENCY.items():
        if tld.startswith(".") and host.endswith(tld):
            return code
    return "KRW"


def format_price(amount: float, currency: str = "KRW") -> str:
    """Format price with currency-specific notation."""
    if currency in ("KRW", "JPY"):
        formatted = f"{int(amount):,}"
        suffix = "원" if currency == "KRW" else "円"
        return f"{formatted}{suffix}"
    elif currency == "USD":
        return f"${amount:,.2f}"
    elif currency == "EUR":
        return f"€{amount:,.2f}"
    elif currency == "GBP":
        return f"£{amount:,.2f}"
    elif currency == "CHF":
        return f"CHF {amount:,.2f}"
    elif currency in ("SEK", "NOK", "DKK"):
        return f"{amount:,.2f} kr"
    elif currency in ("AUD", "CAD", "NZD"):
        return f"${amount:,.2f} {currency}"
    return f"{amount:,.0f}"
