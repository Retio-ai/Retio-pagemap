"""Backward-compat shim — import from pagemap.core.interactive_detector instead."""

from pagemap.core.interactive_detector import (  # noqa: F401
    _CDP_AX_TREE_TIMEOUT,
    _CDP_CSS_BUDGET,
    _CDP_TIER3_TIMEOUT,
    AFFORDANCE_MAP,
    INTERACTIVE_ROLES,
    LANDMARK_ROLES,
    REGION_MAP,
    SKIP_ROLES,
    _cdp_session,
    _classify_tier,
    _extract_options,
    _is_table_noise,
    _process_tier3_batch,
    _walk_ax_tree,
    detect_all,
    detect_interactables_ax,
    detect_interactables_cdp,
)

__all__ = [
    "AFFORDANCE_MAP",
    "INTERACTIVE_ROLES",
    "LANDMARK_ROLES",
    "REGION_MAP",
    "SKIP_ROLES",
    "detect_all",
    "detect_interactables_ax",
    "detect_interactables_cdp",
]
