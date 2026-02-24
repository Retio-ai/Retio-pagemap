# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""API Gateway middleware — trusted-proxy IP extraction + request-ID propagation.

Standalone leaf module with zero dependency on server.py.
Uses stdlib only (ipaddress, uuid, re, logging).

Design choices:

- **Pure ASGI** — no BaseHTTPMiddleware (avoids body buffering, SSE issues).
- **scope["client"] immutable** — downstream middleware sees original TCP peer.
  Extracted client IP stored in ``scope["state"]["client_ip"]`` only.
- **X-Request-ID sanitization** — regex validation prevents log injection (C2).
- **Multiple X-Forwarded-For** — all headers collected per RFC 2616 §4.2 (C3).
- **IPv6 normalization** — bracket/zone-ID stripped before parsing (C4).
- **Cloudflare CIDRs** — static defaults with staleness warning (I1).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

logger = logging.getLogger(__name__)

# ── Cloudflare IP ranges (static defaults) ────────────────────────────
# WARNING: These ranges may become stale. Verify against the canonical source:
#   IPv4: https://www.cloudflare.com/ips-v4
#   IPv6: https://www.cloudflare.com/ips-v6
# Last updated: 2026-02-23.
# For production use, consider periodically refreshing from the Cloudflare API:
#   https://api.cloudflare.com/client/v4/ips

CLOUDFLARE_IPV4_CIDRS: tuple[str, ...] = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
)

CLOUDFLARE_IPV6_CIDRS: tuple[str, ...] = (
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

# ── Request-ID validation (C2: log injection prevention) ──────────────

_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._\-]{1,128}$")


# ── GatewayConfig ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Immutable configuration for trusted proxy detection.

    ``trusted_networks`` is a tuple (not frozenset) because
    ``ipaddress`` networks require sequential containment checks.
    """

    trusted_hosts: frozenset[IPv4Address | IPv6Address]
    trusted_networks: tuple[IPv4Network | IPv6Network, ...]
    trust_all: bool = False


# ── Config parsing ────────────────────────────────────────────────────


def parse_trusted_proxies(raw: list[str]) -> GatewayConfig:
    """Parse a list of proxy specifications into a :class:`GatewayConfig`.

    Supported formats:
    - Single IP: ``"10.0.0.1"``, ``"::1"``
    - CIDR: ``"10.0.0.0/8"``, ``"2001:db8::/32"``
    - Keyword ``"cloudflare"`` — expands to static Cloudflare CIDRs
    - Keyword ``"*"`` — trust all peers (development only)

    Raises:
        ValueError: If any entry is invalid.
    """
    hosts: set[IPv4Address | IPv6Address] = set()
    networks: list[IPv4Network | IPv6Network] = []
    trust_all = False

    for entry in raw:
        entry = entry.strip()
        low = entry.lower()

        if low == "*":
            trust_all = True
            continue

        if low == "cloudflare":
            for cidr in CLOUDFLARE_IPV4_CIDRS:
                networks.append(ipaddress.ip_network(cidr, strict=False))
            for cidr in CLOUDFLARE_IPV6_CIDRS:
                networks.append(ipaddress.ip_network(cidr, strict=False))
            continue

        # Try CIDR first (contains "/"), then single IP
        if "/" in entry:
            networks.append(ipaddress.ip_network(entry, strict=False))
        else:
            normalized = _normalize_ip_str(entry)
            hosts.add(ipaddress.ip_address(normalized))

    return GatewayConfig(
        trusted_hosts=frozenset(hosts),
        trusted_networks=tuple(networks),
        trust_all=trust_all,
    )


# ── Internal helpers ──────────────────────────────────────────────────


def _sanitize_request_id(raw: str | None) -> str:
    """Validate and return request ID, or generate a new UUID.

    C2: Prevents log injection via malicious X-Request-ID values.
    Only ``[a-zA-Z0-9._-]{1,128}`` passes validation.
    """
    if raw and _REQUEST_ID_RE.match(raw):
        return raw
    return uuid.uuid4().hex


def _normalize_ip_str(raw: str) -> str:
    """Normalize an IP string for ``ipaddress.ip_address()``.

    C4: Handles IPv6 brackets (``[2001:db8::1]`` → ``2001:db8::1``)
    and zone IDs (``fe80::1%eth0`` → ``fe80::1``).
    """
    s = raw.strip()
    # Strip brackets
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    # Strip zone ID
    if "%" in s:
        s = s[: s.index("%")]
    return s


def _collect_xff(raw_headers: list[tuple[bytes, bytes]]) -> str:
    """Collect all ``X-Forwarded-For`` headers and join with commas.

    C3: Multiple XFF headers must be concatenated per RFC 2616 §4.2.
    """
    parts: list[str] = []
    for name, value in raw_headers:
        if name.lower() == b"x-forwarded-for":
            parts.append(value.decode("latin-1").strip())
    return ", ".join(parts)


def _is_trusted(addr: IPv4Address | IPv6Address, config: GatewayConfig) -> bool:
    """Check if an IP is in the trusted set.

    O(1) host lookup + O(n) network containment check.
    """
    if config.trust_all:
        return True
    if addr in config.trusted_hosts:
        return True
    return any(addr in net for net in config.trusted_networks)


def _extract_client_ip(xff_combined: str, config: GatewayConfig, peer_ip: str) -> str:
    """Walk XFF right-to-left; return first non-trusted IP.

    If all IPs are trusted, returns the leftmost entry.
    Falls back to ``peer_ip`` if XFF is empty.
    """
    if not xff_combined:
        return peer_ip

    entries = [e.strip() for e in xff_combined.split(",") if e.strip()]
    if not entries:
        return peer_ip

    # Right-to-left walk
    for entry in reversed(entries):
        try:
            normalized = _normalize_ip_str(entry)
            addr = ipaddress.ip_address(normalized)
        except ValueError:
            # Unparseable entry — treat as client IP (conservative)
            return entry
        if not _is_trusted(addr, config):
            return str(addr)

    # All trusted — return leftmost
    try:
        return str(ipaddress.ip_address(_normalize_ip_str(entries[0])))
    except ValueError:
        return entries[0]


def _parse_rfc7239_forwarded(value: str) -> list[dict[str, str]]:
    """Minimal RFC 7239 ``Forwarded`` header parser.

    Extracts ``for=`` directives. Handles quoted IPv6 addresses.
    Returns list of dicts with lowercase directive keys.
    """
    result: list[dict[str, str]] = []
    # Split by comma for multiple forwarded entries
    for entry in value.split(","):
        directives: dict[str, str] = {}
        for pair in entry.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            key, val = pair.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            # Remove quotes
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            directives[key] = val
        if directives:
            result.append(directives)
    return result


def _get_header(raw_headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    """Get the first header value by lowercase name."""
    for hdr_name, hdr_value in raw_headers:
        if hdr_name.lower() == name:
            return hdr_value.decode("latin-1").strip()
    return None


# ── ASGI Middleware ───────────────────────────────────────────────────


class GatewayMiddleware:
    """Pure ASGI middleware for reverse-proxy integration.

    Extracts real client IP from ``X-Forwarded-For`` (trusted peers only),
    propagates ``X-Request-ID``, and stores metadata in ``scope["state"]``.

    **Never** modifies ``scope["client"]`` (I3).
    """

    def __init__(self, app, config: GatewayConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # I2: Defensive initialization
        scope.setdefault("state", {})

        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        _client = scope.get("client")
        peer_ip = _client[0] if _client else ""

        # Determine client IP
        client_ip = peer_ip
        if peer_ip:
            try:
                normalized_peer = _normalize_ip_str(peer_ip)
                peer_addr = ipaddress.ip_address(normalized_peer)
                peer_trusted = _is_trusted(peer_addr, self.config)
            except ValueError:
                peer_trusted = False

            if peer_trusted:
                # Trusted peer — extract from XFF
                xff = _collect_xff(raw_headers)
                if xff:
                    client_ip = _extract_client_ip(xff, self.config, peer_ip)
                else:
                    # Try RFC 7239 Forwarded header as fallback
                    fwd = _get_header(raw_headers, b"forwarded")
                    if fwd:
                        entries = _parse_rfc7239_forwarded(fwd)
                        for entry in reversed(entries):
                            if "for" in entry:
                                try:
                                    fwd_ip = _normalize_ip_str(entry["for"])
                                    fwd_addr = ipaddress.ip_address(fwd_ip)
                                    if not _is_trusted(fwd_addr, self.config):
                                        client_ip = str(fwd_addr)
                                        break
                                except ValueError:
                                    client_ip = entry["for"]
                                    break
                # All Forwarded entries trusted → client_ip remains peer_ip (intentional)
                # Forwarded-Proto / Forwarded-Host
                proto = _get_header(raw_headers, b"x-forwarded-proto")
                if proto:
                    scope["state"]["forwarded_proto"] = proto
                host = _get_header(raw_headers, b"x-forwarded-host")
                if host:
                    scope["state"]["forwarded_host"] = host
            else:
                # Untrusted peer — ignore forwarded headers
                pass

        # X-Request-ID: sanitize or generate
        raw_rid = _get_header(raw_headers, b"x-request-id")
        request_id = _sanitize_request_id(raw_rid)

        # Store in scope["state"]
        scope["state"]["client_ip"] = client_ip
        scope["state"]["request_id"] = request_id

        # Nice-to-have: traceparent for Phase ζ OTel preparation
        traceparent = _get_header(raw_headers, b"traceparent")
        if traceparent:
            scope["state"]["traceparent"] = traceparent

        # Wrap send to inject X-Request-ID in response
        _rid_injected = False

        async def _send_with_request_id(message) -> None:
            nonlocal _rid_injected
            if message["type"] == "http.response.start" and not _rid_injected:
                _rid_injected = True
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send_with_request_id)
