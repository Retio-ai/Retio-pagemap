"""Backward-compat shim — import from pagemap.core.ecommerce.e2e_site_config instead."""

from pagemap.core.ecommerce.e2e_site_config import (  # noqa: F401
    SITE_CONFIG_MAP,
    SITE_CONFIGS,
    SiteFlowConfig,
    get_all_site_ids,
    get_site_config,
)

__all__ = ["SITE_CONFIGS", "SITE_CONFIG_MAP", "SiteFlowConfig", "get_all_site_ids", "get_site_config"]
