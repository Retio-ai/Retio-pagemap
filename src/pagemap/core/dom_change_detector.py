# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""DOM change detection via pre/post structural fingerprinting.

Leaf module with no internal pagemap dependencies.
Captures lightweight DOM fingerprints before/after actions and compares
them to detect significant structural changes (modals, tab switches, etc.)
that don't involve URL navigation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger("pagemap.dom_change_detector")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomLandmarkVector:
    """5-dimensional structural feature vector for DOM landmark fingerprinting."""

    content_ratio: float  # main_region_chars / total_chars (0.0-1.0)
    interaction_density: float  # landmarks_with_interactive / total_landmarks (0.0-1.0)
    structural_symmetry: float  # first-substantial-container sibling symmetry (0.0-1.0)
    nesting_ratio: float  # avg_content_depth / max_depth (0.0-1.0)
    repetition_period: int  # dominant repeating pattern count (0 = none)

    def to_list(self) -> list[float]:
        """Serialize to flat list for ML pipelines / DB storage."""
        return [
            self.content_ratio,
            self.interaction_density,
            self.structural_symmetry,
            self.nesting_ratio,
            float(self.repetition_period),
        ]

    @classmethod
    def from_list(cls, values: list[float]) -> DomLandmarkVector:
        if len(values) != 5:
            raise ValueError(f"Expected 5 values, got {len(values)}")
        return cls(
            content_ratio=values[0],
            interaction_density=values[1],
            structural_symmetry=values[2],
            nesting_ratio=values[3],
            repetition_period=int(values[4]),
        )


@dataclass(frozen=True)
class DomFingerprint:
    """Lightweight structural snapshot of the DOM."""

    interactive_counts: dict[str, int]
    total_interactives: int
    has_dialog: bool
    body_child_count: int
    title: str
    content_hash: int | None = None  # hash of visible text first 2KB
    spa_signals: dict | None = None  # S9: SPA framework detection signals
    landmark_vector: DomLandmarkVector | None = None


@dataclass
class DomChangeVerdict:
    """Result of comparing two DOM fingerprints."""

    changed: bool
    reasons: list[str] = field(default_factory=list)
    severity: str = "none"  # "none" | "minor" | "major" | "content_changed"


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
  // Cache body text once — innerText triggers layout, avoid repeated reads
  const _bodyText = (document.body && document.body.innerText) || '';
  const contentHash = (() => {
    const text = _bodyText.substring(0, 2000);
    let h = 0;
    for (let i = 0; i < text.length; i++) {
      h = ((h << 5) - h + text.charCodeAt(i)) | 0;
    }
    return h;
  })();
  const spaSignals = {
    react: !!(window.__REACT_DEVTOOLS_GLOBAL_HOOK__ ||
              document.querySelector('[data-reactroot]')),
    nextjs: !!(window.__NEXT_DATA__ || document.querySelector('#__next')),
    vue: !!(window.__VUE__ || document.querySelector('[data-v-]')),
    nuxt: !!window.__NUXT__,
    angular: !!document.querySelector('[ng-version]'),
    svelte: !!document.querySelector('[class*="svelte-"]'),
    skeletonCount: document.querySelectorAll(
      '[class*="skeleton"],[class*="shimmer"],[aria-busy="true"]'
    ).length,
    contentLength: _bodyText.trim().length,
  };
  // -- A7 Landmark data --
  const LANDMARK_TAGS_SEL = 'main,article,section,nav,aside,header,footer';
  const landmarkEls = document.querySelectorAll(LANDMARK_TAGS_SEL);
  const totalLandmarks = landmarkEls.length;
  // interaction_density: Set-based ancestor walk (reuse existing els)
  const LANDMARK_TAG_SET = new Set(['MAIN','ARTICLE','SECTION','NAV','ASIDE','HEADER','FOOTER']);
  const landmarksWithInteractive = new Set();
  for (const el of els) {
    let p = el.parentElement;
    while (p && p !== document.body) {
      if (LANDMARK_TAG_SET.has(p.tagName)) {
        landmarksWithInteractive.add(p);
        break;
      }
      p = p.parentElement;
    }
  }
  const interactiveLandmarks = landmarksWithInteractive.size;
  // content_ratio: textContent (no reflow) for main, _bodyText for total
  const mainEl = document.querySelector('main,[role=main]');
  const mainChars = mainEl ? (mainEl.textContent || '').trim().length : 0;
  const totalChars = _bodyText.trim().length || 1;
  // nesting_ratio: sampled depth walk (cap 50 elements)
  const depthEls = document.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li');
  const depthSample = Math.min(depthEls.length, 50);
  const depthStep = Math.max(1, Math.floor(depthEls.length / depthSample));
  let depthSum = 0, maxDepth = 0, depthCount = 0;
  for (let i = 0; i < depthEls.length && depthCount < depthSample; i += depthStep) {
    let d = 0, cur = depthEls[i];
    while (cur.parentElement) { d++; cur = cur.parentElement; }
    depthSum += d; depthCount++;
    if (d > maxDepth) maxDepth = d;
  }
  // structural_symmetry: first container with 4+ children
  let symMatch = 0, symHalf = 0;
  let symContainer = null;
  if (document.body) {
    const bc = document.body.children;
    if (bc.length >= 4) {
      symContainer = document.body;
    } else {
      for (const child of bc) {
        if (child.children && child.children.length >= 4) {
          symContainer = child;
          break;
        }
      }
    }
  }
  if (symContainer) {
    const kids = Array.from(symContainer.children);
    symHalf = Math.floor(kids.length / 2);
    const leftTags = kids.slice(0, symHalf).map(c => c.tagName);
    const rightTags = kids.slice(-symHalf).map(c => c.tagName);
    for (let i = 0; i < symHalf; i++) {
      if (leftTags[i] === rightTags[i]) symMatch++;
    }
  }
  // repetition_period: tag+classes fingerprint
  let repPeriod = 0;
  const repContainers = document.querySelectorAll('ul,ol,section,main,tbody,dl');
  for (const cont of repContainers) {
    const kids = Array.from(cont.children);
    if (kids.length < 3) continue;
    const fps = kids.slice(0, 30).map(k => {
      const cls = (k.getAttribute('class') || '').split(/\\s+/).slice(0,3).sort().join(',');
      return k.tagName + '|' + cls;
    });
    for (let p = 1; p <= 5; p++) {
      let matches = 0;
      for (let i = p; i < fps.length; i++) {
        if (fps[i] === fps[i - p]) matches++;
      }
      if (matches >= fps.length - p - 1) {
        repPeriod = Math.max(repPeriod, Math.floor(kids.length / p));
        break;
      }
    }
  }
  return {
    interactiveCounts: counts,
    totalInteractives: els.length,
    hasDialog: !!document.querySelector(
      '[role=dialog],[role=alertdialog],dialog[open],[aria-modal="true"]'
    ),
    bodyChildCount: document.body ? document.body.children.length : 0,
    title: document.title || '',
    contentHash: contentHash,
    spaSignals: spaSignals,
    landmarkData: {
      totalLandmarks, interactiveLandmarks,
      mainChars, totalChars,
      depthSum, depthCount, maxDepth,
      symMatch, symHalf,
      repPeriod
    }
  };
})()"""

# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def compute_landmark_vector(raw: dict) -> DomLandmarkVector | None:
    """JS raw data -> 5-dimensional landmark vector. None if data missing."""
    ld = raw.get("landmarkData")
    if not isinstance(ld, dict):
        return None

    total_chars = max(ld.get("totalChars", 1), 1)
    content_ratio = min(ld.get("mainChars", 0) / total_chars, 1.0)

    total_lm = ld.get("totalLandmarks", 0)
    interaction_density = ld.get("interactiveLandmarks", 0) / total_lm if total_lm > 0 else 0.0

    sym_half = ld.get("symHalf", 0)
    structural_symmetry = ld.get("symMatch", 0) / sym_half if sym_half > 0 else 0.5

    max_depth = ld.get("maxDepth", 0)
    depth_count = ld.get("depthCount", 0)
    avg_depth = ld.get("depthSum", 0) / depth_count if depth_count > 0 else 0.0
    nesting_ratio = avg_depth / max_depth if max_depth > 0 else 0.0

    def _clamp(v: float) -> float:
        return round(max(0.0, min(1.0, v)), 3)

    return DomLandmarkVector(
        content_ratio=_clamp(content_ratio),
        interaction_density=_clamp(interaction_density),
        structural_symmetry=_clamp(structural_symmetry),
        nesting_ratio=_clamp(nesting_ratio),
        repetition_period=max(int(ld.get("repPeriod", 0)), 0),
    )


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
        content_hash=raw.get("contentHash"),
        spa_signals=raw.get("spaSignals"),
        landmark_vector=compute_landmark_vector(raw),
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

    # --- Content-only change (structure identical, text changed) ---
    if before.content_hash is not None and before.content_hash != after.content_hash:
        return DomChangeVerdict(
            changed=True,
            reasons=["visible text changed"],
            severity="content_changed",
        )

    return DomChangeVerdict(changed=False, severity="none")


def fingerprints_structurally_equal(
    a: DomFingerprint | None,
    b: DomFingerprint | None,
) -> bool:
    """Check if two fingerprints have the same DOM structure (ignoring content_hash).

    Used by cache to decide between Tier A/B (content refresh) vs Tier C (full rebuild).
    """
    if a is None or b is None:
        return False
    return (
        a.interactive_counts == b.interactive_counts
        and a.total_interactives == b.total_interactives
        and a.has_dialog == b.has_dialog
        and a.title == b.title
    )
