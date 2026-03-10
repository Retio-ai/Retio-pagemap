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
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ..config_registry import PruningConfig

from ..i18n import (
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
from . import ChunkType, HtmlChunk, PruneReason

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


def _match_product(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
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


def _match_news_article(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
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
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.news_body_min:
        matches.append(("article_body", "long-text-block"))

    # publisher (from META)
    if chunk.chunk_type == ChunkType.META:
        matches.append(("publisher", "meta-chunk"))

    return matches


def _match_wiki_article(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # title
    if tag == "h1" or _has_og(chunk, "og:title"):
        matches.append(("title", "h1/og:title"))

    # summary (first long paragraph)
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.wiki_summary_min:
        matches.append(("summary", "long-text-block"))

    # sections (headings + following text)
    if chunk.chunk_type == ChunkType.HEADING:
        matches.append(("sections", "heading"))
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.wiki_section_min:
        matches.append(("sections", "section-text"))

    return matches


def _match_saas_page(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
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
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.saas_desc_min:
        matches.append(("description", "long-text"))

    return matches


def _match_government_page(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
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
        and len(text) > cfg.gov_body_min
        and (chunk.in_main or "article" in chunk.parent_xpath.lower())
    ):
        matches.append(("description", "body-text-in-main"))

    # date
    if _DATE_RE.search(text) or chunk.attrs.get("datetime"):
        matches.append(("date", "date-pattern"))

    return matches


# Pruner i18n terms for new schemas
_LOCATION_TERMS = (
    # ko
    "장소",
    "위치",
    "주소",
    # en
    "venue",
    "location",
    "address",
    # ja
    "会場",
    "場所",
    # fr
    "lieu",
    "adresse",
    # de
    "Veranstaltungsort",
    "Ort",
    "Adresse",
)
_OPENING_HOURS_TERMS = (
    # ko
    "영업시간",
    "운영시간",
    # en
    "hours",
    "opening hours",
    "business hours",
    # ja
    "営業時間",
    # fr
    "horaires",
    # de
    "Öffnungszeiten",
)
_ADDRESS_TERMS = (
    # ko
    "주소",
    "위치",
    # en
    "address",
    "location",
    # ja
    "住所",
    "所在地",
    # fr
    "adresse",
    # de
    "Adresse",
    "Standort",
)
_PHONE_RE = re.compile(r"[\+\(]?\d[\d\-\(\)\s]{6,}")
_LOCATION_RE = re.compile("|".join(re.escape(t) for t in _LOCATION_TERMS), re.IGNORECASE)
_OPENING_HOURS_RE = re.compile("|".join(re.escape(t) for t in _OPENING_HOURS_TERMS), re.IGNORECASE)
_ADDRESS_RE = re.compile("|".join(re.escape(t) for t in _ADDRESS_TERMS), re.IGNORECASE)


def _match_faq_page(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # title
    if tag == "h1":
        matches.append(("title", "h1"))

    # question headings
    if tag in ("h2", "h3"):
        matches.append(("question", "h2/h3-heading"))

    # question mark in text → likely a question
    if "?" in text or "\uff1f" in text:  # \uff1f = ？ (fullwidth)
        matches.append(("question", "question-mark"))

    # details/summary → expandable Q&A
    if tag in ("details", "summary"):
        matches.append(("question", "details-summary"))

    # answer text (long text blocks)
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.faq_body_min:
        matches.append(("answer", "long-text-block"))

    return matches


def _match_event(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # name
    if tag == "h1":
        matches.append(("name", "h1"))

    # date — time element or datetime attr
    if tag == "time" or chunk.attrs.get("datetime"):
        matches.append(("date", "time-element"))
    if _DATE_RE.search(text):
        matches.append(("date", "date-pattern"))

    # location
    if _LOCATION_RE.search(text):
        matches.append(("location", "location-keyword"))

    # price
    if _PRICE_RE.search(text) and _NUMERIC_RE.search(text):
        matches.append(("price", "price-pattern"))

    # description
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.event_desc_min:
        matches.append(("description", "long-text-block"))

    return matches


def _match_local_business(chunk: HtmlChunk, cfg: PruningConfig) -> list[tuple[str, str]]:
    matches = []
    text = chunk.text
    tag = chunk.tag

    # name
    if tag == "h1":
        matches.append(("name", "h1"))

    # address
    if _ADDRESS_RE.search(text):
        matches.append(("address", "address-keyword"))

    # telephone
    if _PHONE_RE.search(text):
        matches.append(("telephone", "phone-pattern"))

    # opening hours
    if _OPENING_HOURS_RE.search(text):
        matches.append(("opening_hours", "hours-keyword"))

    # rating
    if _RATING_RE.search(text):
        matches.append(("rating", "rating-pattern"))

    # description
    if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(text) > cfg.local_biz_desc_min:
        matches.append(("description", "long-text-block"))

    return matches


_SCHEMA_MATCHERS = {
    "Product": _match_product,
    "NewsArticle": _match_news_article,
    "WikiArticle": _match_wiki_article,
    "SaaSPage": _match_saas_page,
    "GovernmentPage": _match_government_page,
    "FAQPage": _match_faq_page,
    "Event": _match_event,
    "LocalBusiness": _match_local_business,
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
    score: float = 0.0
    fitness: float | None = None  # A1: [0,1], None=not computed
    tier: str | None = None  # A5: "A"/"B"/"C", None=not classified


# ---------------------------------------------------------------------------
# Score mapping: higher = more important to keep
# ---------------------------------------------------------------------------

_REASON_SCORES: dict[PruneReason, float] = {
    PruneReason.META_ALWAYS: 1.0,
    PruneReason.SCHEMA_MATCH: 0.9,
    PruneReason.IN_MAIN_HEADING: 0.85,
    PruneReason.IN_MAIN_HV_SHORT: 0.75,
    PruneReason.IN_MAIN_TEXT: 0.7,
    PruneReason.IN_MAIN_STRUCTURED: 0.7,
    PruneReason.IN_MAIN_FORM: 0.65,
    PruneReason.IN_MAIN_MEDIA: 0.6,
    PruneReason.KEEP_HEADING_NO_MAIN: 0.6,
    PruneReason.KEEP_TEXT_NO_MAIN: 0.55,
    PruneReason.KEEP_FORM_NO_MAIN: 0.5,
    PruneReason.KEEP_MEDIA_NO_MAIN: 0.45,
    PruneReason.ADJACENT_BOOST: 0.5,
    PruneReason.COUPANG_REC_FILTER: 0.0,
    PruneReason.IN_MAIN_SHORT: 0.0,
    PruneReason.NO_MATCH: 0.0,
}

_ADJACENT_BOOST_RANGE = 2  # ±2 neighbours
_ADJACENT_BOOST_PER_DISTANCE = 0.15
_ADJACENT_BOOST_THRESHOLD = 0.45
_ADJACENT_BOOST_MIN_TEXT = 5


def boost_adjacent_chunks(
    results: list[tuple[HtmlChunk, PruneDecision]],
) -> int:
    """Boost scores of chunks adjacent to SCHEMA_MATCH chunks.

    Only boosts neighbours sharing the same parent_xpath (prevents
    cross-section contamination).  Rejected chunks that reach the
    threshold and have sufficient text are flipped to keep=True.

    Returns the number of flipped chunks (for precision monitoring).
    """
    # Collect indices of SCHEMA_MATCH chunks
    schema_indices: list[int] = []
    for i, (_c, d) in enumerate(results):
        if d.reason == PruneReason.SCHEMA_MATCH:
            schema_indices.append(i)

    if not schema_indices:
        return 0

    # Build boost map: index → max boost
    boost_map: dict[int, float] = {}
    for si in schema_indices:
        src_chunk = results[si][0]
        for dist in range(1, _ADJACENT_BOOST_RANGE + 1):
            boost = _ADJACENT_BOOST_PER_DISTANCE / dist
            for ni in (si - dist, si + dist):
                if 0 <= ni < len(results):
                    neighbour_chunk = results[ni][0]
                    # Only boost same parent_xpath neighbours
                    if neighbour_chunk.parent_xpath and neighbour_chunk.parent_xpath == src_chunk.parent_xpath:
                        boost_map[ni] = max(boost_map.get(ni, 0.0), boost)

    # Apply boosts
    flip_count = 0
    for idx, boost in boost_map.items():
        chunk, decision = results[idx]
        decision.score = min(decision.score + boost, 1.0)
        # Flip rejected chunks that now exceed threshold
        if (
            not decision.keep
            and decision.score >= _ADJACENT_BOOST_THRESHOLD
            and len(chunk.text) >= _ADJACENT_BOOST_MIN_TEXT
        ):
            decision.keep = True
            decision.reason = PruneReason.ADJACENT_BOOST
            decision.reason_detail = f"boost={boost:.2f}"
            flip_count += 1

    return flip_count


_EFFECTIVE_SCORE_BASE: Final[float] = 0.7


def _effective_score(d: PruneDecision) -> float:
    """Fitness-aware effective score. fitness=None -> score unchanged."""
    if d.fitness is None:
        return d.score
    return d.score * (_EFFECTIVE_SCORE_BASE + (1.0 - _EFFECTIVE_SCORE_BASE) * d.fitness)


def apply_budget_selection(
    results: list[tuple[HtmlChunk, PruneDecision]],
    max_tokens: int,
    *,
    score_bias: float = 1.0,
) -> list[tuple[HtmlChunk, PruneDecision]]:
    """Drop lowest-score kept chunks until total tokens <= max_tokens.

    META_ALWAYS chunks are never dropped.  Ties broken by depth (deeper
    nodes dropped first -- shallow nodes carry more structural weight).

    Args:
        score_bias: A2 alpha for budget stage. score^bias amplifies
            score differences when > 1.0, making low-score chunks
            more likely to be dropped first.

    Mutates decisions in-place and returns the same list.
    """
    from ...preprocessing.preprocess import count_tokens as _count_tokens

    # Estimate total kept tokens (tiktoken-accurate, not len/3.5)
    kept = [(i, c, d) for i, (c, d) in enumerate(results) if d.keep]
    total_tok = sum(_count_tokens(c.text) for _, c, _ in kept)

    if total_tok <= max_tokens:
        return results

    # Sort kept chunks by score ASC (lowest first), then depth DESC (deepest first)
    # so we drop the least-important, deepest chunks first
    droppable = [(i, c, d) for i, c, d in kept if d.reason != PruneReason.META_ALWAYS]
    droppable.sort(key=lambda t: (_effective_score(t[2]) ** score_bias, -t[1].depth))

    for _i, c, d in droppable:
        if total_tok <= max_tokens:
            break
        chunk_tok = _count_tokens(c.text)
        d.keep = False
        d.reason_detail = f"budget-drop(score={d.score:.2f})"
        total_tok -= chunk_tok

    return results


def prune_chunks(
    chunks: list[HtmlChunk],
    schema_name: str,
    has_main: bool = False,
    *,
    config: PruningConfig | None = None,
    stage_alpha: float = 1.0,
) -> list[tuple[HtmlChunk, PruneDecision]]:
    """Apply rule-based pruning to select relevant chunks.

    Returns list of (chunk, decision) pairs for all chunks.

    Args:
        config: Optional PruningConfig for CQP-driven threshold overrides.
                 Defaults to module-level constants when ``None``.
        stage_alpha: A2 alpha for rule pruning stage. Scales text-length
            thresholds (> 1.0 = more aggressive pruning).
    """
    from ..config_registry import DEFAULT_PRUNING_CONFIG

    cfg = config or DEFAULT_PRUNING_CONFIG

    # A2: compute effective thresholds (cfg is frozen, so use locals)
    _a = stage_alpha
    eff_in_main_text_min = max(1, int(cfg.in_main_text_min * _a))
    eff_in_main_media_min = max(1, int(cfg.in_main_media_min * _a))
    eff_no_main_text_min = max(1, int(cfg.no_main_text_min * _a))
    eff_no_main_form_min = max(1, int(cfg.no_main_form_min * _a))
    eff_no_main_media_min = max(1, int(cfg.no_main_media_min * _a))

    # A2: build effective cfg for schema matchers (they take PruningConfig)
    if abs(_a - 1.0) > 1e-9:
        from ..config_registry import PruningConfig as _PC

        eff_cfg = _PC(
            in_main_text_min=eff_in_main_text_min,
            in_main_media_min=eff_in_main_media_min,
            no_main_text_min=eff_no_main_text_min,
            no_main_form_min=eff_no_main_form_min,
            no_main_media_min=eff_no_main_media_min,
            news_body_min=max(1, int(cfg.news_body_min * _a)),
            wiki_summary_min=max(1, int(cfg.wiki_summary_min * _a)),
            wiki_section_min=max(1, int(cfg.wiki_section_min * _a)),
            saas_desc_min=max(1, int(cfg.saas_desc_min * _a)),
            gov_body_min=max(1, int(cfg.gov_body_min * _a)),
            faq_body_min=max(1, int(cfg.faq_body_min * _a)),
            event_desc_min=max(1, int(cfg.event_desc_min * _a)),
            local_biz_desc_min=max(1, int(cfg.local_biz_desc_min * _a)),
            coupang_price_count_limit=cfg.coupang_price_count_limit,
            enable_scoring=cfg.enable_scoring,
            enable_adjacent_boost=cfg.enable_adjacent_boost,
            enable_sibling_grouping=cfg.enable_sibling_grouping,
            enable_text_density_signal=cfg.enable_text_density_signal,
            enable_expanded_rescue=cfg.enable_expanded_rescue,
            enable_block_tree_remerge=cfg.enable_block_tree_remerge,
        )
    else:
        eff_cfg = cfg

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
            field_matches = matcher(chunk, eff_cfg)
            if field_matches:
                matched_fields = [f for f, _ in field_matches]
                match_reason = "; ".join(f"{f}:{r}" for f, r in field_matches)

        if matched_fields and (chunk.text or chunk.attrs.get("content")):
            # Rule 6: Coupang recommendation filtering
            if schema_name == "Product" and "price" in matched_fields:
                price_count += 1
                if first_price_xpath is None:
                    first_price_xpath = chunk.xpath
                elif price_count > cfg.coupang_price_count_limit and not chunk.in_main:
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
                len(chunk.text) > eff_in_main_text_min or _is_high_value_short_text(chunk.text)
            ):
                reason = (
                    PruneReason.IN_MAIN_TEXT if len(chunk.text) > eff_in_main_text_min else PruneReason.IN_MAIN_HV_SHORT
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
                len(chunk.text) > eff_in_main_text_min
                or _is_high_value_short_text(chunk.text)
                or _is_measurement_data(chunk.text)
            ):
                reason = (
                    PruneReason.IN_MAIN_STRUCTURED
                    if len(chunk.text) > eff_in_main_text_min
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
            if chunk.chunk_type == ChunkType.MEDIA and len(chunk.text) > eff_in_main_media_min:
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
            if chunk.chunk_type == ChunkType.TEXT_BLOCK and len(chunk.text) > eff_no_main_text_min:
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
            if chunk.chunk_type == ChunkType.FORM and len(chunk.text) > eff_no_main_form_min:
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
            if chunk.chunk_type == ChunkType.MEDIA and len(chunk.text) > eff_no_main_media_min:
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

    # Assign scores to all decisions
    for _c, _d in results:
        _d.score = _REASON_SCORES.get(_d.reason, 0.0)

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

    try:
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
    except Exception:  # nosec B110
        pass

    return results
