"""Backward-compat shim — import from pagemap.server.url_validation instead."""

from pagemap.server.url_validation import (  # noqa: F401
    _CLOUD_METADATA_HOSTS,
    _CLOUD_METADATA_NETWORKS,
    _LOCAL_NETWORKS,
    _PRIVATE_NETWORKS,
    ALLOWED_URL_SCHEMES,
    BLOCKED_HOSTS,
    DNS_RESOLVE_TIMEOUT_SECONDS,
    _is_cloud_metadata_ip,
    _is_local_ip,
    _normalize_ip,
    _resolve_dns,
    _validate_resolved_ips,
    _validate_url,
    _validate_url_with_dns,
)

__all__ = [
    "ALLOWED_URL_SCHEMES",
    "BLOCKED_HOSTS",
    "DNS_RESOLVE_TIMEOUT_SECONDS",
    "_CLOUD_METADATA_HOSTS",
    "_CLOUD_METADATA_NETWORKS",
    "_LOCAL_NETWORKS",
    "_PRIVATE_NETWORKS",
    "_is_cloud_metadata_ip",
    "_is_local_ip",
    "_normalize_ip",
    "_resolve_dns",
    "_validate_resolved_ips",
    "_validate_url",
    "_validate_url_with_dns",
]
