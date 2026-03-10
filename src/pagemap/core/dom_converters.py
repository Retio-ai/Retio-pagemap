# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Pure data converters for CDP / DOM structures.

Leaf module — no internal pagemap or external browser dependencies.
"""

from __future__ import annotations


def _extract_name_source(name_obj: dict) -> str:
    """Extract the name source type from a CDP AXNode name object.

    Examines ``name_obj["sources"]`` to determine where the accessible name
    originated (e.g. ``aria-label``, ``title``, ``contents``).
    """
    if not isinstance(name_obj, dict):
        return ""
    sources = name_obj.get("sources")
    if not sources or not isinstance(sources, list):
        return ""
    for src in sources:
        src_type = src.get("type", "")
        src_value = src.get("value", {})
        # Only consider sources that actually contributed a value
        has_value = False
        if isinstance(src_value, dict):
            has_value = bool(src_value.get("value"))
        elif src_value:
            has_value = True
        if not has_value:
            continue
        if src_type == "attribute":
            return src.get("attribute", "")
        if src_type == "contents":
            return "contents"
        if src_type == "relatedElement":
            return "labelledby"
    return ""


def _cdp_ax_nodes_to_tree(nodes: list[dict]) -> dict | None:
    """Convert CDP Accessibility.getFullAXTree flat node list to a nested tree.

    Matches the format of the old Playwright page.accessibility.snapshot():
    {"role": "...", "name": "...", "value": "...", "focused": false, "children": [...]}
    """
    if not nodes:
        return None

    node_map: dict[str, dict] = {}
    for n in nodes:
        node_id = n.get("nodeId", "")
        role_obj = n.get("role", {})
        name_obj = n.get("name", {})
        role = role_obj.get("value", "") if isinstance(role_obj, dict) else str(role_obj)
        name = name_obj.get("value", "") if isinstance(name_obj, dict) else str(name_obj)

        # Extract properties
        value = ""
        focused = False
        for prop in n.get("properties", []):
            prop_name = prop.get("name", "")
            prop_val = prop.get("value", {})
            v = prop_val.get("value", "") if isinstance(prop_val, dict) else prop_val
            if prop_name == "value":
                value = str(v)
            elif prop_name == "focused":
                focused = bool(v)

        name_source = _extract_name_source(n.get("name", {}))

        tree_node = {
            "role": role,
            "name": name,
            "name_source": name_source,
            "value": value,
            "focused": focused,
            "children": [],
            "backendDOMNodeId": n.get("backendDOMNodeId"),
        }
        node_map[node_id] = tree_node

    # Build parent-child relationships
    for n in nodes:
        node_id = n.get("nodeId", "")
        child_ids = n.get("childIds", [])
        parent_node = node_map.get(node_id)
        if parent_node:
            for cid in child_ids:
                child_node = node_map.get(cid)
                if child_node:
                    parent_node["children"].append(child_node)

    # Root is the first node
    root_id = nodes[0].get("nodeId", "")
    return node_map.get(root_id)
