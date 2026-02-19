"""Security tests for the Page Map MCP server.

Tests URL validation, input sanitization, key whitelisting, and error handling.
"""

from pagemap.server import (
    ALLOWED_KEY_COMBOS,
    ALLOWED_KEYS,
    MAX_SELECT_VALUE_LENGTH,
    MAX_TYPE_VALUE_LENGTH,
    VALID_ACTIONS,
    _normalize_ip,
    _safe_error,
    _validate_url,
)

# ── URL Validation ───────────────────────────────────────────────────


class TestValidateUrl:
    """Tests for _validate_url (SSRF prevention)."""

    # --- Allowed URLs ---

    def test_https_url_allowed(self):
        assert _validate_url("https://www.google.com") is None

    def test_http_url_allowed(self):
        assert _validate_url("http://example.com") is None

    def test_https_with_path_and_query(self):
        assert _validate_url("https://ko.wikipedia.org/wiki/대한민국?action=view") is None

    def test_https_with_port(self):
        assert _validate_url("https://example.com:8443/page") is None

    # --- Blocked schemes ---

    def test_file_scheme_blocked(self):
        err = _validate_url("file:///etc/passwd")
        assert err is not None
        assert "scheme" in err.lower() or "not allowed" in err.lower()

    def test_file_scheme_ssh_key_blocked(self):
        err = _validate_url("file:///Users/user/.ssh/id_rsa")
        assert err is not None

    def test_javascript_scheme_blocked(self):
        err = _validate_url("javascript:alert(1)")
        assert err is not None

    def test_data_scheme_blocked(self):
        err = _validate_url("data:text/html,<script>alert(1)</script>")
        assert err is not None

    def test_ftp_scheme_blocked(self):
        err = _validate_url("ftp://files.example.com/secret.txt")
        assert err is not None

    def test_empty_scheme_blocked(self):
        err = _validate_url("://no-scheme.com")
        assert err is not None

    # --- Blocked hosts ---

    def test_localhost_blocked(self):
        err = _validate_url("http://localhost:3000/admin")
        assert err is not None
        assert "blocked" in err.lower()

    def test_localhost_uppercase_blocked(self):
        err = _validate_url("http://LOCALHOST/admin")
        assert err is not None

    def test_metadata_google_blocked(self):
        err = _validate_url("http://metadata.google.internal/computeMetadata/v1/")
        assert err is not None

    def test_aws_metadata_ip_blocked(self):
        err = _validate_url("http://169.254.169.254/latest/meta-data/")
        assert err is not None

    # --- Blocked private IPs ---

    def test_loopback_127_blocked(self):
        err = _validate_url("http://127.0.0.1:8080/")
        assert err is not None
        assert "private" in err.lower() or "blocked" in err.lower()

    def test_loopback_127_x_blocked(self):
        err = _validate_url("http://127.0.0.2/")
        assert err is not None

    def test_private_10_blocked(self):
        err = _validate_url("http://10.0.0.1/internal")
        assert err is not None

    def test_private_172_blocked(self):
        err = _validate_url("http://172.16.0.1/")
        assert err is not None

    def test_private_192_168_blocked(self):
        err = _validate_url("http://192.168.1.1/")
        assert err is not None

    def test_ipv6_loopback_blocked(self):
        err = _validate_url("http://[::1]:8080/")
        assert err is not None

    # --- Edge cases ---

    def test_no_hostname_blocked(self):
        err = _validate_url("http:///path")
        assert err is not None
        assert "hostname" in err.lower()

    def test_public_ip_allowed(self):
        assert _validate_url("http://8.8.8.8/") is None

    def test_non_private_172_allowed(self):
        # 172.32.0.0 is NOT in 172.16.0.0/12
        assert _validate_url("http://172.32.0.1/") is None


# ── Key Whitelist ────────────────────────────────────────────────────


class TestKeyWhitelist:
    """Tests for press_key allowed keys."""

    def test_enter_allowed(self):
        assert "Enter" in ALLOWED_KEYS

    def test_tab_allowed(self):
        assert "Tab" in ALLOWED_KEYS

    def test_escape_allowed(self):
        assert "Escape" in ALLOWED_KEYS

    def test_arrow_keys_allowed(self):
        for key in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"):
            assert key in ALLOWED_KEYS

    def test_shift_tab_combo_allowed(self):
        assert "Shift+Tab" in ALLOWED_KEY_COMBOS

    def test_ctrl_c_combo_allowed(self):
        assert "Control+c" in ALLOWED_KEY_COMBOS

    def test_dangerous_ctrl_w_not_allowed(self):
        # Control+w = close tab
        assert "Control+w" not in ALLOWED_KEY_COMBOS
        assert "Control+w" not in ALLOWED_KEYS

    def test_dangerous_ctrl_q_not_allowed(self):
        # Control+q = quit browser
        assert "Control+q" not in ALLOWED_KEY_COMBOS

    def test_dangerous_alt_f4_not_allowed(self):
        assert "Alt+F4" not in ALLOWED_KEY_COMBOS
        assert "Alt+F4" not in ALLOWED_KEYS

    def test_meta_q_not_allowed(self):
        # Meta+q = quit app on macOS
        assert "Meta+q" not in ALLOWED_KEY_COMBOS


# ── Action Validation ────────────────────────────────────────────────


class TestActionValidation:
    """Tests for action type and value constraints."""

    def test_valid_actions(self):
        assert {"click", "type", "select", "press_key"} == VALID_ACTIONS

    def test_type_max_length(self):
        assert MAX_TYPE_VALUE_LENGTH == 1000

    def test_select_max_length(self):
        assert MAX_SELECT_VALUE_LENGTH == 500


# ── Error Sanitization ───────────────────────────────────────────────


class TestSafeError:
    """Tests for _safe_error message sanitization."""

    def test_strips_unix_paths(self):
        exc = Exception("FileNotFoundError: /Users/john/.ssh/id_rsa not found")
        msg = _safe_error("test", exc)
        assert "/Users/john/.ssh/id_rsa" not in msg
        assert "<path>" in msg

    def test_strips_nested_paths(self):
        exc = Exception("Error in /home/deploy/app/secrets/config.yaml line 42")
        msg = _safe_error("test", exc)
        assert "/home/deploy" not in msg

    def test_truncates_long_messages(self):
        exc = Exception("x" * 500)
        msg = _safe_error("test", exc)
        assert len(msg) < 300  # context prefix + 200 char limit + "..."

    def test_includes_context(self):
        exc = Exception("something failed")
        msg = _safe_error("get_page_map", exc)
        assert "get_page_map" in msg

    def test_preserves_useful_info(self):
        exc = TimeoutError("Navigation timeout of 30000ms exceeded")
        msg = _safe_error("test", exc)
        assert "timeout" in msg.lower() or "30000" in msg

    def test_strips_api_keys(self):
        exc = Exception("ANTHROPIC_API_KEY=sk-ant-abc123xyz789 found in config")
        msg = _safe_error("test", exc)
        assert "sk-ant-abc123xyz789" not in msg
        assert "<redacted>" in msg

    def test_strips_bearer_tokens(self):
        exc = Exception("Auth failed with Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        msg = _safe_error("test", exc)
        assert "eyJhbGciOiJIUzI1NiJ9" not in msg
        assert "<redacted>" in msg

    def test_strips_generic_secret(self):
        exc = Exception("SECRET=mysupersecretvalue leaked")
        msg = _safe_error("test", exc)
        assert "mysupersecretvalue" not in msg


# ── IP Normalization ────────────────────────────────────────────────


class TestNormalizeIp:
    """Tests for _normalize_ip (SSRF bypass prevention)."""

    def test_standard_ipv4_passthrough(self):
        assert _normalize_ip("127.0.0.1") == "127.0.0.1"

    def test_standard_ipv6_passthrough(self):
        assert _normalize_ip("::1") == "::1"

    def test_decimal_integer_loopback(self):
        # 2130706433 = 127.0.0.1
        result = _normalize_ip("2130706433")
        assert result == "127.0.0.1"

    def test_hex_loopback(self):
        # 0x7f000001 = 127.0.0.1
        result = _normalize_ip("0x7f000001")
        assert result == "127.0.0.1"

    def test_decimal_10_network(self):
        # 167772161 = 10.0.0.1
        result = _normalize_ip("167772161")
        assert result == "10.0.0.1"

    def test_not_an_ip(self):
        assert _normalize_ip("example.com") is None

    def test_empty_string(self):
        assert _normalize_ip("") is None

    def test_zero_ip(self):
        result = _normalize_ip("0")
        assert result == "0.0.0.0"


class TestSsrfAdvanced:
    """Advanced SSRF bypass prevention tests."""

    # --- Decimal integer bypass ---

    def test_decimal_loopback_blocked(self):
        err = _validate_url("http://2130706433/")
        assert err is not None
        assert "private" in err.lower() or "blocked" in err.lower()

    def test_decimal_10_network_blocked(self):
        err = _validate_url("http://167772161/")
        assert err is not None

    # --- Hex bypass ---

    def test_hex_loopback_blocked(self):
        err = _validate_url("http://0x7f000001/")
        assert err is not None

    # --- CGNAT range ---

    def test_cgnat_100_64_blocked(self):
        err = _validate_url("http://100.64.0.1/")
        assert err is not None
        assert "private" in err.lower() or "blocked" in err.lower()

    def test_cgnat_100_127_blocked(self):
        err = _validate_url("http://100.127.255.254/")
        assert err is not None

    # --- IPv4-mapped IPv6 ---

    def test_ipv4_mapped_ipv6_loopback_blocked(self):
        err = _validate_url("http://[::ffff:127.0.0.1]/")
        assert err is not None

    def test_ipv4_mapped_ipv6_private_blocked(self):
        err = _validate_url("http://[::ffff:10.0.0.1]/")
        assert err is not None

    # --- 0.0.0.0/8 ---

    def test_zero_network_blocked(self):
        err = _validate_url("http://0.0.0.0/")
        assert err is not None

    def test_zero_network_0_1_blocked(self):
        err = _validate_url("http://0.0.0.1/")
        assert err is not None

    # --- Public IPs still allowed ---

    def test_public_cloudflare_allowed(self):
        assert _validate_url("https://1.1.1.1/") is None

    def test_public_100_non_cgnat_allowed(self):
        # 100.128.0.1 is NOT in 100.64.0.0/10 (CGNAT ends at 100.127.x.x)
        assert _validate_url("http://100.128.0.1/") is None


# ── Octal Pure Parsing ──────────────────────────────────────────────


class TestNormalizeIpOctalPureParsing:
    """Tests that octal IP parsing uses pure arithmetic (no DNS queries)."""

    def test_octal_loopback(self):
        # 0177 = 127 in octal
        assert _normalize_ip("0177.0.0.1") == "127.0.0.1"

    def test_octal_10_network(self):
        # 012 = 10 in octal
        assert _normalize_ip("012.0.0.1") == "10.0.0.1"

    def test_octal_all_octets(self):
        # 0300.0375.0.01 = 192.253.0.1
        assert _normalize_ip("0300.0375.0.01") == "192.253.0.1"

    def test_invalid_octal_digit_8(self):
        # 8 is not a valid octal digit
        assert _normalize_ip("0189.0.0.1") is None

    def test_invalid_octal_digit_9(self):
        assert _normalize_ip("099.0.0.1") is None

    def test_five_octets_rejected(self):
        assert _normalize_ip("0177.0.0.0.1") is None

    def test_three_octets_rejected(self):
        assert _normalize_ip("0177.0.1") is None

    def test_empty_octet_rejected(self):
        assert _normalize_ip("0177..0.1") is None

    def test_octet_overflow_octal_256(self):
        # 0400 in octal = 256, exceeds 255
        assert _normalize_ip("0400.0.0.1") is None

    def test_no_dns_called(self):
        """Ensure socket.gethostbyname is never called during octal parsing."""
        from unittest.mock import patch

        with patch("socket.gethostbyname") as mock_dns:
            _normalize_ip("0177.0.0.1")
            mock_dns.assert_not_called()

    def test_no_dns_called_invalid(self):
        from unittest.mock import patch

        with patch("socket.gethostbyname") as mock_dns:
            _normalize_ip("0189.0.0.1")
            mock_dns.assert_not_called()

    def test_validate_url_blocks_octal_loopback(self):
        err = _validate_url("http://0177.0.0.1/")
        assert err is not None
        assert "private" in err.lower() or "blocked" in err.lower()

    def test_validate_url_blocks_octal_10_network(self):
        err = _validate_url("http://012.0.0.1/")
        assert err is not None

    def test_non_octal_dotted_passthrough(self):
        # No leading zero → not octal, already handled by ipaddress
        assert _normalize_ip("192.168.1.1") == "192.168.1.1"

    def test_mixed_octal_and_decimal(self):
        # 0300 = 192, 0250 = 168, 1, 1
        assert _normalize_ip("0300.0250.1.1") == "192.168.1.1"
