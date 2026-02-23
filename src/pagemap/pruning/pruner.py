# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Rule-based pruning with 5-domain schema heuristics.

No BasePruner ABC — concrete RuleBasedPruner directly (Phase 0, no premature abstraction).

Algorithm:
  1. META / RSC_DATA chunks → unconditionally kept
  2. Schema field heuristics → keep matching chunks with reason
  3. <main> descendants with aom_weight ≥ 0.5 → keep
  4. keep-if-unsure: HEADING + TEXT_BLOCK(len>20) on pages without <main>
  5. Coupang recommendation filtering: reject repeated product blocks outside first occurrence
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from pagemap.i18n import (
    AVAILABILITY_TERMS,
    BRAND_TERMS,
    CONTACT_TERMS,
    DEPARTMENT_TERMS,
    DISCOUNT_TERMS,
    FEATURE_TERMS,
    PRICE_TERMS,
    PRICING_TERMS,
    RATING_TERMS,
    REPORTER_TERMS,
    REVIEW_COUNT_TERMS,
    SHIPPING_TERMS,
)
from pagemap.pruning import ChunkType, HtmlChunk, PruneReason

logger = logging.getLogger(__name__)

# ---- In-main thresholds ----
_IN_MAIN_TEXT_MIN = 50  # TEXT_BLOCK, TABLE, LIST
_IN_MAIN_MEDIA_MIN = 10  # MEDIA caption/alt
# HEADING, FORM: always keep in main (no threshold)

# ---- No-main fallback thresholds ----
_NO_MAIN_TEXT_MIN = 30  # TEXT_BLOCK
_NO_MAIN_FORM_MIN = 20  # FORM
_NO_MAIN_MEDIA_MIN = 20  # MEDIA

# ---- Schema-specific body text thresholds ----
_NEWS_BODY_MIN = 50  # NewsArticle article_body
_WIKI_SUMMARY_MIN = 100  # WikiArticle summary
_WIKI_SECTION_MIN = 30  # WikiArticle section text
_SAAS_DESC_MIN = 50  # SaaSPage description
_GOV_BODY_MIN = 30  # GovernmentPage body text

# ---- Coupang recommendation filter ----
_COUPANG_PRICE_COUNT_LIMIT = 10  # start filtering after N price blocks

# ---------------------------------------------------------------------------
# Schema field matching heuristics — built from i18n universal terms
# ---------------------------------------------------------------------------


def _terms_pattern(terms: tuple[str, ...]) -> str:
    """Join escaped literal terms into an alternation pattern."""
    return "|".join(re.escape(t) for t in terms)


_PRICE_RE = re.compile(
    r"(?:" + _terms_pattern(PRICE_TERMS) + r"|,\d{3}|\d{1,3}(?:,\d{3})+)",
)
_NUMERIC_RE = re.compile(r"\d+")
_DATE_RE = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
_RATING_RE = re.compile(
    r"(?:" + _terms_pattern(RATING_TERMS) + r"|\d\.\d)",
    re.IGNORECASE,
)
_REVIEW_COUNT_RE = re.compile(
    r"\d+\s*(?:" + _terms_pattern(REVIEW_COUNT_TERMS) + r")",
    re.IGNORECASE,
)
_REPORTER_RE = re.compile(_terms_pattern(REPORTER_TERMS), re.IGNORECASE)
_CONTACT_RE = re.compile(_terms_pattern(CONTACT_TERMS), re.IGNORECASE)
_BRAND_RE = re.compile(_terms_pattern(BRAND_TERMS), re.IGNORECASE)

# DEPARTMENT_RE: special handling for Korean "원" to avoid false positives
# with prices like "189,000원". Lookbehind/lookahead excludes digit context.
_DEPARTMENT_RE = re.compile(
    r"(?:" + "|".join(re.escape(t) for t in DEPARTMENT_TERMS if t != "원") + r"|(?<!\d )(?<![,\d])원(?![,\d]))",
    re.IGNORECASE,
)
_FEATURE_RE = re.compile(_terms_pattern(FEATURE_TERMS), re.IGNORECASE)
_PRICING_RE = re.compile(_terms_pattern(PRICING_TERMS), re.IGNORECASE)

_AVAILABILITY_RE = re.compile(_terms_pattern(AVAILABILITY_TERMS), re.IGNORECASE)
_SHIPPING_RE = re.compile(_terms_pattern(SHIPPING_TERMS), re.IGNORECASE)
_SCARCITY_RE = re.compile(r"(?:only|just|남은|残り|seulement|nur)\s*\d+", re.IGNORECASE)
_MEASUREMENT_RE = re.compile(
    r"\d+\.?\d*\s*(?:cm|mm|\uc778\uce58|inch|inches|kg|g|lb|oz|%|\u2033|\u2032|\u2034|ml|L)",
    re.IGNORECASE,
)
_SIZE_LABEL_RE = re.compile(
    r"\b(?:XS|S|M|L|XL|XXL|2XL|3XL|FREE|총장|가슴[단둘]레|어깨[너폭]비|소매[길장]이"
    r"|허리[단둘]레|hip|waist|chest|shoulder|sleeve|length|inseam)\b",
    re.IGNORECASE,
)
_DISCOUNT_RE = re.compile(
    r"\d+\s*%\s*(?:" + "|".join(re.escape(t) for t in DISCOUNT_TERMS) + r")",
    re.IGNORECASE,
)


def _is_high_value_short_text(text: str) -> bool:
    """Check if short text contains high-value e-commerce signals."""
    return bool(
        _AVAILABILITY_RE.search(text)
        or _SHIPPING_RE.search(text)
        or _SCARCITY_RE.search(text)
        or _DISCOUNT_RE.search(text)
    )


def _is_measurement_data(text: str) -> bool:
    """Check if text contains measurement/size spec data (e.g. size tables)."""
    return bool(_MEASUREMENT_RE.search(text) or _SIZE_LABEL_RE.search(text))


def _has_itemprop(chunk: HtmlChunk, prop: str) -> bool:
    return chunk.attrs.get("itemprop", "") == prop


def _has_og(chunk: HtmlChunk, prop: str) -> bool:
    """Check if META chunk has og:* property."""
    if chunk.chunk_type != ChunkType.META:
        return False
    return prop in chunk.attrs


# Per-schema field matching rules
# Returns list of (field_name, reason) tuples for a chunk


_PRODUCT_CLASS_RE = re.compile(
    r"(?:product|goods|item)[-_]?(?:name|title|info|card|unit|detail|summary)",
    re.IGNORECASE,
)
_PRODUCT_NAME_CLASS_RE = re.compile(
    r"(?:product|goods|item)[-_]?(?:name|title)(?:V\d+)?",
    re.IGNORECASE,
)


def _match_product(chunk: HtmlChunk) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag
    chunk_class = chunk.attrs.get("class", "").lower() if "class" in chunk.attrs else ""

    # name
    if tag == "h1" or _has_itemprop(chunk, "name") or _has_og(chunk, "og:title"):
        matches.append(("name", "h1/itemprop/og:title"))
    if _PRODUCT_NAME_CLASS_RE.search(chunk_class):
        matches.append(("name", "class=product-name"))

    # price
    if _PRICE_RE.search(text) and _NUMERIC_RE.search(text):
        matches.append(("price", "price-pattern"))
    if _has_itemprop(chunk, "price"):
        matches.append(("price", "itemprop=price"))
    if "price" in chunk_class:
        matches.append(("price", "class=price"))

    # product card container (keep entire card block if class matches)
    if _PRODUCT_CLASS_RE.search(chunk_class):
        matches.append(("product_card", "class=product-card"))

    # rating
    if _RATING_RE.search(text) or _has_itemprop(chunk, "ratingValue"):
        matches.append(("rating", "rating-pattern"))

    # review_count
    if _REVIEW_COUNT_RE.search(text) or _has_itemprop(chunk, "reviewCount"):
        matches.append(("review_count", "review-count-pattern"))

    # brand
    if _has_itemprop(chunk, "brand") or _BRAND_RE.search(text):
        matches.append(("brand", "brand-pattern"))

    return matches


def _match_news_article(chunk: HtmlChunk) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # headline (h1 only) / section_heading (h2)
    if tag == "h1" or _has_itemprop(chunk, "headline") or _has_og(chunk, "og:title"):
        if tag != "h2":  # h2 with itemprop="headline" → section_heading only
            matches.append(("headline", "h1/itemprop/og:title"))
    if tag == "h2":
        matches.append(("section_heading", "h2-section"))

    # date
    if tag == "time" or chunk.attrs.get("datetime"):
        matches.append(("date_published", "time-element"))
    if _DATE_RE.search(text):
        matches.append(("date_published", "date-pattern"))
    if chunk.chunk_type == ChunkType.RSC_DATA:
        matches.append(("date_published", "rsc-data"))

    # author
    if _has_itemprop(chunk, "author") or _REPORTER_RE.search(text):
        matches.append(("author", "author-pattern"))

    # body (long text blocks — news articles have substantial paragraphs)
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > _NEWS_BODY_MIN:
        matches.append(("article_body", "long-text-block"))

    # publisher (from META)
    if chunk.chunk_type == ChunkType.META:
        matches.append(("publisher", "meta-chunk"))

    return matches


def _match_wiki_article(chunk: HtmlChunk) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # title
    if tag == "h1" or _has_og(chunk, "og:title"):
        matches.append(("title", "h1/og:title"))

    # summary (first long paragraph)
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > _WIKI_SUMMARY_MIN:
        matches.append(("summary", "long-text-block"))

    # sections (headings + following text)
    if chunk.chunk_type == ChunkType.HEADING:
        matches.append(("sections", "heading"))
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > _WIKI_SECTION_MIN:
        matches.append(("sections", "section-text"))

    return matches


def _match_saas_page(chunk: HtmlChunk) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # product_name
    if tag == "h1" or _has_og(chunk, "og:title") or _has_og(chunk, "og:site_name"):
        matches.append(("name", "h1/og:title"))

    # pricing
    if _PRICING_RE.search(text):
        matches.append(("pricing", "pricing-pattern"))
    if chunk.chunk_type == ChunkType.TABLE and _PRICING_RE.search(text):
        matches.append(("pricing", "pricing-table"))

    # features
    if chunk.chunk_type == ChunkType.LIST and _FEATURE_RE.search(text):
        matches.append(("features", "feature-list"))
    if chunk.chunk_type == ChunkType.HEADING and _FEATURE_RE.search(text):
        matches.append(("features", "feature-heading"))

    # description
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > _SAAS_DESC_MIN:
        matches.append(("description", "long-text"))

    return matches


def _match_government_page(chunk: HtmlChunk) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # title
    if tag == "h1" or _has_og(chunk, "og:title"):
        matches.append(("title", "h1/og:title"))

    # department
    if chunk.chunk_type == ChunkType.META and _has_og(chunk, "og:site_name"):
        matches.append(("department", "og:site_name"))
    if _DEPARTMENT_RE.search(text):
        matches.append(("department", "department-pattern"))

    # contact_info
    if _CONTACT_RE.search(text):
        matches.append(("contact_info", "contact-pattern"))

    # body
    if (
        chunk.chunk_type == ChunkType.TEXT_BLOCK
        and len(text) > _GOV_BODY_MIN
        and (chunk.in_main or "article" in chunk.parent_xpath.lower())
    ):
        matches.append(("description", "body-text-in-main"))

    # date
    if _DATE_RE.search(text) or chunk.attrs.get("datetime"):
        matches.append(("date", "date-pattern"))

    return matches


_SCHEMA_MATCHERS = {
    "Product": _match_product,
    "NewsArticle": _match_news_article,
    "WikiArticle": _match_wiki_article,
    "SaaSPage": _match_saas_page,
    "GovernmentPage": _match_government_page,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _xpath_common_depth(xpath1: str, xpath2: str) -> int:
    """Count shared leading path segments between two xpaths."""
    parts1 = xpath1.strip("/").split("/")
    parts2 = xpath2.strip("/").split("/")
    common = 0
    for a, b in zip(parts1, parts2, strict=False):
        if a == b:
            common += 1
        else:
            break
    return common


_PRICE_SAME_CONTAINER_DEPTH = 3  # /html/body/divX 이상 공유 필요


# ---------------------------------------------------------------------------
# Pruner
# ---------------------------------------------------------------------------


@dataclass
class PruneDecision:
    """Decision for a single chunk."""

    keep: bool
    reason: PruneReason
    reason_detail: str = ""
    matched_fields: list[str] = field(default_factory=list)


def prune_chunks(
    chunks: list[HtmlChunk],
    schema_name: str,
    has_main: bool = False,
) -> list[tuple[HtmlChunk, PruneDecision]]:
    """Apply rule-based pruning to select relevant chunks.

    Returns list of (chunk, decision) pairs for all chunks.
    """
    matcher = _SCHEMA_MATCHERS.get(schema_name)
    if schema_name and schema_name != "Generic" and matcher is None:
        logger.warning("Unknown schema_name=%r, falling back to generic rules", schema_name)
    results: list[tuple[HtmlChunk, PruneDecision]] = []

    # For Coupang: track first product price block to detect recommendation repeats
    first_price_xpath: str | None = None
    price_count = 0

    for chunk in chunks:
        # Rule 1: META / RSC_DATA → always keep
        if chunk.chunk_type in (ChunkType.META, ChunkType.RSC_DATA):
            results.append(
                (
                    chunk,
                    PruneDecision(
                        keep=True,
                        reason=PruneReason.META_ALWAYS,
                    ),
                )
            )
            continue

        # Rule 3: Schema heuristic matching
        matched_fields: list[str] = []
        match_reason = ""
        if matcher:
            field_matches = matcher(chunk)
            if field_matches:
                matched_fields = [f for f, _ in field_matches]
                match_reason = "; ".join(f"{f}:{r}" for f, r in field_matches)

        if matched_fields and (chunk.text or chunk.attrs.get("content")):
            # Rule 6: Coupang recommendation filtering
            if schema_name == "Product" and "price" in matched_fields:
                price_count += 1
                if first_price_xpath is None:
                    first_price_xpath = chunk.xpath
                elif price_count > _COUPANG_PRICE_COUNT_LIMIT and not chunk.in_main:
                    shared = _xpath_common_depth(first_price_xpath, chunk.xpath)
                    if shared < _PRICE_SAME_CONTAINER_DEPTH:
                        # Different container → likely recommendation section
                        results.append(
                            (
                                chunk,
                                PruneDecision(
                                    keep=False,
                                    reason=PruneReason.COUPANG_REC_FILTER,
                                    matched_fields=matched_fields,
                                ),
                            )
                        )
                        continue
                    # Same container → fall through to keep

            results.append(
                (
                    chunk,
                    PruneDecision(
                        keep=True,
                        reason=PruneReason.SCHEMA_MATCH,
                        reason_detail=match_reason,
                        matched_fields=matched_fields,
                    ),
                )
            )
            continue

        # Rule 4: main area priority (selective)
        # Keep headings and substantial text in <main>, skip short/noise chunks
        if has_main and chunk.in_main:
            if chunk.chunk_type == ChunkType.HEADING:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.IN_MAIN_HEADING,
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.TEXT_BLOCK and (
                len(chunk.text) > _IN_MAIN_TEXT_MIN or _is_high_value_short_text(chunk.text)
            ):
                reason = (
                    PruneReason.IN_MAIN_TEXT if len(chunk.text) > _IN_MAIN_TEXT_MIN else PruneReason.IN_MAIN_HV_SHORT
                )
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=reason,
                        ),
                    )
                )
                continue
            if chunk.chunk_type in (ChunkType.TABLE, ChunkType.LIST) and (
                len(chunk.text) > _IN_MAIN_TEXT_MIN
                or _is_high_value_short_text(chunk.text)
                or _is_measurement_data(chunk.text)
            ):
                reason = (
                    PruneReason.IN_MAIN_STRUCTURED
                    if len(chunk.text) > _IN_MAIN_TEXT_MIN
                    else PruneReason.IN_MAIN_HV_SHORT
                )
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=reason,
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.FORM:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.IN_MAIN_FORM,
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.MEDIA and len(chunk.text) > _IN_MAIN_MEDIA_MIN:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.IN_MAIN_MEDIA,
                        ),
                    )
                )
                continue
            # Short text in main — skip (e.g., navigation labels, captions)
            results.append(
                (
                    chunk,
                    PruneDecision(
                        keep=False,
                        reason=PruneReason.IN_MAIN_SHORT,
                    ),
                )
            )
            continue

        # Rule 5: keep-if-unsure for pages without <main>
        if not has_main:
            if chunk.chunk_type == ChunkType.HEADING:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.KEEP_HEADING_NO_MAIN,
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(chunk.text) > _NO_MAIN_TEXT_MIN:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.KEEP_TEXT_NO_MAIN,
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.FORM and len(chunk.text) > _NO_MAIN_FORM_MIN:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.KEEP_FORM_NO_MAIN,
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.MEDIA and len(chunk.text) > _NO_MAIN_MEDIA_MIN:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason=PruneReason.KEEP_MEDIA_NO_MAIN,
                        ),
                    )
                )
                continue

        # Default: don't keep
        results.append(
            (
                chunk,
                PruneDecision(
                    keep=False,
                    reason=PruneReason.NO_MATCH,
                ),
            )
        )

    kept = 0
    reason_kept: Counter[str] = Counter()
    reason_removed: Counter[str] = Counter()
    for _chunk, decision in results:
        if decision.keep:
            kept += 1
            reason_kept[decision.reason] += 1
        else:
            reason_removed[decision.reason] += 1

    total = len(results)
    logger.debug(
        "Pruner: %d/%d chunks kept (schema=%s) | kept_by=%s | removed_by=%s",
        kept,
        total,
        schema_name,
        reason_kept.most_common(),
        reason_removed.most_common(),
    )

    from pagemap.telemetry import emit
    from pagemap.telemetry.events import PRUNE_DECISIONS

    emit(
        PRUNE_DECISIONS,
        {
            "kept": kept,
            "removed": total - kept,
            "schema_name": schema_name,
            "kept_reasons": dict(reason_kept),
            "removed_reasons": dict(reason_removed),
        },
    )

    return results
