"""Tests for --allow-local flag: selective localhost/RFC 1918 access.

Validates that:
- Default (flag OFF): all private IPs and localhost blocked (no regression)
- Flag ON: loopback, RFC 1918, IPv6 ULA permitted
- Cloud metadata (169.254.x.x, metadata.google.internal): always blocked
- _parse_server_args: CLI arg and env var parsing
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

import pagemap.server as srv
from pagemap.server import (
    _is_cloud_metadata_ip,
    _is_local_ip,
    _parse_server_args,
    _validate_resolved_ips,
    _validate_url,
    _validate_url_with_dns,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _fake_getaddrinfo_factory(ip_list: list[str]):
    """Return a mock getaddrinfo that returns the given IPs."""

    def _fake(hostname, port, family=0, type_=0, **kw):
        results = []
        for ip in ip_list:
            if ":" in ip:
                results.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0)))
            else:
                results.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)))
        return results

    return _fake


@pytest.fixture(autouse=True)
def _reset_allow_local():
    """Reset _allow_local flag before/after each test."""
    original = srv._allow_local
    srv._allow_local = False
    yield
    srv._allow_local = original


# ── TestValidateUrlDefaultBlocking ───────────────────────────────────


class TestValidateUrlDefaultBlocking:
    """Flag OFF: existing blocking behavior must be preserved."""

    def test_localhost_blocked(self):
        assert _validate_url("http://localhost:3000/") is not None

    def test_loopback_blocked(self):
        assert _validate_url("http://127.0.0.1/") is not None

    def test_10_network_blocked(self):
        assert _validate_url("http://10.0.0.1/") is not None

    def test_172_16_network_blocked(self):
        assert _validate_url("http://172.16.0.1/") is not None

    def test_192_168_network_blocked(self):
        assert _validate_url("http://192.168.1.1/") is not None

    def test_metadata_ip_blocked(self):
        assert _validate_url("http://169.254.169.254/") is not None

    def test_metadata_host_blocked(self):
        assert _validate_url("http://metadata.google.internal/") is not None


# ── TestValidateUrlAllowLocal ────────────────────────────────────────


class TestValidateUrlAllowLocal:
    """Flag ON: localhost and RFC 1918 ranges permitted."""

    def setup_method(self):
        srv._allow_local = True

    def test_localhost_allowed(self):
        assert _validate_url("http://localhost:3000/") is None

    def test_loopback_allowed(self):
        assert _validate_url("http://127.0.0.1:8080/") is None

    def test_loopback_non_canonical_allowed(self):
        """127.0.0.2 is also loopback."""
        assert _validate_url("http://127.0.0.2/") is None

    def test_10_network_allowed(self):
        assert _validate_url("http://10.0.0.1/") is None

    def test_172_16_network_allowed(self):
        assert _validate_url("http://172.16.0.1/") is None

    def test_192_168_network_allowed(self):
        assert _validate_url("http://192.168.1.100/") is None

    def test_ipv6_loopback_allowed(self):
        assert _validate_url("http://[::1]:3000/") is None

    def test_public_url_still_allowed(self):
        assert _validate_url("https://example.com/") is None

    def test_invalid_scheme_still_blocked(self):
        err = _validate_url("ftp://localhost/")
        assert err is not None
        assert "scheme" in err.lower()


# ── TestCloudMetadataAlwaysBlocked ───────────────────────────────────


class TestCloudMetadataAlwaysBlocked:
    """Cloud metadata must be blocked regardless of --allow-local."""

    def setup_method(self):
        srv._allow_local = True

    def test_metadata_ip_blocked_with_flag(self):
        err = _validate_url("http://169.254.169.254/latest/meta-data/")
        assert err is not None
        assert "blocked" in err.lower()

    def test_metadata_host_blocked_with_flag(self):
        err = _validate_url("http://metadata.google.internal/")
        assert err is not None
        assert "blocked" in err.lower()

    def test_link_local_non_canonical_blocked(self):
        """169.254.1.1 (non-canonical metadata range) still blocked."""
        err = _validate_url("http://169.254.1.1/")
        assert err is not None

    def test_cgnat_blocked_with_flag(self):
        """100.64.x (CGNAT) not exempted by --allow-local."""
        err = _validate_url("http://100.64.0.1/")
        assert err is not None

    def test_zero_network_blocked_with_flag(self):
        """0.0.0.0 not exempted by --allow-local."""
        err = _validate_url("http://0.0.0.0/")
        assert err is not None

    def test_metadata_ip_blocked_without_flag(self):
        srv._allow_local = False
        err = _validate_url("http://169.254.169.254/")
        assert err is not None

    def test_metadata_host_blocked_without_flag(self):
        srv._allow_local = False
        err = _validate_url("http://metadata.google.internal/")
        assert err is not None


# ── TestResolvedIpsAllowLocal ────────────────────────────────────────


class TestResolvedIpsAllowLocal:
    """_validate_resolved_ips with --allow-local flag."""

    def setup_method(self):
        srv._allow_local = True

    def test_loopback_allowed(self):
        assert _validate_resolved_ips(["127.0.0.1"], "myapp.local") is None

    def test_10_network_allowed(self):
        assert _validate_resolved_ips(["10.0.0.5"], "internal.dev") is None

    def test_192_168_allowed(self):
        assert _validate_resolved_ips(["192.168.1.100"], "home.dev") is None

    def test_metadata_ip_blocked(self):
        err = _validate_resolved_ips(["169.254.169.254"], "evil.com")
        assert err is not None
        assert "cloud metadata" in err.lower()

    def test_mixed_local_and_metadata_blocked(self):
        """If any IP is cloud metadata, entire resolution is blocked."""
        err = _validate_resolved_ips(["127.0.0.1", "169.254.169.254"], "tricky.com")
        assert err is not None

    def test_ipv6_loopback_allowed(self):
        assert _validate_resolved_ips(["::1"], "localhost6.dev") is None

    def test_cgnat_blocked(self):
        """CGNAT (100.64.x) is not local dev — stays blocked."""
        err = _validate_resolved_ips(["100.64.0.1"], "carrier.net")
        assert err is not None

    def test_public_ip_still_passes(self):
        assert _validate_resolved_ips(["93.184.216.34"], "example.com") is None


# ── TestValidateUrlWithDnsAllowLocal ─────────────────────────────────


class TestValidateUrlWithDnsAllowLocal:
    """Integration: _validate_url_with_dns with --allow-local."""

    def setup_method(self):
        srv._allow_local = True

    async def test_domain_resolving_to_loopback_allowed(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["127.0.0.1"]),
        ):
            result = await _validate_url_with_dns("http://myapp.local:3000/")
        assert result is None

    async def test_domain_resolving_to_192_168_allowed(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["192.168.1.50"]),
        ):
            result = await _validate_url_with_dns("http://devbox.local/")
        assert result is None

    async def test_domain_resolving_to_metadata_blocked(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["169.254.169.254"]),
        ):
            err = await _validate_url_with_dns("http://metadata-alias.evil.com/")
        assert err is not None

    async def test_localhost_url_allowed(self):
        """http://localhost:3000 should pass with --allow-local (no DNS needed)."""
        result = await _validate_url_with_dns("http://localhost:3000/")
        assert result is None


# ── TestHelperFunctions ──────────────────────────────────────────────


class TestHelperFunctions:
    """Unit tests for _is_cloud_metadata_ip and _is_local_ip helpers."""

    def test_is_cloud_metadata_ip_link_local(self):
        import ipaddress

        assert _is_cloud_metadata_ip(ipaddress.ip_address("169.254.169.254")) is True

    def test_is_cloud_metadata_ip_public(self):
        import ipaddress

        assert _is_cloud_metadata_ip(ipaddress.ip_address("8.8.8.8")) is False

    def test_is_cloud_metadata_ip_loopback(self):
        import ipaddress

        assert _is_cloud_metadata_ip(ipaddress.ip_address("127.0.0.1")) is False

    def test_is_local_ip_loopback(self):
        import ipaddress

        assert _is_local_ip(ipaddress.ip_address("127.0.0.1")) is True

    def test_is_local_ip_rfc1918(self):
        import ipaddress

        assert _is_local_ip(ipaddress.ip_address("192.168.1.1")) is True
        assert _is_local_ip(ipaddress.ip_address("10.0.0.1")) is True
        assert _is_local_ip(ipaddress.ip_address("172.16.0.1")) is True

    def test_is_local_ip_ipv6_loopback(self):
        import ipaddress

        assert _is_local_ip(ipaddress.ip_address("::1")) is True

    def test_is_local_ip_metadata(self):
        import ipaddress

        assert _is_local_ip(ipaddress.ip_address("169.254.169.254")) is False

    def test_is_local_ip_public(self):
        import ipaddress

        assert _is_local_ip(ipaddress.ip_address("8.8.8.8")) is False


# ── TestParseServerArgs ──────────────────────────────────────────────


class TestParseServerArgs:
    """Tests for _parse_server_args CLI + env var parsing.

    _parse_server_args returns (allow_local, telemetry_enabled) tuple.
    """

    def test_cli_flag_enables(self):
        allow_local, _telem = _parse_server_args(["--allow-local"])
        assert allow_local is True

    def test_no_flag_disables(self):
        allow_local, _telem = _parse_server_args([])
        assert allow_local is False

    def test_env_var_1_enables(self):
        with patch.dict("os.environ", {"PAGEMAP_ALLOW_LOCAL": "1"}):
            allow_local, _telem = _parse_server_args([])
            assert allow_local is True

    def test_env_var_true_enables(self):
        with patch.dict("os.environ", {"PAGEMAP_ALLOW_LOCAL": "true"}):
            allow_local, _telem = _parse_server_args([])
            assert allow_local is True

    def test_env_var_0_disables(self):
        with patch.dict("os.environ", {"PAGEMAP_ALLOW_LOCAL": "0"}):
            allow_local, _telem = _parse_server_args([])
            assert allow_local is False

    def test_both_cli_and_env(self):
        with patch.dict("os.environ", {"PAGEMAP_ALLOW_LOCAL": "1"}):
            allow_local, _telem = _parse_server_args(["--allow-local"])
            assert allow_local is True

    def test_env_var_yes_enables(self):
        with patch.dict("os.environ", {"PAGEMAP_ALLOW_LOCAL": "yes"}):
            allow_local, _telem = _parse_server_args([])
            assert allow_local is True

    def test_env_var_empty_disables(self):
        with patch.dict("os.environ", {"PAGEMAP_ALLOW_LOCAL": ""}):
            allow_local, _telem = _parse_server_args([])
            assert allow_local is False

    def test_unknown_args_ignored(self):
        """parse_known_args should ignore unknown flags like 'serve'."""
        allow_local, _telem = _parse_server_args(["serve", "--allow-local"])
        assert allow_local is True

    def test_telemetry_cli_flag(self):
        _local, telem = _parse_server_args(["--telemetry"])
        assert telem is True

    def test_telemetry_env_var(self):
        with patch.dict("os.environ", {"PAGEMAP_TELEMETRY": "1"}):
            _local, telem = _parse_server_args([])
            assert telem is True

    def test_telemetry_disabled_by_default(self):
        _local, telem = _parse_server_args([])
        assert telem is False
