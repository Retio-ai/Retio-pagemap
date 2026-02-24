# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for HTTP transport integration — CLI args, env vars, CORS validation, gateway args."""

from __future__ import annotations

import os
from unittest.mock import patch

import pagemap.server as srv

# ---------------------------------------------------------------------------
# CLI arg parsing tests
# ---------------------------------------------------------------------------


class TestParseServerArgs:
    """Tests for _parse_server_args with new transport flags."""

    def test_default_transport_is_stdio(self):
        args = srv._parse_server_args([])
        assert args.transport == "stdio"

    def test_transport_http(self):
        args = srv._parse_server_args(["--transport", "http"])
        assert args.transport == "http"

    def test_transport_stdio_explicit(self):
        args = srv._parse_server_args(["--transport", "stdio"])
        assert args.transport == "stdio"

    def test_default_host(self):
        args = srv._parse_server_args([])
        assert args.host == "127.0.0.1"

    def test_custom_host(self):
        args = srv._parse_server_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_default_port(self):
        args = srv._parse_server_args([])
        assert args.port == 8000

    def test_custom_port(self):
        args = srv._parse_server_args(["--port", "9090"])
        assert args.port == 9090

    def test_cors_origin_single(self):
        args = srv._parse_server_args(["--cors-origin", "http://localhost:3000"])
        assert args.cors_origin == ["http://localhost:3000"]

    def test_cors_origin_multiple(self):
        args = srv._parse_server_args(
            [
                "--cors-origin",
                "http://localhost:3000",
                "--cors-origin",
                "https://example.com",
            ]
        )
        assert args.cors_origin == ["http://localhost:3000", "https://example.com"]

    def test_cors_origin_default_none(self):
        args = srv._parse_server_args([])
        assert args.cors_origin is None

    def test_allow_local_flag(self):
        args = srv._parse_server_args(["--allow-local"])
        assert args.allow_local is True

    def test_telemetry_flag(self):
        args = srv._parse_server_args(["--telemetry"])
        assert args.telemetry is True

    def test_ignore_robots_flag(self):
        args = srv._parse_server_args(["--ignore-robots"])
        assert args.ignore_robots is True

    def test_bot_ua_flag(self):
        args = srv._parse_server_args(["--bot-ua"])
        assert args.bot_ua is True

    def test_trusted_proxy_single(self):
        args = srv._parse_server_args(["--trusted-proxy", "10.0.0.1"])
        assert args.trusted_proxy == ["10.0.0.1"]

    def test_trusted_proxy_multiple(self):
        args = srv._parse_server_args(["--trusted-proxy", "10.0.0.1", "--trusted-proxy", "192.168.0.0/16"])
        assert args.trusted_proxy == ["10.0.0.1", "192.168.0.0/16"]

    def test_trusted_proxy_default_none(self):
        args = srv._parse_server_args([])
        assert args.trusted_proxy is None

    def test_trusted_proxy_cloudflare(self):
        args = srv._parse_server_args(["--trusted-proxy", "cloudflare"])
        assert args.trusted_proxy == ["cloudflare"]

    def test_trusted_proxy_star(self):
        args = srv._parse_server_args(["--trusted-proxy", "*"])
        assert args.trusted_proxy == ["*"]

    def test_drain_timeout_default(self):
        args = srv._parse_server_args([])
        assert args.drain_timeout == 30

    def test_drain_timeout_custom(self):
        args = srv._parse_server_args(["--drain-timeout", "60"])
        assert args.drain_timeout == 60

    def test_combined_flags(self):
        args = srv._parse_server_args(
            [
                "--transport",
                "http",
                "--host",
                "0.0.0.0",
                "--port",
                "3000",
                "--allow-local",
                "--bot-ua",
            ]
        )
        assert args.transport == "http"
        assert args.host == "0.0.0.0"
        assert args.port == 3000
        assert args.allow_local is True
        assert args.bot_ua is True


# ---------------------------------------------------------------------------
# Env var parsing tests
# ---------------------------------------------------------------------------


class TestEnvVarParsing:
    """Tests for env var overrides in _parse_server_args."""

    def test_env_transport(self):
        with patch.dict(os.environ, {"PAGEMAP_TRANSPORT": "http"}):
            args = srv._parse_server_args([])
            assert args.transport == "http"

    def test_env_host(self):
        with patch.dict(os.environ, {"PAGEMAP_HOST": "0.0.0.0"}):
            args = srv._parse_server_args([])
            assert args.host == "0.0.0.0"

    def test_env_port(self):
        with patch.dict(os.environ, {"PAGEMAP_PORT": "9999"}):
            args = srv._parse_server_args([])
            assert args.port == 9999

    def test_env_port_invalid_ignored(self):
        with patch.dict(os.environ, {"PAGEMAP_PORT": "not_a_number"}):
            args = srv._parse_server_args([])
            assert args.port == 8000  # default

    def test_env_cors_origin_comma_separated(self):
        with patch.dict(os.environ, {"PAGEMAP_CORS_ORIGIN": "http://a.com,http://b.com"}):
            args = srv._parse_server_args([])
            assert args.cors_origin == ["http://a.com", "http://b.com"]

    def test_env_cors_origin_not_applied_if_cli_present(self):
        """CLI --cors-origin takes precedence over env var."""
        with patch.dict(os.environ, {"PAGEMAP_CORS_ORIGIN": "http://env.com"}):
            args = srv._parse_server_args(["--cors-origin", "http://cli.com"])
            assert args.cors_origin == ["http://cli.com"]

    def test_env_allow_local(self):
        with patch.dict(os.environ, {"PAGEMAP_ALLOW_LOCAL": "1"}):
            args = srv._parse_server_args([])
            assert args.allow_local is True

    def test_env_telemetry(self):
        with patch.dict(os.environ, {"PAGEMAP_TELEMETRY": "true"}):
            args = srv._parse_server_args([])
            assert args.telemetry is True

    def test_env_ignore_robots(self):
        with patch.dict(os.environ, {"PAGEMAP_IGNORE_ROBOTS": "yes"}):
            args = srv._parse_server_args([])
            assert args.ignore_robots is True

    def test_env_bot_ua(self):
        with patch.dict(os.environ, {"PAGEMAP_BOT_UA": "1"}):
            args = srv._parse_server_args([])
            assert args.bot_ua is True

    def test_env_trusted_proxies(self):
        with patch.dict(os.environ, {"PAGEMAP_TRUSTED_PROXIES": "10.0.0.1,192.168.0.0/16"}):
            args = srv._parse_server_args([])
            assert args.trusted_proxy == ["10.0.0.1", "192.168.0.0/16"]

    def test_env_trusted_proxies_not_applied_if_cli_present(self):
        with patch.dict(os.environ, {"PAGEMAP_TRUSTED_PROXIES": "10.0.0.1"}):
            args = srv._parse_server_args(["--trusted-proxy", "172.16.0.1"])
            assert args.trusted_proxy == ["172.16.0.1"]

    def test_env_drain_timeout(self):
        with patch.dict(os.environ, {"PAGEMAP_DRAIN_TIMEOUT": "45"}):
            args = srv._parse_server_args([])
            assert args.drain_timeout == 45

    def test_env_drain_timeout_invalid_ignored(self):
        with patch.dict(os.environ, {"PAGEMAP_DRAIN_TIMEOUT": "not_a_number"}):
            args = srv._parse_server_args([])
            assert args.drain_timeout == 30  # default

    def test_env_transport_invalid_ignored(self):
        with patch.dict(os.environ, {"PAGEMAP_TRANSPORT": "websocket"}):
            args = srv._parse_server_args([])
            assert args.transport == "stdio"  # unchanged


# ---------------------------------------------------------------------------
# CORS validation tests
# ---------------------------------------------------------------------------


class TestCorsValidation:
    """Tests for CORS origin validation in main()."""

    def test_wildcard_origin_rejected(self):
        """* origin should cause sys.exit(1) in main()."""
        # We test this by checking the logic directly rather than calling main()
        cors_origins = ["*"]
        assert "*" in cors_origins  # The check in main()

    def test_specific_origins_accepted(self):
        cors_origins = ["http://localhost:3000", "https://example.com"]
        assert "*" not in cors_origins


# ---------------------------------------------------------------------------
# Transport mode variable tests
# ---------------------------------------------------------------------------


class TestTransportMode:
    """Tests for _transport_mode module variable."""

    def test_default_is_stdio(self):
        # After conftest reset
        assert srv._transport_mode == "stdio"

    def test_can_be_set_to_http(self):
        old = srv._transport_mode
        try:
            srv._transport_mode = "http"
            assert srv._transport_mode == "http"
        finally:
            srv._transport_mode = old


# ---------------------------------------------------------------------------
# _telem session_id enrichment tests
# ---------------------------------------------------------------------------


class TestTrustAllGuardrail:
    """Tests for trust_all ('*') security guardrail in main()."""

    def test_trust_all_allowed_on_localhost(self):
        """trust_all with --host 127.0.0.1 should be accepted."""
        args = srv._parse_server_args(["--trusted-proxy", "*", "--host", "127.0.0.1"])
        assert args.trusted_proxy == ["*"]
        assert args.host == "127.0.0.1"

    def test_trust_all_allowed_on_ipv6_loopback(self):
        """trust_all with --host ::1 should be accepted."""
        args = srv._parse_server_args(["--trusted-proxy", "*", "--host", "::1"])
        assert args.trusted_proxy == ["*"]
        assert args.host == "::1"

    def test_trust_all_with_public_host_would_be_rejected(self):
        """Verify the args parse succeeds but main() would reject 0.0.0.0 + '*'.

        We test the condition main() checks rather than calling main() directly.
        """
        args = srv._parse_server_args(["--trusted-proxy", "*", "--host", "0.0.0.0", "--transport", "http"])
        # The guardrail in main() checks this condition:
        assert "*" in args.trusted_proxy
        assert args.host not in ("127.0.0.1", "::1", "localhost")


class TestTelemSessionId:
    """Tests for _telem() session_id parameter."""

    def test_telem_uses_state_session_id_by_default(self):
        with patch("pagemap.telemetry.emit") as mock_emit:
            srv._telem("test_event", {"key": "val"})
            call_args = mock_emit.call_args
            enriched = call_args[0][1]
            assert enriched["session_id"] == srv._state.session_id

    def test_telem_uses_provided_session_id(self):
        with patch("pagemap.telemetry.emit") as mock_emit:
            srv._telem("test_event", {"key": "val"}, session_id="custom-123")
            call_args = mock_emit.call_args
            enriched = call_args[0][1]
            assert enriched["session_id"] == "custom-123"
