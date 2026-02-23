# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""AOM (Accessibility Object Model) based pre-filtering.

HTML5 semantic tags → implicit ARIA role mapping is prioritized over
explicit role attributes, because Korean sites rarely use ARIA annotations.

Removes low-weight nodes (navigation, ads, banners, popups) from the DOM
before chunk decomposition.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import lxml.html

logger = logging.getLogger(__name__)

# ---- AOM weight thresholds ----
_DEFAULT_THRESHOLD = 0.5  # below this = remove
_FILTER_SIDEBAR_WEIGHT = 0.7  # aside/complementary with form controls
_LINK_DENSITY_HIGH = 0.8  # ratio above this = heavy penalty
_LINK_DENSITY_MODERATE = 0.5  # ratio above this = moderate penalty
_LINK_DENSITY_HIGH_WEIGHT = 0.2  # weight assigned at high density
_LINK_DENSITY_MODERATE_WEIGHT = 0.4  # weight assigned at moderate density
_NOISE_PATTERN_WEIGHT = 0.2  # weight for 2+ noise class/id matches
_NOISE_COUNT_THRESHOLD = 2  # min noise pattern matches to penalize
_CONTENT_NOISE_OVERRIDE_WEIGHT = 0.7  # content + noise coexist
_LINK_DENSITY_MIN_TEXT_LEN = 50  # skip density calc below this

# HTML5 semantic tag → implicit ARIA role → default weight
_SEMANTIC_WEIGHTS: dict[str, tuple[str, float]] = {
    "main": ("main", 1.0),
    "article": ("article", 1.0),
    "section": ("region", 0.8),
    "nav": ("navigation", 0.0),
    "aside": ("complementary", 0.3),
    "header": ("banner", 0.0),  # weight 0.0 only when body-direct child
    "footer": ("contentinfo", 0.0),  # weight 0.0 only when body-direct child
}

# Class/ID noise patterns (English names common on Korean sites)
_NOISE_PATTERNS = [
    re.compile(r"\bad[-_]?\b", re.IGNORECASE),
    re.compile(r"\badvertis", re.IGNORECASE),
    re.compile(r"\bsponsor", re.IGNORECASE),
    re.compile(r"\bbanner\b", re.IGNORECASE),
    re.compile(r"\brecommend", re.IGNORECASE),
    re.compile(r"\brelated\b", re.IGNORECASE),
    re.compile(r"\bsidebar\b", re.IGNORECASE),
    re.compile(r"\bpopup\b", re.IGNORECASE),
    re.compile(r"\bmodal\b", re.IGNORECASE),
    re.compile(r"\bcookie\b", re.IGNORECASE),
    re.compile(r"\btracking\b", re.IGNORECASE),
    re.compile(r"\boverlay\b", re.IGNORECASE),
    re.compile(r"\bpromo", re.IGNORECASE),
    re.compile(r"\bwidget\b", re.IGNORECASE),
    re.compile(r"\btoast\b", re.IGNORECASE),
    re.compile(r"\bsnackbar\b", re.IGNORECASE),
]

# Positive content class/ID patterns (content containers worth keeping)
_CONTENT_PATTERNS = [
    re.compile(r"\barticle\b", re.IGNORECASE),
    re.compile(r"\bcontent\b", re.IGNORECASE),
    re.compile(r"\bentry\b", re.IGNORECASE),
    re.compile(r"\bpost\b", re.IGNORECASE),
    re.compile(r"\bstory\b", re.IGNORECASE),
    re.compile(r"\bproduct\b", re.IGNORECASE),
    re.compile(r"\bitem\b", re.IGNORECASE),
    re.compile(r"\bgoods\b", re.IGNORECASE),
]

# Pre-compiled patterns for inline style checks (Phase 6.3b)
_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_VISIBILITY_HIDDEN_RE = re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE)
_OPACITY_ZERO_RE = re.compile(r"opacity\s*:\s*0(?:\.0+)?(?:\s*[;!]|\s*$)", re.IGNORECASE)
_FONT_SIZE_ZERO_RE = re.compile(r"font-size\s*:\s*0(?:\.0+)?(?:px|em|rem|%)?(?:\s*[;!]|\s*$)", re.IGNORECASE)

# Price pattern for Product schema noise override (Phase 3.3)
_PRICE_IN_NOISE_RE = re.compile(r"(?:₩|원|\$|€|£|¥)\s*[\d,]+|\d{2,3}(?:,\d{3})+", re.IGNORECASE)


@dataclass
class AomFilterStats:
    """Statistics from AOM filtering."""

    total_nodes: int = 0
    removed_nodes: int = 0
    removal_reasons: dict[str, int] = field(default_factory=dict)
    removed_xpaths: set[str] = field(default_factory=set)  # for future xpath-level matching

    def record(self, reason: str) -> None:
        self.removed_nodes += 1
        self.removal_reasons[reason] = self.removal_reasons.get(reason, 0) + 1


def _is_body_direct_child(el: lxml.html.HtmlElement) -> bool:
    """Check if element is a direct child of <body>."""
    parent = el.getparent()
    if parent is None:
        return False
    tag = parent.tag.lower() if isinstance(parent.tag, str) else ""
    return tag == "body"


def _count_noise_matches(el: lxml.html.HtmlElement) -> int:
    """Count how many noise patterns match in class/id attributes."""
    cls = el.get("class", "")
    eid = el.get("id", "")
    text = f"{cls} {eid}"
    if not text.strip():
        return 0
    return sum(1 for p in _NOISE_PATTERNS if p.search(text))


_LINK_DENSITY_TAGS = frozenset(
    {
        "div",
        "li",
        "td",
        "th",
        "p",
        "blockquote",
    }
)  # Note: section/aside omitted — already handled by semantic tag early return


def _count_content_matches(el: lxml.html.HtmlElement) -> int:
    """Count how many content patterns match in class/id attributes."""
    cls = el.get("class", "")
    eid = el.get("id", "")
    text = f"{cls} {eid}"
    if not text.strip():
        return 0
    return sum(1 for p in _CONTENT_PATTERNS if p.search(text))


_FILTER_CONTROL_TAGS = frozenset({"input", "select", "textarea"})


def _has_interactive_descendants(el: lxml.html.HtmlElement) -> bool:
    """Check if element contains visible form controls (input/select/textarea).

    Filter sidebars contain interactive controls; related-products sections
    contain mostly links and product cards.
    """
    for desc in el.iter():
        if not isinstance(desc.tag, str):
            continue
        tag = desc.tag.lower()
        if tag in _FILTER_CONTROL_TAGS:
            if tag == "input" and desc.get("type", "").lower() == "hidden":
                continue
            return True
    return False


def _compute_weight(
    el: lxml.html.HtmlElement,
    schema_name: str | None = None,
) -> tuple[float, str]:
    """Compute AOM weight for an element.

    Returns (weight, reason). Lower weight = more likely to be noise.

    Priority:
      1. Explicit role attribute
      2. HTML5 semantic tag implicit mapping
      3. aria-hidden="true"
      4. Inline style display:none / visibility:hidden
      5. Class/ID noise pattern matching
    """
    tag = el.tag.lower() if isinstance(el.tag, str) else ""

    # 1. Explicit role attribute
    role = el.get("role", "").lower()
    if role:
        if role in ("navigation", "banner", "contentinfo", "complementary"):
            # Schema-conditional exception: gov.kr contact_info in footer
            if role == "contentinfo" and schema_name == "GovernmentPage":
                return 0.6, "footer-gov-exception"
            if role == "complementary" and _has_interactive_descendants(el):
                return _FILTER_SIDEBAR_WEIGHT, "filter-sidebar"
            return 0.0 if role in ("navigation", "banner", "contentinfo") else 0.3, f"role={role}"
        if role in ("main", "article"):
            return 1.0, f"role={role}"
        if role == "region":
            return 0.8, "role=region"

    # 2. HTML5 semantic tag mapping
    if tag in _SEMANTIC_WEIGHTS:
        implicit_role, default_weight = _SEMANTIC_WEIGHTS[tag]

        # header/footer: only 0.0 if body-direct child
        if tag in ("header", "footer"):
            if _is_body_direct_child(el):
                if tag == "footer" and schema_name == "GovernmentPage":
                    return 0.6, "footer-gov-exception"
                return default_weight, f"semantic-{tag}"
            else:
                # Not body-direct: keep (might be article header/footer)
                return 0.8, f"semantic-{tag}-nested"

        # section: only full weight if it has a label
        if tag == "section":
            if el.get("aria-label") or el.get("aria-labelledby"):
                return 0.8, "semantic-section-labeled"
            return 0.6, "semantic-section-unlabeled"

        if tag == "aside":
            if _has_interactive_descendants(el):
                return _FILTER_SIDEBAR_WEIGHT, "filter-sidebar"
            return default_weight, f"semantic-{tag}"

        return default_weight, f"semantic-{tag}"

    # 3. aria-hidden="true"
    if el.get("aria-hidden") == "true":
        return 0.0, "aria-hidden"

    # 4. Inline style checks (hidden content = prompt injection vector)
    style = el.get("style", "")
    if style:
        if _DISPLAY_NONE_RE.search(style):
            return 0.0, "display-none"
        if _VISIBILITY_HIDDEN_RE.search(style):
            return 0.0, "visibility-hidden"
        if _OPACITY_ZERO_RE.search(style):
            return 0.0, "opacity-zero"
        if _FONT_SIZE_ZERO_RE.search(style):
            return 0.0, "font-size-zero"

    # 5. Class/ID noise patterns + content patterns
    noise_count = _count_noise_matches(el)
    content_count = _count_content_matches(el)

    if noise_count >= _NOISE_COUNT_THRESHOLD:
        if content_count > 0:
            return _CONTENT_NOISE_OVERRIDE_WEIGHT, f"content-override-noise({content_count}vs{noise_count})"
        # Product schema: preserve noise-matched elements that contain price data
        if schema_name == "Product":
            text_content = (el.text_content() or "").strip()
            if text_content and _PRICE_IN_NOISE_RE.search(text_content):
                return _CONTENT_NOISE_OVERRIDE_WEIGHT, f"product-price-in-noise({noise_count})"
        return _NOISE_PATTERN_WEIGHT, f"noise-pattern({noise_count})"

    if content_count > 0:
        return 1.0, f"content-pattern({content_count})"

    # 6. Link density penalty (block-level containers only)
    if tag in _LINK_DENSITY_TAGS:
        total_text = (el.text_content() or "").strip()
        total_len = len(total_text)
        if total_len > _LINK_DENSITY_MIN_TEXT_LEN:
            link_text_len = sum(len((a.text_content() or "").strip()) for a in el.iter("a"))
            if link_text_len > 0:
                density = link_text_len / total_len
                if density > _LINK_DENSITY_HIGH:
                    return _LINK_DENSITY_HIGH_WEIGHT, f"link-density-high({density:.2f})"
                if density > _LINK_DENSITY_MODERATE:
                    return _LINK_DENSITY_MODERATE_WEIGHT, f"link-density({density:.2f})"

    # Default: keep
    return 1.0, "default"


def aom_filter(
    doc: lxml.html.HtmlElement,
    schema_name: str | None = None,
    threshold: float = _DEFAULT_THRESHOLD,
) -> AomFilterStats:
    """Apply AOM-based filtering to DOM tree (in-place).

    Removes nodes with weight < threshold along with all their descendants.
    """
    stats = AomFilterStats()

    # Collect elements to remove (can't modify tree during iteration)
    to_remove: list[tuple[lxml.html.HtmlElement, str]] = []

    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        stats.total_nodes += 1

        tag = el.tag.lower()
        # Never remove body/html/main
        if tag in ("body", "html", "main"):
            continue

        weight, reason = _compute_weight(el, schema_name)
        if weight < threshold:
            to_remove.append((el, reason))

    # Remove collected elements (parent-first to avoid double removal)
    for el, reason in to_remove:
        # Skip if already removed as descendant of a previously removed element
        parent = el.getparent()
        if parent is None:
            continue

        try:
            xpath = doc.getroottree().getpath(el)
        except ValueError:
            continue

        # O(depth) ancestor prefix check via set lookup,
        # replacing O(removed_count) linear scan.
        # range(4,...): /html(2), /html/body(3) never removed (line 272 guard).
        parts = xpath.split("/")
        if any("/".join(parts[:i]) in stats.removed_xpaths for i in range(4, len(parts))):
            continue

        parent.remove(el)
        stats.removed_xpaths.add(xpath)
        stats.record(reason)

    logger.debug(
        "AOM filter: %d/%d nodes removed (%s)",
        stats.removed_nodes,
        stats.total_nodes,
        stats.removal_reasons,
    )

    from pagemap.telemetry import emit
    from pagemap.telemetry.events import AOM_FILTER_COMPLETE

    emit(
        AOM_FILTER_COMPLETE,
        {
            "total_nodes": stats.total_nodes,
            "removed_nodes": stats.removed_nodes,
            "removal_reasons": dict(stats.removal_reasons),
        },
    )

    return stats


# Mapping of removed landmark reasons (weight < 0.5) to Interactable.region names.
# Noise-class and link-density removals are intentionally excluded (no region mapping).
_REASON_TO_REGION: dict[str, str] = {
    "semantic-nav": "navigation",
    "role=navigation": "navigation",
    "semantic-header": "header",
    "role=banner": "header",
    "semantic-footer": "footer",
    "role=contentinfo": "footer",
    "semantic-aside": "complementary",
    "role=complementary": "complementary",
}


def derive_pruned_regions(stats: AomFilterStats) -> set[str]:
    """Map AOM removal reasons to interactable region names."""
    regions: set[str] = set()
    for reason in stats.removal_reasons:
        region = _REASON_TO_REGION.get(reason)
        if region:
            regions.add(region)
    return regions
