"""DOM change detection via pre/post structural fingerprinting.

Leaf module with no internal pagemap dependencies.
Captures lightweight DOM fingerprints before/after actions and compares
them to detect significant structural changes (modals, tab switches, etc.)
that don't involve URL navigation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from playwright.async_api import Page

logger = logging.getLogger("pagemap.dom_change_detector")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomFingerprint:
    """Lightweight structural snapshot of the DOM."""

    interactive_counts: dict[str, int]
    total_interactives: int
    has_dialog: bool
    body_child_count: int
    title: str


@dataclass
class DomChangeVerdict:
    """Result of comparing two DOM fingerprints."""

    changed: bool
    reasons: list[str] = field(default_factory=list)
    severity: str = "none"  # "none" | "minor" | "major"


# ---------------------------------------------------------------------------
# JS fingerprint IIFE — single querySelectorAll + JS-side bucketing
# ---------------------------------------------------------------------------

_DOM_FINGERPRINT_JS = """(() => {
  const INTERACTIVE = 'button,[role=button],[role=link],[role=textbox],' +
    '[role=combobox],[role=checkbox],[role=radio],[role=menuitem],' +
    '[role=menuitemcheckbox],[role=menuitemradio],[role=tab],[role=treeitem],' +
    '[role=option],[role=gridcell],[role=switch],[role=slider],' +
    '[role=spinbutton],[role=searchbox],[role=listbox],' +
    'a[href],input:not([type=hidden]),select,textarea,' +
    '[tabindex]:not([tabindex=\\"-1\\"])';
  const els = document.querySelectorAll(INTERACTIVE);
  const counts = {};
  for (const el of els) {
    const key = el.getAttribute('role') || el.tagName.toLowerCase();
    counts[key] = (counts[key] || 0) + 1;
  }
  return {
    interactiveCounts: counts,
    totalInteractives: els.length,
    hasDialog: !!document.querySelector(
      '[role=dialog],[role=alertdialog],dialog[open],[aria-modal="true"]'
    ),
    bodyChildCount: document.body ? document.body.children.length : 0,
    title: document.title || ''
  };
})()"""

# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


async def capture_dom_fingerprint(page: Page) -> DomFingerprint | None:
    """Capture a DOM structural fingerprint. Returns None on any failure."""
    try:
        raw = await page.evaluate(_DOM_FINGERPRINT_JS)
    except Exception:
        logger.debug("DOM fingerprint capture failed", exc_info=True)
        return None
    if not isinstance(raw, dict):
        return None
    return DomFingerprint(
        interactive_counts=raw.get("interactiveCounts", {}),
        total_interactives=raw.get("totalInteractives", 0),
        has_dialog=bool(raw.get("hasDialog", False)),
        body_child_count=raw.get("bodyChildCount", 0),
        title=raw.get("title", ""),
    )


# ---------------------------------------------------------------------------
# Detection (pure function)
# ---------------------------------------------------------------------------

_MAJOR_ABS = 3
_MAJOR_PCT = 0.20


def detect_dom_changes(
    before: DomFingerprint | None,
    after: DomFingerprint | None,
) -> DomChangeVerdict:
    """Compare two fingerprints and return a change verdict.

    None inputs → severity "none" (graceful skip).
    """
    if before is None or after is None:
        return DomChangeVerdict(changed=False, severity="none")

    reasons: list[str] = []

    # --- Major signals ---
    if before.title != after.title:
        reasons.append("title changed")

    if not before.has_dialog and after.has_dialog:
        reasons.append("dialog appeared")

    diff = after.total_interactives - before.total_interactives
    abs_diff = abs(diff)
    if abs_diff > 0:
        base = max(before.total_interactives, 1)
        pct = abs_diff / base
        if abs_diff > _MAJOR_ABS or pct > _MAJOR_PCT:
            direction = "increased" if diff > 0 else "decreased"
            reasons.append(f"interactive elements {direction} by {abs_diff} ({pct:.0%})")

    # Determine if any reason so far is major-level
    has_major = bool(reasons)

    # --- Minor signals (only if no major yet) ---
    minor_reasons: list[str] = []
    if abs_diff > 0 and not has_major:
        minor_reasons.append(f"interactive count changed by {abs_diff}")

    if before.body_child_count != after.body_child_count and abs_diff == 0:
        minor_reasons.append("body child count changed")

    # --- Build verdict ---
    if has_major:
        return DomChangeVerdict(changed=True, reasons=reasons, severity="major")
    if minor_reasons:
        return DomChangeVerdict(changed=True, reasons=minor_reasons, severity="minor")
    return DomChangeVerdict(changed=False, severity="none")
