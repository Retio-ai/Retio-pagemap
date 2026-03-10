# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""URL validation, SSRF defense, and DNS rebinding protection.

Extracted from server.py — all symbols are re-exported there for backward compatibility.

INVARIANT: NEVER use 'from pagemap.server import _allow_local' — it captures
the value at import time, not a reference. Always use 'import pagemap.server as _srv'
and access '_srv._allow_local' to preserve test patching compatibility.

INVARIANT: _resolve_dns must use '_srv.socket.getaddrinfo' (not module-level socket)
because tests patch 'pagemap.server.socket.getaddrinfo'.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket  # used locally for gaierror; DNS calls go through _srv.socket for test patching
from urllib.parse import urlparse

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

# ── Security constants ────────────────────────────────────────────────

ALLOWED_URL_SCHEMES = {"http", "https"}

# Hostnames that must never be navigated to
BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",  # GCP metadata
        "metadata.goog",  # GCP metadata (alternative)
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "100.100.100.200",  # Alibaba Cloud metadata
        "169.254.170.2",  # AWS ECS task metadata
    }
)

# Private/reserved IP ranges (RFC 1918, loopback, link-local, CGNAT, IPv4-mapped IPv6)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),  # "This" network
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (Carrier-grade NAT)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
]

# Cloud metadata — always blocked regardless of --allow-local
_CLOUD_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",  # GCP metadata (alternative)
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "100.100.100.200",  # Alibaba Cloud metadata
        "169.254.170.2",  # AWS ECS task metadata
        "fd00:ec2::254",  # AWS Nitro IPv6 metadata (CVE-2026-27129)
    }
)
_CLOUD_METADATA_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fd00:ec2::/32"),  # AWS Nitro IPv6 metadata range
]

# Networks unlocked by --allow-local (loopback + RFC 1918 + IPv6 ULA only)
_LOCAL_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # IPv4 loopback
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
]


# ── IP helpers ────────────────────────────────────────────────────────


def _is_cloud_metadata_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return True if IP is in a cloud metadata range (always blocked)."""
    return any(addr in net for net in _CLOUD_METADATA_NETWORKS)


def _is_local_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return True if IP is loopback or RFC 1918 (--allow-local exemption)."""
    return any(addr in net for net in _LOCAL_NETWORKS)


def _normalize_ip(hostname: str) -> str | None:
    """Normalize IP address formats (octal, hex, decimal) to standard form.

    Returns normalized IP string, or None if hostname is not an IP address.
    Handles bypass attempts like 0177.0.0.1 (octal), 0x7f000001 (hex),
    and 2130706433 (decimal).

    Uses pure arithmetic parsing — no DNS queries are performed.
    """
    # Try direct parse first
    try:
        return str(ipaddress.ip_address(hostname))
    except ValueError:
        pass

    # Decimal integer IP (e.g. 2130706433 → 127.0.0.1)
    try:
        num = int(hostname)
        if 0 <= num <= 0xFFFFFFFF:
            return str(ipaddress.ip_address(num))
    except (ValueError, OverflowError):
        pass

    # Hex IP (e.g. 0x7f000001 → 127.0.0.1)
    if hostname.startswith("0x"):
        try:
            num = int(hostname, 16)
            if 0 <= num <= 0xFFFFFFFF:
                return str(ipaddress.ip_address(num))
        except (ValueError, OverflowError):
            pass

    # Octal octets (e.g. 0177.0.0.01) — pure arithmetic, no DNS
    if "." in hostname:
        parts = hostname.split(".")
        if len(parts) == 4:
            has_octal = False
            octets: list[int] = []
            valid = True
            for p in parts:
                if not p:
                    valid = False
                    break
                if len(p) > 1 and p.startswith("0"):
                    # Octal: validate all digits are 0-7
                    if not all(c in "01234567" for c in p):
                        valid = False
                        break
                    has_octal = True
                    octets.append(int(p, 8))
                elif p.isdigit():
                    octets.append(int(p, 10))
                else:
                    valid = False
                    break
            if valid and has_octal and len(octets) == 4:
                if all(0 <= o <= 255 for o in octets):
                    ip_int = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
                    return str(ipaddress.ip_address(ip_int))
                # Octet overflow — return None (blocked as invalid)
                return None

    return None


# ── URL validation ────────────────────────────────────────────────────


def _validate_url(url: str) -> str | None:
    """Validate URL for safe navigation.

    Returns None if URL is safe, or an error message string if blocked.
    """
    import pagemap.server as _srv  # lazy: read _srv._allow_local at call time

    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format."

    # Scheme check
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return f"URL scheme '{scheme}' is not allowed. Use http or https."

    # Hostname extraction
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return "URL must include a hostname."

    # Cloud metadata hosts: always blocked (never exempted by --allow-local)
    if hostname in _CLOUD_METADATA_HOSTS:
        return f"Access to '{hostname}' is blocked."

    # Other blocked hosts (e.g. "localhost"): blocked unless --allow-local
    if hostname in BLOCKED_HOSTS and not _srv._allow_local:
        return f"Access to '{hostname}' is blocked."

    # Normalize IP formats (octal, hex, decimal) before checking
    normalized_ip = _normalize_ip(hostname)
    check_ip = normalized_ip or hostname

    # IP address check
    try:
        addr = ipaddress.ip_address(check_ip)

        # Cloud metadata IP range: always blocked
        if _is_cloud_metadata_ip(addr):
            return f"Access to cloud metadata IP '{hostname}' is blocked."

        # Private/reserved IP: blocked unless --allow-local covers this range
        for network in _PRIVATE_NETWORKS:
            if addr in network:
                if _srv._allow_local and _is_local_ip(addr):
                    return None  # permitted by --allow-local
                return f"Access to private/reserved IP '{hostname}' is blocked."
    except ValueError:
        # Not an IP literal — that's fine, it's a domain name
        pass

    return None


# ── DNS rebinding defense ─────────────────────────────────────────────

DNS_RESOLVE_TIMEOUT_SECONDS = 2.0


async def _resolve_dns(hostname: str) -> list[str]:
    """Resolve hostname to deduplicated IP address list.

    Uses asyncio.to_thread to avoid blocking the event loop.
    Raises ValueError on DNS failure or timeout.

    NOTE: Uses _srv.socket (pagemap.server.socket) so tests that patch
    'pagemap.server.socket.getaddrinfo' propagate correctly.
    """
    import pagemap.server as _srv  # lazy: tests patch _srv.socket.getaddrinfo

    def _sync_resolve() -> list[str]:
        results = _srv.socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        # Deduplicate IPs (getaddrinfo may return duplicates for different socket types)
        seen: set[str] = set()
        ips: list[str] = []
        for _family, _type, _proto, _canonname, sockaddr in results:
            ip = sockaddr[0]
            if ip not in seen:
                seen.add(ip)
                ips.append(ip)
        return ips

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_resolve),
            timeout=DNS_RESOLVE_TIMEOUT_SECONDS,
        )
    except TimeoutError as e:
        raise ValueError(f"DNS resolution timed out for '{hostname}'") from e
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for '{hostname}': {e}") from e


def _validate_resolved_ips(ips: list[str], hostname: str) -> str | None:
    """Check resolved IPs against private/reserved ranges.

    Returns None if all IPs are public, or an error message if any is private.
    Uses dual check: explicit _PRIVATE_NETWORKS list + is_global fallback.
    """
    import pagemap.server as _srv  # lazy: read _srv._allow_local at call time

    if not ips:
        return f"DNS resolution returned no addresses for '{hostname}'."

    for ip_str in ips:
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return f"Invalid IP '{ip_str}' resolved from '{hostname}'."

        # Cloud metadata: always blocked
        if _is_cloud_metadata_ip(addr):
            return f"DNS rebinding blocked: '{hostname}' resolved to cloud metadata IP {ip_str}."

        # Check 1: explicit private network membership
        is_private = any(addr in net for net in _PRIVATE_NETWORKS)
        if is_private:
            if _srv._allow_local and _is_local_ip(addr):
                continue  # permitted by --allow-local
            return f"DNS rebinding blocked: '{hostname}' resolved to private IP {ip_str}."

        # Check 2 (defense-in-depth): is_global catches reserved ranges
        # not in our explicit list (e.g., documentation, benchmarking ranges)
        # These are never local dev IPs — not exempted by --allow-local
        if not addr.is_global:
            return f"DNS rebinding blocked: '{hostname}' resolved to non-global IP {ip_str}."

    return None


async def _validate_url_with_dns(url: str) -> str | None:
    """Validate URL with DNS resolution for domain hostnames.

    Combines sync URL validation (scheme, IP literal) with async DNS
    resolution for domain names. Returns None if safe, error string if blocked.
    """
    # S4: SSRF Advanced — normalize once, use normalized URL for both checks (TOCTOU prevention)
    try:
        from pagemap.security import SSRF_ADVANCED_ENABLED

        if SSRF_ADVANCED_ENABLED:
            from pagemap.security.ssrf_advanced import normalize_url_safe, validate_url_advanced

            url = normalize_url_safe(url)  # Normalize once
            adv_err = validate_url_advanced(url)  # Advanced check on normalized URL
            if adv_err:
                return adv_err
    except ImportError:
        pass

    # Fast path: sync validation (scheme, blocked hosts, IP literals)
    error = _validate_url(url)
    if error:
        return error

    # Extract hostname for DNS check
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format."

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None  # Already caught by _validate_url

    # Skip DNS for IP literals — already validated by _validate_url
    try:
        ipaddress.ip_address(hostname)
        return None  # IP literal, already checked
    except ValueError:
        pass

    # Also skip if _normalize_ip recognizes it (octal/hex/decimal)
    if _normalize_ip(hostname) is not None:
        return None  # Non-standard IP format, already checked

    # Domain name — resolve and validate IPs
    try:
        ips = await _resolve_dns(hostname)
    except ValueError as e:
        return str(e)

    return _validate_resolved_ips(ips, hostname)
