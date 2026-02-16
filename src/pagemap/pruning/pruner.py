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
from dataclasses import dataclass, field

from pagemap.i18n import (
    BRAND_TERMS,
    CONTACT_TERMS,
    DEPARTMENT_TERMS,
    FEATURE_TERMS,
    PRICE_TERMS,
    PRICING_TERMS,
    RATING_TERMS,
    REPORTER_TERMS,
    REVIEW_COUNT_TERMS,
)
from pagemap.pruning import ChunkType, HtmlChunk

logger = logging.getLogger(__name__)

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
    r"(?:" + _terms_pattern(RATING_TERMS) + r"|\d\.\d)", re.IGNORECASE,
)
_REVIEW_COUNT_RE = re.compile(
    r"\d+\s*(?:" + _terms_pattern(REVIEW_COUNT_TERMS) + r")", re.IGNORECASE,
)
_REPORTER_RE = re.compile(_terms_pattern(REPORTER_TERMS), re.IGNORECASE)
_CONTACT_RE = re.compile(_terms_pattern(CONTACT_TERMS), re.IGNORECASE)
_BRAND_RE = re.compile(_terms_pattern(BRAND_TERMS), re.IGNORECASE)

# DEPARTMENT_RE: special handling for Korean "원" to avoid false positives
# with prices like "189,000원". Lookbehind/lookahead excludes digit context.
_DEPARTMENT_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(t) for t in DEPARTMENT_TERMS if t != "원")
    + r"|(?<![,\d])원(?![,\d]))",
    re.IGNORECASE,
)
_FEATURE_RE = re.compile(_terms_pattern(FEATURE_TERMS), re.IGNORECASE)
_PRICING_RE = re.compile(_terms_pattern(PRICING_TERMS), re.IGNORECASE)


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

    # headline
    if tag in ("h1", "h2") or _has_itemprop(chunk, "headline") or _has_og(chunk, "og:title"):
        matches.append(("headline", "heading/itemprop/og:title"))

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
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > 50:
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
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > 100:
        matches.append(("summary", "long-text-block"))

    # sections (headings + following text)
    if chunk.chunk_type == ChunkType.HEADING:
        matches.append(("sections", "heading"))
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > 30:
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
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > 50:
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
        and len(text) > 30
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
# Pruner
# ---------------------------------------------------------------------------


@dataclass
class PruneDecision:
    """Decision for a single chunk."""

    keep: bool
    reason: str
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
                        reason="meta-always-keep",
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

        if matched_fields:
            # Rule 6: Coupang recommendation filtering
            if schema_name == "Product" and "price" in matched_fields:
                price_count += 1
                if first_price_xpath is None:
                    first_price_xpath = chunk.xpath
                elif price_count > 3 and not chunk.in_main:
                    # Likely recommendation section outside main
                    results.append(
                        (
                            chunk,
                            PruneDecision(
                                keep=False,
                                reason="coupang-recommendation-filter",
                                matched_fields=matched_fields,
                            ),
                        )
                    )
                    continue

            results.append(
                (
                    chunk,
                    PruneDecision(
                        keep=True,
                        reason=f"schema-match: {match_reason}",
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
                            reason="in-main-heading",
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(chunk.text) > 50:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason="in-main-text",
                        ),
                    )
                )
                continue
            if chunk.chunk_type in (ChunkType.TABLE, ChunkType.LIST) and len(chunk.text) > 50:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason="in-main-structured",
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
                        reason="in-main-short",
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
                            reason="keep-heading-no-main",
                        ),
                    )
                )
                continue
            if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(chunk.text) > 30:
                results.append(
                    (
                        chunk,
                        PruneDecision(
                            keep=True,
                            reason="keep-text-no-main",
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
                    reason="no-match",
                ),
            )
        )

    kept = sum(1 for _, d in results if d.keep)
    total = len(results)
    logger.debug("Pruner: %d/%d chunks kept (schema=%s)", kept, total, schema_name)

    return results
