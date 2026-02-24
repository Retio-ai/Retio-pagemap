# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for gateway middleware — trusted proxy, XFF, request-ID, ASGI middleware."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from unittest.mock import AsyncMock

import httpx
import pytest

from pagemap.gateway import (
    CLOUDFLARE_IPV4_CIDRS,
    CLOUDFLARE_IPV6_CIDRS,
    GatewayConfig,
    GatewayMiddleware,
    _collect_xff,
    _extract_client_ip,
    _is_trusted,
    _normalize_ip_str,
    _parse_rfc7239_forwarded,
    _sanitize_request_id,
    parse_trusted_proxies,
)

# ── TestParseConfig ───────────────────────────────────────────────────


class TestParseConfig:
    """Tests for parse_trusted_proxies()."""

    def test_single_ipv4(self):
        cfg = parse_trusted_proxies(["10.0.0.1"])
        assert IPv4Address("10.0.0.1") in cfg.trusted_hosts
        assert not cfg.trust_all

    def test_single_ipv6(self):
        cfg = parse_trusted_proxies(["::1"])
        assert IPv6Address("::1") in cfg.trusted_hosts

    def test_cidr_ipv4(self):
        cfg = parse_trusted_proxies(["10.0.0.0/8"])
        assert IPv4Network("10.0.0.0/8") in cfg.trusted_networks

    def test_cidr_ipv6(self):
        cfg = parse_trusted_proxies(["2001:db8::/32"])
        assert IPv6Network("2001:db8::/32") in cfg.trusted_networks

    def test_cloudflare_keyword(self):
        cfg = parse_trusted_proxies(["cloudflare"])
        assert len(cfg.trusted_networks) == len(CLOUDFLARE_IPV4_CIDRS) + len(CLOUDFLARE_IPV6_CIDRS)
        assert not cfg.trust_all

    def test_star_sets_trust_all(self):
        cfg = parse_trusted_proxies(["*"])
        assert cfg.trust_all is True

    def test_mixed_entries(self):
        cfg = parse_trusted_proxies(["10.0.0.1", "192.168.0.0/16", "cloudflare", "*"])
        assert cfg.trust_all is True
        assert IPv4Address("10.0.0.1") in cfg.trusted_hosts
        # 1 explicit CIDR + cloudflare CIDRs
        assert len(cfg.trusted_networks) == 1 + len(CLOUDFLARE_IPV4_CIDRS) + len(CLOUDFLARE_IPV6_CIDRS)

    def test_invalid_ip_raises(self):
        with pytest.raises(ValueError):
            parse_trusted_proxies(["not-an-ip"])

    def test_whitespace_stripped(self):
        cfg = parse_trusted_proxies(["  10.0.0.1  "])
        assert IPv4Address("10.0.0.1") in cfg.trusted_hosts

    def test_frozen_config(self):
        cfg = parse_trusted_proxies(["10.0.0.1"])
        with pytest.raises(AttributeError):
            cfg.trust_all = True  # type: ignore[misc]


# ── TestIsTrusted ─────────────────────────────────────────────────────


class TestIsTrusted:
    """Tests for _is_trusted()."""

    def test_exact_match(self):
        cfg = GatewayConfig(
            trusted_hosts=frozenset({IPv4Address("10.0.0.1")}),
            trusted_networks=(),
        )
        assert _is_trusted(IPv4Address("10.0.0.1"), cfg)

    def test_cidr_match(self):
        cfg = GatewayConfig(
            trusted_hosts=frozenset(),
            trusted_networks=(IPv4Network("10.0.0.0/8"),),
        )
        assert _is_trusted(IPv4Address("10.1.2.3"), cfg)

    def test_no_match(self):
        cfg = GatewayConfig(
            trusted_hosts=frozenset({IPv4Address("10.0.0.1")}),
            trusted_networks=(IPv4Network("192.168.0.0/16"),),
        )
        assert not _is_trusted(IPv4Address("172.16.0.1"), cfg)

    def test_trust_all(self):
        cfg = GatewayConfig(
            trusted_hosts=frozenset(),
            trusted_networks=(),
            trust_all=True,
        )
        assert _is_trusted(IPv4Address("1.2.3.4"), cfg)

    def test_ipv6_exact(self):
        cfg = GatewayConfig(
            trusted_hosts=frozenset({IPv6Address("::1")}),
            trusted_networks=(),
        )
        assert _is_trusted(IPv6Address("::1"), cfg)

    def test_ipv6_cidr(self):
        cfg = GatewayConfig(
            trusted_hosts=frozenset(),
            trusted_networks=(IPv6Network("2001:db8::/32"),),
        )
        assert _is_trusted(IPv6Address("2001:db8::1"), cfg)


# ── TestNormalizeIpStr ────────────────────────────────────────────────


class TestNormalizeIpStr:
    """C4: IPv6 bracket and zone ID normalization."""

    def test_bracketed_ipv6(self):
        assert _normalize_ip_str("[::1]") == "::1"

    def test_bracketed_full_ipv6(self):
        assert _normalize_ip_str("[2001:db8::1]") == "2001:db8::1"

    def test_zone_id_stripped(self):
        assert _normalize_ip_str("fe80::1%eth0") == "fe80::1"

    def test_bracket_and_zone(self):
        assert _normalize_ip_str("[fe80::1%25eth0]") == "fe80::1"

    def test_plain_ipv4_passthrough(self):
        assert _normalize_ip_str("10.0.0.1") == "10.0.0.1"

    def test_whitespace_stripped(self):
        assert _normalize_ip_str("  10.0.0.1  ") == "10.0.0.1"


# ── TestSanitizeRequestId ────────────────────────────────────────────


class TestSanitizeRequestId:
    """C2: X-Request-ID log injection prevention."""

    def test_valid_passthrough(self):
        assert _sanitize_request_id("abc-123.def_456") == "abc-123.def_456"

    def test_newline_rejected(self):
        result = _sanitize_request_id("abc\r\ndef")
        assert "\r" not in result
        assert "\n" not in result
        # Should be a valid UUID hex
        assert len(result) == 32

    def test_too_long_rejected(self):
        result = _sanitize_request_id("a" * 129)
        assert len(result) <= 128

    def test_empty_generates_uuid(self):
        result = _sanitize_request_id("")
        assert len(result) == 32  # uuid4().hex

    def test_none_generates_uuid(self):
        result = _sanitize_request_id(None)
        assert len(result) == 32

    def test_special_chars_rejected(self):
        result = _sanitize_request_id("id; DROP TABLE")
        assert len(result) == 32  # regenerated


# ── TestCollectXff ────────────────────────────────────────────────────


class TestCollectXff:
    """C3: Multiple X-Forwarded-For header collection."""

    def test_single_header(self):
        headers = [(b"x-forwarded-for", b"1.2.3.4")]
        assert _collect_xff(headers) == "1.2.3.4"

    def test_multiple_headers_concatenated(self):
        headers = [
            (b"x-forwarded-for", b"1.2.3.4"),
            (b"x-forwarded-for", b"5.6.7.8"),
        ]
        assert _collect_xff(headers) == "1.2.3.4, 5.6.7.8"

    def test_no_xff_returns_empty(self):
        headers = [(b"host", b"example.com")]
        assert _collect_xff(headers) == ""

    def test_case_insensitive(self):
        headers = [(b"X-Forwarded-For", b"1.2.3.4")]
        assert _collect_xff(headers) == "1.2.3.4"

    def test_chain_in_single_header(self):
        headers = [(b"x-forwarded-for", b"1.1.1.1, 2.2.2.2, 3.3.3.3")]
        assert _collect_xff(headers) == "1.1.1.1, 2.2.2.2, 3.3.3.3"


# ── TestExtractClientIp ──────────────────────────────────────────────


class TestExtractClientIp:
    """XFF right-to-left walk for client IP extraction."""

    def _cfg(self, *ips: str) -> GatewayConfig:
        hosts = set()
        nets = []
        for ip in ips:
            if "/" in ip:
                nets.append(IPv4Network(ip) if ":" not in ip else IPv6Network(ip))
            else:
                hosts.add(IPv4Address(ip) if ":" not in ip else IPv6Address(ip))
        return GatewayConfig(trusted_hosts=frozenset(hosts), trusted_networks=tuple(nets))

    def test_single_xff_entry(self):
        cfg = self._cfg("10.0.0.1")
        assert _extract_client_ip("1.2.3.4", cfg, "10.0.0.1") == "1.2.3.4"

    def test_chain_right_to_left(self):
        cfg = self._cfg("10.0.0.0/8")
        # client, proxy1, proxy2 (trusted)
        assert _extract_client_ip("1.1.1.1, 10.0.0.2, 10.0.0.1", cfg, "10.0.0.3") == "1.1.1.1"

    def test_all_trusted_returns_leftmost(self):
        cfg = self._cfg("10.0.0.0/8")
        assert _extract_client_ip("10.1.1.1, 10.2.2.2", cfg, "10.0.0.1") == "10.1.1.1"

    def test_empty_xff_returns_peer(self):
        cfg = self._cfg("10.0.0.1")
        assert _extract_client_ip("", cfg, "10.0.0.1") == "10.0.0.1"

    def test_ipv6_in_xff(self):
        cfg = self._cfg("::1")
        assert _extract_client_ip("2001:db8::1, ::1", cfg, "::1") == "2001:db8::1"

    def test_unparseable_treated_as_client(self):
        cfg = self._cfg("10.0.0.1")
        result = _extract_client_ip("not-an-ip, 10.0.0.1", cfg, "10.0.0.1")
        assert result == "not-an-ip"


# ── TestParseRfc7239 ─────────────────────────────────────────────────


class TestParseRfc7239:
    """RFC 7239 Forwarded header parsing."""

    def test_simple_for(self):
        result = _parse_rfc7239_forwarded("for=1.2.3.4")
        assert len(result) == 1
        assert result[0]["for"] == "1.2.3.4"

    def test_multi_directive(self):
        result = _parse_rfc7239_forwarded("for=1.2.3.4;proto=https;by=10.0.0.1")
        assert result[0]["for"] == "1.2.3.4"
        assert result[0]["proto"] == "https"
        assert result[0]["by"] == "10.0.0.1"

    def test_quoted_ipv6(self):
        result = _parse_rfc7239_forwarded('for="[2001:db8::1]"')
        assert result[0]["for"] == "[2001:db8::1]"

    def test_multi_entry(self):
        result = _parse_rfc7239_forwarded("for=1.1.1.1, for=2.2.2.2")
        assert len(result) == 2
        assert result[0]["for"] == "1.1.1.1"
        assert result[1]["for"] == "2.2.2.2"

    def test_unknown_directives_preserved(self):
        result = _parse_rfc7239_forwarded("for=1.2.3.4;custom=value")
        assert result[0]["custom"] == "value"


# ── TestGatewayMiddleware ────────────────────────────────────────────


class TestGatewayMiddleware:
    """ASGI middleware integration tests."""

    def _make_scope(
        self,
        *,
        peer_ip: str = "10.0.0.1",
        peer_port: int = 12345,
        headers: list[tuple[bytes, bytes]] | None = None,
        scope_type: str = "http",
    ) -> dict:
        scope = {
            "type": scope_type,
            "client": (peer_ip, peer_port),
            "headers": headers or [],
        }
        return scope

    def _cfg_trust_10(self) -> GatewayConfig:
        return parse_trusted_proxies(["10.0.0.0/8"])

    async def test_trusted_xff_extraction(self):
        """Trusted peer → XFF used to extract client IP."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            peer_ip="10.0.0.1",
            headers=[(b"x-forwarded-for", b"1.2.3.4")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["client_ip"] == "1.2.3.4"

    async def test_untrusted_ignores_xff(self):
        """Untrusted peer → XFF ignored, TCP peer used."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            peer_ip="1.2.3.4",
            headers=[(b"x-forwarded-for", b"5.6.7.8")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["client_ip"] == "1.2.3.4"

    async def test_request_id_passthrough(self):
        """Valid X-Request-ID is preserved."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            headers=[(b"x-request-id", b"my-req-123")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["request_id"] == "my-req-123"

    async def test_request_id_generated_when_missing(self):
        """No X-Request-ID → UUID generated."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope()
        await mw(scope, AsyncMock(), AsyncMock())
        assert len(captured["request_id"]) == 32  # uuid hex

    async def test_request_id_sanitized(self):
        """Malicious X-Request-ID is replaced."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            headers=[(b"x-request-id", b"evil\r\nHeader: injected")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert "\r" not in captured["request_id"]
        assert "\n" not in captured["request_id"]

    async def test_forwarded_proto_stored(self):
        """X-Forwarded-Proto stored in state for trusted peers."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            peer_ip="10.0.0.1",
            headers=[
                (b"x-forwarded-for", b"1.2.3.4"),
                (b"x-forwarded-proto", b"https"),
            ],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["forwarded_proto"] == "https"

    async def test_lifespan_passthrough(self):
        """Lifespan events pass through untouched."""
        cfg = self._cfg_trust_10()
        app_called = False

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True
            assert "state" not in scope or scope.get("state") == {}

        mw = GatewayMiddleware(app, cfg)
        scope = {"type": "lifespan"}
        await mw(scope, AsyncMock(), AsyncMock())
        assert app_called

    async def test_scope_client_immutable(self):
        """scope["client"] must not be modified (I3)."""
        cfg = self._cfg_trust_10()
        original_client = ("10.0.0.1", 12345)
        captured_client = None

        async def app(scope, receive, send):
            nonlocal captured_client
            captured_client = scope["client"]

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            peer_ip="10.0.0.1",
            headers=[(b"x-forwarded-for", b"1.2.3.4")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured_client == original_client

    async def test_response_header_injection(self):
        """X-Request-ID is injected into http.response.start."""
        cfg = self._cfg_trust_10()
        sent_messages = []

        async def send(message):
            sent_messages.append(message)

        async def app(scope, receive, send_fn):
            await send_fn({"type": "http.response.start", "status": 200, "headers": []})
            await send_fn({"type": "http.response.body", "body": b"ok"})

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            headers=[(b"x-request-id", b"test-rid-001")],
        )
        await mw(scope, AsyncMock(), send)

        # Check response.start has x-request-id
        start_msg = sent_messages[0]
        header_names = [h[0] for h in start_msg["headers"]]
        assert b"x-request-id" in header_names
        rid_val = dict(start_msg["headers"])[b"x-request-id"]
        assert rid_val == b"test-rid-001"

    async def test_response_header_injected_once(self):
        """X-Request-ID injected only on first http.response.start (idempotent)."""
        cfg = self._cfg_trust_10()
        sent_messages = []

        async def send(message):
            sent_messages.append(message)

        async def app(scope, receive, send_fn):
            # SSE: multiple response.start-like calls (simulate)
            await send_fn({"type": "http.response.start", "status": 200, "headers": []})
            await send_fn({"type": "http.response.start", "status": 200, "headers": []})

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope()
        await mw(scope, AsyncMock(), send)

        # First should have x-request-id, second should NOT
        first_headers = dict(sent_messages[0]["headers"])
        assert b"x-request-id" in first_headers
        second_headers = dict(sent_messages[1]["headers"])
        assert b"x-request-id" not in second_headers

    async def test_scope_state_defensive_init(self):
        """I2: scope without "state" key is handled."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = {
            "type": "http",
            "client": ("10.0.0.1", 12345),
            "headers": [],
            # No "state" key!
        }
        await mw(scope, AsyncMock(), AsyncMock())
        assert "client_ip" in captured
        assert "request_id" in captured

    async def test_websocket_scope_handled(self):
        """WebSocket scopes also get gateway processing."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            scope_type="websocket",
            peer_ip="10.0.0.1",
            headers=[(b"x-forwarded-for", b"1.2.3.4")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["client_ip"] == "1.2.3.4"

    async def test_traceparent_stored(self):
        """traceparent header stored in state for OTel prep."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            headers=[(b"traceparent", b"00-abc123-def456-01")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["traceparent"] == "00-abc123-def456-01"

    async def test_rfc7239_forwarded_extraction(self):
        """Trusted peer + RFC 7239 Forwarded header (no XFF) → client IP extracted."""
        cfg = self._cfg_trust_10()
        captured = {}

        async def app(scope, receive, send):
            captured.update(scope["state"])

        mw = GatewayMiddleware(app, cfg)
        scope = self._make_scope(
            peer_ip="10.0.0.1",
            headers=[(b"forwarded", b"for=1.2.3.4;proto=https")],
        )
        await mw(scope, AsyncMock(), AsyncMock())
        assert captured["client_ip"] == "1.2.3.4"


# ── TestGatewayIntegration ───────────────────────────────────────────


class TestGatewayIntegration:
    """Integration tests through middleware with httpx.ASGITransport."""

    @pytest.fixture
    def app(self):
        import pagemap.server as srv

        return srv.mcp.streamable_http_app()

    @pytest.fixture
    def gw_app(self, app):
        cfg = parse_trusted_proxies(["10.0.0.0/8"])
        return GatewayMiddleware(app, cfg)

    @pytest.fixture
    def client(self, gw_app):
        transport = httpx.ASGITransport(app=gw_app)
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def test_health_through_gateway(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_livez_through_gateway(self, client):
        resp = await client.get("/livez")
        assert resp.status_code == 200

    async def test_readyz_through_gateway(self, client):
        resp = await client.get("/readyz")
        assert resp.status_code == 200

    async def test_response_has_request_id(self, client):
        resp = await client.get("/health", headers={"x-request-id": "my-test-id"})
        assert resp.headers.get("x-request-id") == "my-test-id"

    async def test_response_generates_request_id(self, client):
        resp = await client.get("/health")
        rid = resp.headers.get("x-request-id")
        assert rid is not None
        assert len(rid) == 32
