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

from playwright.async_api import Page

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
) -> None:
    """Recursively walk AX tree and extract interactive elements.

    Args:
        node: AX tree node dict from Playwright
        results: accumulator for found interactables
        ref_counter: mutable counter [current_ref] for sequential numbering
        current_region: inherited region from landmark ancestors
        seen_names: deduplication set of (role, name) pairs
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

    # Recurse into children
    for child in node.get("children", []):
        _walk_ax_tree(child, results, ref_counter, current_region, seen_names)


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
    finally:
        await cdp.detach()

    if not snapshot:
        logger.warning("Empty AX tree snapshot")
        return []

    results: list[Interactable] = []
    ref_counter = [0]
    _walk_ax_tree(snapshot, results, ref_counter)

    logger.info(
        "Tier 1-2: %d interactables (%d Tier 1, %d Tier 2)",
        len(results),
        sum(1 for r in results if r.tier == 1),
        sum(1 for r in results if r.tier == 2),
    )
    return results


async def detect_interactables_cdp(
    page: Page,
    existing: list[Interactable] | None = None,
) -> list[Interactable]:
    """Detect additional interactive elements via CDP event listeners (Tier 3).

    Finds elements with click/pointer event handlers that aren't already
    captured by the AX tree (Tier 1-2).

    Args:
        page: Playwright page object
        existing: already-detected Tier 1-2 elements for deduplication

    Returns:
        List of NEW Tier 3 Interactable elements (not overlapping with existing)
    """
    cdp = await page.context.new_cdp_session(page)
    results: list[Interactable] = []
    ref_start = max((e.ref for e in existing), default=0) if existing else 0
    ref_counter = [ref_start]

    try:
        doc = await cdp.send("DOM.getDocument", {"depth": 0})
        root_id = doc["root"]["nodeId"]

        # Find potential interactive elements not covered by standard ARIA
        # Focus on div/span with tabindex or click handlers
        selectors = [
            "div[tabindex]:not([role])",
            "span[tabindex]:not([role])",
            "div[onclick]",
            "span[onclick]",
            "a:not([href])[onclick]",
        ]

        existing_names = set()
        if existing:
            existing_names = {e.name.lower() for e in existing if e.name}

        for selector in selectors:
            try:
                query = await cdp.send(
                    "DOM.querySelectorAll",
                    {
                        "nodeId": root_id,
                        "selector": selector,
                    },
                )
            except Exception:
                continue

            for node_id in query.get("nodeIds", []):
                if node_id == 0:
                    continue
                try:
                    node = await cdp.send("DOM.describeNode", {"nodeId": node_id})
                    node_info = node["node"]
                    backend_id = node_info["backendNodeId"]

                    # Get event listeners to confirm interactivity
                    obj = await cdp.send(
                        "DOM.resolveNode",
                        {
                            "backendNodeId": backend_id,
                        },
                    )
                    object_id = obj["object"].get("objectId")
                    if not object_id:
                        continue

                    listeners = await cdp.send(
                        "DOMDebugger.getEventListeners",
                        {
                            "objectId": object_id,
                        },
                    )
                    click_events = [
                        evt["type"]
                        for evt in listeners.get("listeners", [])
                        if evt["type"] in ("click", "mousedown", "pointerdown", "touchstart")
                    ]
                    if not click_events:
                        continue

                    # Extract name from attributes
                    attrs = node_info.get("attributes", [])
                    attr_dict = {}
                    for i in range(0, len(attrs) - 1, 2):
                        attr_dict[attrs[i]] = attrs[i + 1]

                    name = sanitize_text(
                        (attr_dict.get("aria-label", "") or attr_dict.get("title", "") or attr_dict.get("alt", "")).strip()
                    )

                    # Skip if already captured in AX tree
                    if name.lower() in existing_names:
                        continue

                    # Get text content as fallback name
                    if not name:
                        try:
                            text_obj = await cdp.send(
                                "DOM.getOuterHTML",
                                {
                                    "backendNodeId": backend_id,
                                },
                            )
                            # Extract visible text (rough)
                            import re

                            html_text = text_obj.get("outerHTML", "")
                            clean = re.sub(r"<[^>]+>", " ", html_text)
                            clean = re.sub(r"\s+", " ", clean).strip()
                            if len(clean) < 80:
                                name = sanitize_text(clean)
                        except Exception:
                            pass

                    if not name:
                        continue

                    tag = node_info.get("localName", "div")
                    role = attr_dict.get("role", tag)

                    ref_counter[0] += 1
                    results.append(
                        Interactable(
                            ref=ref_counter[0],
                            role=role,
                            name=name,
                            affordance="click",
                            region="unknown",
                            tier=3,
                        )
                    )

                except Exception:
                    continue

    except Exception as e:
        logger.warning("CDP Tier 3 detection failed: %s", e)
    finally:
        await cdp.detach()

    logger.info("Tier 3: %d additional interactables from CDP", len(results))
    return results


async def detect_all(
    page: Page,
    enable_tier3: bool = True,
) -> list[Interactable]:
    """Run full interactive element detection (Tier 1-3).

    Args:
        page: Playwright page object
        enable_tier3: whether to run CDP-based Tier 3 detection

    Returns:
        Combined list of all interactables, sequentially numbered
    """
    # Tier 1-2 from AX tree
    ax_elements = await detect_interactables_ax(page)

    # Tier 3 from CDP
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
    return all_elements
