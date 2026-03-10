# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Site Hints — Layer 2 per-site fallback rules.

Generalizes the Amazon price fallback pattern from pruned_context_builder.py
into a priority-sorted registry of site-specific extraction rules.

Existing inline Amazon fallback in pruned_context_builder is preserved.
Site hints are applied AFTER engine extraction for idempotency.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── Condition / Action types ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FieldMissing:
    """Condition: the field is None or empty."""

    pass


@dataclass(frozen=True, slots=True)
class Always:
    """Condition: always apply."""

    pass


@dataclass(frozen=True, slots=True)
class RegexExtract:
    """Action: extract value via regex from HTML."""

    pattern: re.Pattern[str]
    group: int | str = 1


@dataclass(frozen=True, slots=True)
class StaticValue:
    """Action: set a static value."""

    value: Any


# ── SiteHint dataclass ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SiteHint:
    """A per-site extraction fallback rule."""

    hint_id: str
    domain: str
    page_types: frozenset[str]
    field: str
    condition: FieldMissing | Always
    action: RegexExtract | StaticValue
    source: str = "manual"  # "manual" | "cqp_auto"
    confidence: float = 0.7
    priority: int = 0  # lower = evaluated first


# ── Hint Registry (priority-sorted list, not dict) ─────────────────

_AMAZON_PRICE_RE = re.compile(
    r'class=(?P<q>["\'])[^"\']*(?:a-price|a-offscreen|price)[^"\']*(?P=q)[^>]*>'
    r"(?:\s*<[^>]+>)*\s*(?P<price>[^<]+)",
    re.IGNORECASE,
)

_AMAZON_CURRENCY_RE = re.compile(
    r'<span[^>]*class=["\'][^"\']*a-price-symbol[^"\']*["\'][^>]*>([^<]+)</span>',
    re.IGNORECASE,
)

_COUPANG_PRICE_RE = re.compile(
    r'class=["\'][^"\']*total-price[^"\']*["\'][^>]*>\s*(?:<[^>]+>)*\s*([\d,]+)',
    re.IGNORECASE,
)

_HINTS: list[SiteHint] = sorted(
    [
        # Amazon: price fallback from a-price/a-offscreen class
        SiteHint(
            hint_id="amazon_price",
            domain="amazon.",
            page_types=frozenset({"product_detail"}),
            field="price_text",
            condition=FieldMissing(),
            action=RegexExtract(pattern=_AMAZON_PRICE_RE, group=2),
            source="manual",
            confidence=0.85,
            priority=0,
        ),
        # Amazon: currency fallback
        SiteHint(
            hint_id="amazon_currency",
            domain="amazon.",
            page_types=frozenset({"product_detail"}),
            field="currency",
            condition=FieldMissing(),
            action=RegexExtract(pattern=_AMAZON_CURRENCY_RE, group=1),
            source="manual",
            confidence=0.80,
            priority=1,
        ),
        # Coupang: price fallback from total-price class
        SiteHint(
            hint_id="coupang_price",
            domain="coupang.com",
            page_types=frozenset({"product_detail"}),
            field="price_text",
            condition=FieldMissing(),
            action=RegexExtract(pattern=_COUPANG_PRICE_RE, group=1),
            source="manual",
            confidence=0.80,
            priority=2,
        ),
        # Coupang: default currency
        SiteHint(
            hint_id="coupang_currency",
            domain="coupang.com",
            page_types=frozenset({"product_detail", "listing", "search_results"}),
            field="currency",
            condition=FieldMissing(),
            action=StaticValue(value="KRW"),
            source="manual",
            confidence=0.95,
            priority=3,
        ),
    ],
    key=lambda h: h.priority,
)


def _domain_matches(url: str, domain_pattern: str) -> bool:
    """Check if URL's host contains the domain pattern."""
    try:
        host = urlparse(url).hostname or ""
        return domain_pattern in host
    except Exception:
        return False


def _check_condition(condition: FieldMissing | Always, value: Any) -> bool:
    """Check if condition is met."""
    if isinstance(condition, Always):
        return True
    if isinstance(condition, FieldMissing):
        return value is None or value == ""
    return False


def _execute_action(
    action: RegexExtract | StaticValue,
    raw_html: str,
    html_lower: str,
) -> Any:
    """Execute action to get replacement value."""
    if isinstance(action, StaticValue):
        return action.value

    if isinstance(action, RegexExtract):
        match = action.pattern.search(raw_html)
        if match:
            try:
                return match.group(action.group).strip()
            except (IndexError, AttributeError):
                return None

    return None


def apply_site_hints(
    *,
    url: str,
    ecom_data: dict[str, Any],
    raw_html: str,
    html_lower: str,
    page_type: str,
) -> tuple[dict[str, Any], list[str]]:
    """Apply site-specific hints to ecommerce data.

    Returns (updated_data, list_of_applied_hint_ids).
    Never raises — returns original data on error.
    """
    applied: list[str] = []

    try:
        for hint in _HINTS:
            if page_type not in hint.page_types:
                continue
            if not _domain_matches(url, hint.domain):
                continue

            current_value = ecom_data.get(hint.field)
            if not _check_condition(hint.condition, current_value):
                continue

            new_value = _execute_action(hint.action, raw_html, html_lower)
            if new_value is not None:
                ecom_data[hint.field] = new_value
                applied.append(hint.hint_id)

                # Emit telemetry
                try:
                    from pagemap.telemetry import emit
                    from pagemap.telemetry.events import SITE_HINT_APPLIED

                    emit(
                        SITE_HINT_APPLIED,
                        {
                            "hint_id": hint.hint_id,
                            "domain": hint.domain,
                            "field": hint.field,
                            "url": url,
                        },
                    )
                except Exception:  # nosec B110
                    pass

    except Exception as e:
        logger.debug("Site hints error: %s", e)

    return ecom_data, applied
