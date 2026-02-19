"""4-Tier interactive element detection from AX Tree + CDP.

Tier 1: Standard ARIA roles (button, link, textbox, etc.) with explicit names
Tier 2: Implicit HTML roles (anchors, inputs, selects) via AX tree
Tier 3: CDP event listeners (click handlers on divs/spans)
Tier 4: Visual/heuristic detection (reserved, not implemented in Phase 0)

The detector uses Playwright's accessibility.snapshot() as the primary source,
supplemented by CDP DOMDebugger.getEventListeners() for Tier 3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from playwright.async_api import CDPSession, Page

from . import Interactable
from .sanitizer import sanitize_text

logger = logging.getLogger(__name__)

# ── Role → Affordance mapping ──────────────────────────────────────────

AFFORDANCE_MAP: dict[str, str] = {
    # click
    "button": "click",
    "link": "click",
    "menuitem": "click",
    "menuitemcheckbox": "click",
    "menuitemradio": "click",
    "tab": "click",
    "treeitem": "click",
    "option": "click",
    "gridcell": "click",
    "cell": "click",
    "row": "click",
    # type
    "textbox": "type",
    "searchbox": "type",
    "spinbutton": "type",
    "textarea": "type",
    # select
    "combobox": "select",
    "listbox": "select",
    # click — checkbox/switch/radio는 브라우저 click()으로 토글됨
    "checkbox": "click",
    "switch": "click",
    "radio": "click",
    # click — slider/scrollbar도 click 인터랙션 사용 (MVP)
    "slider": "click",
    "scrollbar": "click",
}

# Roles that are interactive (Tier 1/2 detection targets)
INTERACTIVE_ROLES = frozenset(AFFORDANCE_MAP.keys())

# Roles considered landmark/region containers
LANDMARK_ROLES = frozenset(
    {
        "banner",
        "navigation",
        "main",
        "contentinfo",
        "complementary",
        "search",
        "form",
        "region",
    }
)

# Landmark role → region name
REGION_MAP: dict[str, str] = {
    "banner": "header",
    "navigation": "navigation",
    "main": "main",
    "contentinfo": "footer",
    "complementary": "complementary",
    "search": "search",
    "form": "form",
    "region": "main",
}

# Roles to skip (not interactive, not interesting)
SKIP_ROLES = frozenset(
    {
        "none",
        "presentation",
        "generic",
        "paragraph",
        "heading",
        "text",
        "StaticText",
        "img",
        "image",
        "separator",
        "status",
        "alert",
        "log",
        "timer",
        "tooltip",
        "figure",
        "caption",
        "math",
        "definition",
        "term",
        "note",
        "marquee",
        "directory",
        "document",
        "feed",
        "group",
        "toolbar",
        "meter",
        "progressbar",
    }
)

# ── Tier 3 batch JS ──────────────────────────────────────────────────
# Single Runtime.evaluate call replaces ~200 sequential CDP IPC round-trips.
# Requires includeCommandLineAPI=true for getEventListeners() access.

_TIER3_BATCH_JS = """\
(() => {
  const SELECTORS = [
    "div[tabindex]:not([role])",
    "span[tabindex]:not([role])",
    "div[onclick]",
    "span[onclick]",
    "a:not([href])[onclick]"
  ];
  const CLICK_EVENTS = ["click", "mousedown", "pointerdown", "touchstart"];
  const MAX = 200;

  if (typeof getEventListeners !== "function")
    return {error: "no_getEventListeners", elements: []};

  function getUniqueSelector(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + CSS.escape(el.id);
    const TA = ["data-testid", "data-test-id", "data-cy", "data-test"];
    for (const a of TA) {
      const v = el.getAttribute(a);
      if (v) return "[" + a + '="' + CSS.escape(v) + '"]';
    }
    const al = el.getAttribute("aria-label");
    if (al) {
      const s = el.localName + '[aria-label="' + CSS.escape(al) + '"]';
      try { if (document.querySelectorAll(s).length === 1) return s; } catch(e) {}
    }
    const na = el.getAttribute("name");
    if (na) {
      const s = el.localName + '[name="' + CSS.escape(na) + '"]';
      try { if (document.querySelectorAll(s).length === 1) return s; } catch(e) {}
    }
    if (el.localName === "a") {
      const hr = el.getAttribute("href");
      if (hr) {
        const s = 'a[href="' + CSS.escape(hr) + '"]';
        try { if (document.querySelectorAll(s).length === 1) return s; } catch(e) {}
      }
    }
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      let seg = cur.localName;
      if (cur.id) { path.unshift("#" + CSS.escape(cur.id)); break; }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(
          s => s.localName === cur.localName
        );
        if (sibs.length > 1) {
          seg += ":nth-of-type(" + (sibs.indexOf(cur) + 1) + ")";
        }
      }
      path.unshift(seg);
      cur = cur.parentElement;
    }
    return path.join(" > ");
  }

  const seen = new Set();
  const candidates = [];
  for (const sel of SELECTORS) {
    try {
      for (const el of document.querySelectorAll(sel)) {
        if (seen.has(el)) continue;
        seen.add(el);
        candidates.push(el);
        if (candidates.length >= MAX) break;
      }
    } catch (e) {}
    if (candidates.length >= MAX) break;
  }

  const results = [];
  for (const el of candidates) {
    const listeners = getEventListeners(el);
    let hasClick = false;
    for (const evt of CLICK_EVENTS) {
      if (listeners[evt] && listeners[evt].length > 0) { hasClick = true; break; }
    }
    if (!hasClick) continue;

    const name = (
      el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("alt") || ""
    ).trim();
    let textFallback = "";
    if (!name) {
      const t = (el.textContent || "").trim().replace(/\\s+/g, " ");
      if (t.length > 0 && t.length < 80) textFallback = t;
    }

    results.push({
      tag: el.localName || "div",
      role: el.getAttribute("role") || el.localName || "div",
      name: name,
      textFallback: textFallback,
      cssSelector: getUniqueSelector(el)
    });
  }
  return {error: null, elements: results};
})()
"""


_UNIQUE_SELECTOR_JS = """\
function() {
    const el = this;
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + CSS.escape(el.id);
    const TA = ["data-testid", "data-test-id", "data-cy", "data-test"];
    for (const a of TA) {
        const v = el.getAttribute(a);
        if (v) return "[" + a + '="' + CSS.escape(v) + '"]';
    }
    const al = el.getAttribute("aria-label");
    if (al) {
        const sel = el.localName + '[aria-label="' + CSS.escape(al) + '"]';
        try { if (document.querySelectorAll(sel).length === 1) return sel; } catch(e) {}
    }
    const na = el.getAttribute("name");
    if (na) {
        const sel = el.localName + '[name="' + CSS.escape(na) + '"]';
        try { if (document.querySelectorAll(sel).length === 1) return sel; } catch(e) {}
    }
    if (el.localName === "a") {
        const href = el.getAttribute("href");
        if (href) {
            const sel = 'a[href="' + CSS.escape(href) + '"]';
            try { if (document.querySelectorAll(sel).length === 1) return sel; } catch(e) {}
        }
    }
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
        let seg = cur.localName;
        if (cur.id) { path.unshift("#" + CSS.escape(cur.id)); break; }
        const parent = cur.parentElement;
        if (parent) {
            const sibs = Array.from(parent.children).filter(
                s => s.localName === cur.localName
            );
            if (sibs.length > 1) {
                seg += ":nth-of-type(" + (sibs.indexOf(cur) + 1) + ")";
            }
        }
        path.unshift(seg);
        cur = cur.parentElement;
    }
    return path.join(" > ");
}
"""


async def _resolve_css_selectors(
    cdp: CDPSession,
    interactables: list[Interactable],
    backend_id_map: dict[int, int],
) -> None:
    """Resolve CSS selectors for interactables using their backendDOMNodeIds.

    Modifies interactables in-place by setting selector field.
    Individual element failures are silently skipped (best-effort).

    Args:
        cdp: Active CDP session (will NOT be detached by this function)
        interactables: List of Interactable objects to update
        backend_id_map: Mapping of ref -> backendDOMNodeId
    """
    if not backend_id_map:
        return

    ref_to_item = {item.ref: item for item in interactables}

    for ref, backend_node_id in backend_id_map.items():
        item = ref_to_item.get(ref)
        if item is None:
            continue

        try:
            resolve_result = await cdp.send(
                "DOM.resolveNode",
                {"backendNodeId": backend_node_id},
            )
            object_id = resolve_result.get("object", {}).get("objectId")
            if not object_id:
                continue

            fn_result = await cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": _UNIQUE_SELECTOR_JS,
                    "returnByValue": True,
                },
            )
            selector = fn_result.get("result", {}).get("value", "")
            if selector:
                item.selector = selector

        except Exception:
            logger.debug(
                "CSS selector resolution failed for ref=%d backendNodeId=%d",
                ref,
                backend_node_id,
            )
            continue


@dataclass
class _AXNode:
    """Internal representation of an AX tree node during traversal."""

    role: str
    name: str
    value: str
    focused: bool
    children: list[_AXNode]
    # Region assigned from nearest landmark ancestor
    region: str = "unknown"


def _classify_tier(role: str, name: str) -> int:
    """Classify an interactive element into Tier 1 or 2.

    Tier 1: Elements with explicit accessibility names (well-labeled)
    Tier 2: Elements with roles but empty/generic names
    """
    if name and name.strip():
        return 1
    return 2


def _extract_options(node: dict) -> list[str]:
    """Extract option labels from a combobox/listbox/select AX node."""
    options = []
    for child in node.get("children", []):
        child_role = child.get("role", "").lower()
        if child_role in ("option", "menuitem", "listitem"):
            child_name = child.get("name", "").strip()
            if child_name:
                options.append(sanitize_text(child_name, max_len=150))
        # Recurse into groups
        if child_role in ("group", "listbox"):
            options.extend(_extract_options(child))
    return options


def _walk_ax_tree(
    node: dict,
    results: list[Interactable],
    ref_counter: list[int],
    current_region: str = "unknown",
    seen_names: set[str] | None = None,
    backend_id_map: dict[int, int] | None = None,
) -> None:
    """Recursively walk AX tree and extract interactive elements.

    Args:
        node: AX tree node dict from Playwright
        results: accumulator for found interactables
        ref_counter: mutable counter [current_ref] for sequential numbering
        current_region: inherited region from landmark ancestors
        seen_names: deduplication set of (role, name) pairs
        backend_id_map: optional output map of ref -> backendDOMNodeId
    """
    if seen_names is None:
        seen_names = set()

    role = node.get("role", "").lower()
    name = node.get("name", "").strip()
    value = str(node.get("valuetext", node.get("value", ""))).strip()

    # Update region from landmark roles
    if role in LANDMARK_ROLES:
        current_region = REGION_MAP.get(role, current_region)

    # Check if this is an interactive element
    if role in INTERACTIVE_ROLES:
        # Sanitize untrusted web content
        name = sanitize_text(name)
        value = sanitize_text(value)

        # Deduplication: skip if we've seen this exact role+name combo
        dedup_key = f"{role}:{name}"
        if dedup_key not in seen_names or not name:
            if name:  # Only deduplicate named elements
                seen_names.add(dedup_key)

            tier = _classify_tier(role, name)
            affordance = AFFORDANCE_MAP.get(role, "click")

            # Extract options for select-type elements
            options = []
            if affordance == "select" or role in ("combobox", "listbox"):
                options = _extract_options(node)

            ref_counter[0] += 1
            results.append(
                Interactable(
                    ref=ref_counter[0],
                    role=role,
                    name=name,
                    affordance=affordance,
                    region=current_region,
                    tier=tier,
                    value=value,
                    options=options,
                )
            )

            # Track backendDOMNodeId for CSS selector resolution
            if backend_id_map is not None:
                backend_dom_id = node.get("backendDOMNodeId")
                if backend_dom_id is not None:
                    backend_id_map[ref_counter[0]] = backend_dom_id

    # Recurse into children
    for child in node.get("children", []):
        _walk_ax_tree(child, results, ref_counter, current_region, seen_names, backend_id_map)


async def detect_interactables_ax(
    page: Page,
    interesting_only: bool = False,
) -> list[Interactable]:
    """Detect interactive elements from AX tree (Tier 1-2).

    Uses CDP Accessibility.getFullAXTree since Playwright's accessibility
    API was removed in v1.58+.

    Args:
        page: Playwright page object
        interesting_only: unused, kept for API compat

    Returns:
        List of Interactable elements with sequential ref numbers
    """
    from .browser_session import _cdp_ax_nodes_to_tree

    cdp = await page.context.new_cdp_session(page)
    try:
        result = await cdp.send("Accessibility.getFullAXTree")
        nodes = result.get("nodes", [])
        snapshot = _cdp_ax_nodes_to_tree(nodes) if nodes else None

        if not snapshot:
            logger.warning("Empty AX tree snapshot")
            return []

        results: list[Interactable] = []
        ref_counter = [0]
        backend_id_map: dict[int, int] = {}
        _walk_ax_tree(snapshot, results, ref_counter, backend_id_map=backend_id_map)

        # Resolve CSS selectors while CDP session is still alive
        if backend_id_map:
            await _resolve_css_selectors(cdp, results, backend_id_map)

        logger.info(
            "Tier 1-2: %d interactables (%d Tier 1, %d Tier 2, %d with selector)",
            len(results),
            sum(1 for r in results if r.tier == 1),
            sum(1 for r in results if r.tier == 2),
            sum(1 for r in results if r.selector),
        )
        return results
    finally:
        await cdp.detach()


def _process_tier3_batch(
    raw_elements: list[dict],
    existing_names: set[str],
    ref_start: int,
) -> list[Interactable]:
    """Process batch JS results into Tier 3 Interactables.

    Pure function (no I/O) for easy unit testing.

    Args:
        raw_elements: dicts with keys {tag, role, name, textFallback}
        existing_names: lowercased names from Tier 1-2 for dedup
        ref_start: last ref number assigned (next will be ref_start + 1)

    Returns:
        List of new Tier 3 Interactable objects
    """
    results: list[Interactable] = []
    ref_counter = ref_start
    seen_in_batch: set[str] = set()

    for elem in raw_elements:
        raw_name = (elem.get("name", "") or elem.get("textFallback", "")).strip()
        name = sanitize_text(raw_name)

        if not name:
            continue

        name_lower = name.lower()
        if name_lower in existing_names:
            continue

        if name_lower in seen_in_batch:
            continue
        seen_in_batch.add(name_lower)

        tag = elem.get("tag", "div")
        role = elem.get("role", tag)

        ref_counter += 1
        results.append(
            Interactable(
                ref=ref_counter,
                role=role,
                name=name,
                affordance="click",
                region="unknown",
                tier=3,
                selector=elem.get("cssSelector", ""),
            )
        )

    return results


async def detect_interactables_cdp(
    page: Page,
    existing: list[Interactable] | None = None,
) -> list[Interactable]:
    """Detect additional interactive elements via CDP event listeners (Tier 3).

    Uses a single Runtime.evaluate call with includeCommandLineAPI to access
    getEventListeners(), collapsing ~200 sequential CDP calls into 1.

    Args:
        page: Playwright page object
        existing: already-detected Tier 1-2 elements for deduplication

    Returns:
        List of NEW Tier 3 Interactable elements (not overlapping with existing)
    """
    ref_start = max((e.ref for e in existing), default=0) if existing else 0
    existing_names: set[str] = set()
    if existing:
        existing_names = {e.name.lower() for e in existing if e.name}

    cdp = await page.context.new_cdp_session(page)
    try:
        result = await cdp.send(
            "Runtime.evaluate",
            {
                "expression": _TIER3_BATCH_JS,
                "returnByValue": True,
                "includeCommandLineAPI": True,
            },
        )

        value = result.get("result", {}).get("value")
        if not value or not isinstance(value, dict):
            logger.warning("CDP Tier 3: unexpected result format from Runtime.evaluate")
            return []

        error = value.get("error")
        if error:
            logger.warning("CDP Tier 3: JS-side error: %s", error)
            return []

        raw_elements = value.get("elements", [])

    except Exception as e:
        logger.warning("CDP Tier 3 detection failed: %s", e)
        return []
    finally:
        await cdp.detach()

    results = _process_tier3_batch(raw_elements, existing_names, ref_start)
    logger.info("Tier 3: %d additional interactables from CDP", len(results))
    return results


async def detect_all(
    page: Page,
    enable_tier3: bool = True,
) -> tuple[list[Interactable], list[str]]:
    """Run full interactive element detection (Tier 1-3).

    Args:
        page: Playwright page object
        enable_tier3: whether to run CDP-based Tier 3 detection

    Returns:
        Tuple of (combined interactables list, warning messages)
    """
    warnings: list[str] = []

    # Tier 1-2 from AX tree (isolated: failure yields empty list + warning)
    try:
        ax_elements = await detect_interactables_ax(page)
    except Exception as e:
        logger.warning("AX tree Tier 1-2 detection failed: %s", e)
        ax_elements = []
        warnings.append(f"AX tree detection failed ({type(e).__name__}): interactive elements may be incomplete")

    # Tier 3 from CDP (already isolated internally)
    cdp_elements = []
    if enable_tier3:
        cdp_elements = await detect_interactables_cdp(page, existing=ax_elements)

    # Combine and renumber
    all_elements = ax_elements + cdp_elements
    for i, el in enumerate(all_elements, 1):
        el.ref = i

    logger.info(
        "Total: %d interactables (Tier 1: %d, Tier 2: %d, Tier 3: %d)",
        len(all_elements),
        sum(1 for e in all_elements if e.tier == 1),
        sum(1 for e in all_elements if e.tier == 2),
        sum(1 for e in all_elements if e.tier == 3),
    )
    return all_elements, warnings
