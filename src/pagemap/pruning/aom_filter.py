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


@dataclass
class AomFilterStats:
    """Statistics from AOM filtering."""

    total_nodes: int = 0
    removed_nodes: int = 0
    removal_reasons: dict[str, int] = field(default_factory=dict)

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

        return default_weight, f"semantic-{tag}"

    # 3. aria-hidden="true"
    if el.get("aria-hidden") == "true":
        return 0.0, "aria-hidden"

    # 4. Inline style checks
    style = el.get("style", "")
    if style:
        if re.search(r"display\s*:\s*none", style, re.IGNORECASE):
            return 0.0, "display-none"
        if re.search(r"visibility\s*:\s*hidden", style, re.IGNORECASE):
            return 0.0, "visibility-hidden"

    # 5. Class/ID noise patterns
    noise_count = _count_noise_matches(el)
    if noise_count >= 2:
        return 0.2, f"noise-pattern({noise_count})"

    # Default: keep
    return 1.0, "default"


def aom_filter(
    doc: lxml.html.HtmlElement,
    schema_name: str | None = None,
    threshold: float = 0.5,
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
    removed_xpaths: set[str] = set()
    for el, reason in to_remove:
        # Skip if already removed as descendant of a previously removed element
        parent = el.getparent()
        if parent is None:
            continue

        try:
            xpath = doc.getroottree().getpath(el)
        except ValueError:
            continue

        # Check if any ancestor was already removed
        already_removed = False
        for removed_xpath in removed_xpaths:
            if xpath.startswith(removed_xpath + "/"):
                already_removed = True
                break
        if already_removed:
            continue

        parent.remove(el)
        removed_xpaths.add(xpath)
        stats.record(reason)

    logger.debug(
        "AOM filter: %d/%d nodes removed (%s)",
        stats.removed_nodes,
        stats.total_nodes,
        stats.removal_reasons,
    )
    return stats
