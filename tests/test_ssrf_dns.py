"""Tests for DNS rebinding defense, redirect chain validation, and SSRF integration.

Tests the S2 SSRF hardening: pre-nav DNS, post-nav DNS, route guard,
and execute_action SSRF checks.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pagemap import Interactable, PageMap
from pagemap.server import (
    _resolve_dns,
    _validate_resolved_ips,
    _validate_url,
    _validate_url_with_dns,
    execute_action,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_page_map(url: str = "https://example.com") -> PageMap:
    """Create a minimal PageMap for testing."""
    return PageMap(
        url=url,
        title="Test Page",
        page_type="unknown",
        interactables=[
            Interactable(
                ref=1,
                role="link",
                name="Click Me",
                affordance="click",
                region="main",
                tier=1,
            ),
        ],
        pruned_context="",
        pruned_tokens=0,
        generation_ms=0.0,
    )


def _make_mock_session(current_url: str = "https://example.com") -> MagicMock:
    """Create a mock BrowserSession."""
    session = MagicMock()
    session.get_page_url = AsyncMock(return_value=current_url)

    locator = AsyncMock()
    locator.first = AsyncMock()
    locator.first.click = AsyncMock()
    locator.count = AsyncMock(return_value=1)

    page = MagicMock()
    page.get_by_role = MagicMock(return_value=locator)
    page.wait_for_timeout = AsyncMock()
    page.goto = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    session.page = page
    return session


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
def _reset_state():
    """Reset global state before each test."""
    import pagemap.server as srv

    srv._last_page_map = None
    yield
    srv._last_page_map = None


# ── TestResolveDns ───────────────────────────────────────────────────


class TestResolveDns:
    """Tests for _resolve_dns async DNS resolution."""

    @pytest.mark.asyncio
    async def test_resolves_ipv4(self):
        with patch("pagemap.server.socket.getaddrinfo", _fake_getaddrinfo_factory(["93.184.216.34"])):
            ips = await _resolve_dns("example.com")
        assert ips == ["93.184.216.34"]

    @pytest.mark.asyncio
    async def test_resolves_ipv6(self):
        with patch(
            "pagemap.server.socket.getaddrinfo", _fake_getaddrinfo_factory(["2606:2800:220:1:248:1893:25c8:1946"])
        ):
            ips = await _resolve_dns("example.com")
        assert ips == ["2606:2800:220:1:248:1893:25c8:1946"]

    @pytest.mark.asyncio
    async def test_resolves_mixed_ipv4_ipv6(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]),
        ):
            ips = await _resolve_dns("example.com")
        assert len(ips) == 2
        assert "93.184.216.34" in ips
        assert "2606:2800:220:1:248:1893:25c8:1946" in ips

    @pytest.mark.asyncio
    async def test_deduplicates_ips(self):
        """getaddrinfo may return duplicate IPs for different socket types."""
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["93.184.216.34", "93.184.216.34"]),
        ):
            ips = await _resolve_dns("example.com")
        assert ips == ["93.184.216.34"]

    @pytest.mark.asyncio
    async def test_gaierror_raises_valueerror(self):
        def _fail(*args, **kw):
            raise socket.gaierror("Name or service not known")

        with (
            patch("pagemap.server.socket.getaddrinfo", _fail),
            pytest.raises(ValueError, match="DNS resolution failed"),
        ):
            await _resolve_dns("nonexistent.invalid")

    @pytest.mark.asyncio
    async def test_timeout_raises_valueerror(self):
        """DNS resolution that exceeds timeout raises ValueError."""
        import asyncio

        async def _slow_thread(fn):
            await asyncio.sleep(10)
            return fn()

        with (
            patch("pagemap.server.asyncio.to_thread", side_effect=_slow_thread),
            patch("pagemap.server.DNS_RESOLVE_TIMEOUT_SECONDS", 0.01),
            pytest.raises(ValueError, match="timed out"),
        ):
            await _resolve_dns("slow.example.com")


# ── TestValidateResolvedIps ──────────────────────────────────────────


class TestValidateResolvedIps:
    """Tests for _validate_resolved_ips IP checking."""

    def test_public_ip_passes(self):
        assert _validate_resolved_ips(["93.184.216.34"], "example.com") is None

    def test_loopback_blocked(self):
        err = _validate_resolved_ips(["127.0.0.1"], "evil.com")
        assert err is not None
        assert "private" in err.lower() or "rebinding" in err.lower()
        assert "evil.com" in err

    def test_private_10_blocked(self):
        err = _validate_resolved_ips(["10.0.0.1"], "evil.com")
        assert err is not None

    def test_private_172_blocked(self):
        err = _validate_resolved_ips(["172.16.0.1"], "evil.com")
        assert err is not None

    def test_private_192_168_blocked(self):
        err = _validate_resolved_ips(["192.168.1.1"], "evil.com")
        assert err is not None

    def test_link_local_blocked(self):
        err = _validate_resolved_ips(["169.254.169.254"], "evil.com")
        assert err is not None

    def test_cgnat_blocked(self):
        err = _validate_resolved_ips(["100.64.0.1"], "evil.com")
        assert err is not None

    def test_ipv6_loopback_blocked(self):
        err = _validate_resolved_ips(["::1"], "evil.com")
        assert err is not None

    def test_ipv6_private_blocked(self):
        err = _validate_resolved_ips(["fc00::1"], "evil.com")
        assert err is not None

    def test_ipv6_link_local_blocked(self):
        err = _validate_resolved_ips(["fe80::1"], "evil.com")
        assert err is not None

    def test_mixed_public_and_private_blocked(self):
        """If any resolved IP is private, entire hostname is blocked."""
        err = _validate_resolved_ips(["93.184.216.34", "127.0.0.1"], "evil.com")
        assert err is not None

    def test_empty_ip_list_blocked(self):
        err = _validate_resolved_ips([], "empty.com")
        assert err is not None
        assert "no addresses" in err.lower()

    def test_is_global_fallback_blocks_reserved(self):
        """is_global catches ranges not in explicit _PRIVATE_NETWORKS list."""
        # 192.0.0.1 is in 192.0.0.0/24 (IETF Protocol Assignments)
        # which is not in _PRIVATE_NETWORKS but is_global returns False
        err = _validate_resolved_ips(["192.0.0.1"], "reserved.com")
        assert err is not None
        assert "non-global" in err.lower()

    def test_multiple_public_ips_pass(self):
        assert _validate_resolved_ips(["93.184.216.34", "8.8.8.8"], "good.com") is None

    def test_ipv4_mapped_ipv6_blocked(self):
        err = _validate_resolved_ips(["::ffff:127.0.0.1"], "evil.com")
        assert err is not None

    def test_zero_network_blocked(self):
        err = _validate_resolved_ips(["0.0.0.0"], "evil.com")
        assert err is not None


# ── TestValidateUrlWithDns ───────────────────────────────────────────


class TestValidateUrlWithDns:
    """Tests for _validate_url_with_dns combined validation."""

    @pytest.mark.asyncio
    async def test_scheme_blocked_before_dns(self):
        """Invalid scheme is caught without DNS resolution."""
        with patch("pagemap.server._resolve_dns") as mock_dns:
            err = await _validate_url_with_dns("ftp://evil.com/file")
        assert err is not None
        assert "scheme" in err.lower()
        mock_dns.assert_not_called()

    @pytest.mark.asyncio
    async def test_ip_literal_skips_dns(self):
        """IP literal URLs skip DNS resolution (already validated by _validate_url)."""
        with patch("pagemap.server._resolve_dns") as mock_dns:
            result = await _validate_url_with_dns("http://8.8.8.8/")
        assert result is None
        mock_dns.assert_not_called()

    @pytest.mark.asyncio
    async def test_private_ip_literal_blocked_without_dns(self):
        with patch("pagemap.server._resolve_dns") as mock_dns:
            err = await _validate_url_with_dns("http://127.0.0.1/")
        assert err is not None
        mock_dns.assert_not_called()

    @pytest.mark.asyncio
    async def test_domain_resolving_to_private_blocked(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["10.0.0.1"]),
        ):
            err = await _validate_url_with_dns("http://evil.com/")
        assert err is not None
        assert "rebinding" in err.lower()

    @pytest.mark.asyncio
    async def test_domain_resolving_to_public_passes(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["93.184.216.34"]),
        ):
            result = await _validate_url_with_dns("https://example.com/")
        assert result is None

    @pytest.mark.asyncio
    async def test_dns_timeout_returns_error(self):
        def _fail(*args, **kw):
            raise socket.gaierror("timeout")

        with patch("pagemap.server.socket.getaddrinfo", _fail):
            err = await _validate_url_with_dns("http://slow.example.com/")
        assert err is not None
        assert "failed" in err.lower()

    @pytest.mark.asyncio
    async def test_octal_ip_skips_dns(self):
        """Octal IP formats are already validated by _validate_url, skip DNS."""
        with patch("pagemap.server._resolve_dns") as mock_dns:
            err = await _validate_url_with_dns("http://0177.0.0.1/")
        assert err is not None  # Blocked as private IP
        mock_dns.assert_not_called()

    @pytest.mark.asyncio
    async def test_hex_ip_skips_dns(self):
        with patch("pagemap.server._resolve_dns") as mock_dns:
            err = await _validate_url_with_dns("http://0x7f000001/")
        assert err is not None
        mock_dns.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_host_before_dns(self):
        """Blocked hostnames (localhost etc.) caught before DNS."""
        with patch("pagemap.server._resolve_dns") as mock_dns:
            err = await _validate_url_with_dns("http://localhost/")
        assert err is not None
        mock_dns.assert_not_called()

    @pytest.mark.asyncio
    async def test_domain_to_metadata_ip_blocked(self):
        """Domain resolving to cloud metadata IP (169.254.169.254) is blocked."""
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["169.254.169.254"]),
        ):
            err = await _validate_url_with_dns("http://metadata-alias.evil.com/")
        assert err is not None

    @pytest.mark.asyncio
    async def test_domain_to_ipv6_loopback_blocked(self):
        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["::1"]),
        ):
            err = await _validate_url_with_dns("http://ipv6-evil.com/")
        assert err is not None


# ── TestGetPageMapDnsValidation ──────────────────────────────────────


class TestGetPageMapDnsValidation:
    """Integration tests for get_page_map with DNS validation."""

    @pytest.mark.asyncio
    async def test_pre_nav_dns_rebinding_blocked(self):
        """get_page_map blocks URL whose domain resolves to private IP."""
        from pagemap.server import get_page_map

        with patch(
            "pagemap.server.socket.getaddrinfo",
            _fake_getaddrinfo_factory(["10.0.0.1"]),
        ):
            result = await get_page_map(url="http://evil.com/steal")

        assert "Error" in result
        assert "rebinding" in result.lower() or "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_pre_nav_public_domain_proceeds(self):
        """get_page_map allows URL whose domain resolves to public IP."""
        from pagemap.server import get_page_map

        mock_session = _make_mock_session("https://example.com")
        page_map = _make_page_map("https://example.com")

        async def _fake_build(*a, **kw):
            return page_map

        with (
            patch(
                "pagemap.server.socket.getaddrinfo",
                _fake_getaddrinfo_factory(["93.184.216.34"]),
            ),
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.page_map_builder.build_page_map_live",
                side_effect=_fake_build,
            ),
            patch("pagemap.serializer.to_agent_prompt", return_value="page map output"),
        ):
            result = await get_page_map(url="https://example.com")

        # Should proceed without SSRF block
        assert "Error" not in result or "rebinding" not in result.lower()

    @pytest.mark.asyncio
    async def test_post_nav_redirect_to_private_blocked(self):
        """Post-navigation check catches redirect to domain resolving to private IP."""
        from pagemap.server import get_page_map

        # First call (pre-nav) resolves to public, second (post-nav) resolves to private
        call_count = 0

        def _switching_getaddrinfo(*args, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return _fake_getaddrinfo_factory(["93.184.216.34"])(*args, **kw)
            else:
                return _fake_getaddrinfo_factory(["10.0.0.1"])(*args, **kw)

        mock_session = _make_mock_session("http://internal.evil.com/admin")
        page_map = _make_page_map("http://internal.evil.com/admin")

        async def _fake_build(*a, **kw):
            return page_map

        with (
            patch("pagemap.server.socket.getaddrinfo", _switching_getaddrinfo),
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.page_map_builder.build_page_map_live",
                side_effect=_fake_build,
            ),
            patch("pagemap.serializer.to_agent_prompt", return_value="output"),
        ):
            result = await get_page_map(url="http://redirect.evil.com/")

        assert "Error" in result
        assert "Redirect" in result or "rebinding" in result.lower()


# ── TestExecuteActionSsrfCheck ───────────────────────────────────────


class TestExecuteActionSsrfCheck:
    """Tests for execute_action SSRF check on post-action navigation."""

    @pytest.mark.asyncio
    async def test_click_navigates_to_private_ip_blocked(self):
        """Click that navigates to private IP → blocked, about:blank, page_map cleared."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="http://127.0.0.1:8080/admin")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Error" in result
        assert "blocked" in result.lower()
        # Should navigate to about:blank
        mock_session.page.goto.assert_called_once_with("about:blank")
        # Page map should be cleared
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_click_navigates_to_metadata_blocked(self):
        """Click navigating to cloud metadata → blocked."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="http://169.254.169.254/latest/meta-data/")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Error" in result
        assert "blocked" in result.lower()
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_click_navigates_to_dns_rebinding_blocked(self):
        """Click navigating to domain that resolves to private IP → blocked."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="http://evil.internal.com/steal")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.socket.getaddrinfo",
                _fake_getaddrinfo_factory(["10.0.0.1"]),
            ),
        ):
            result = await execute_action(ref=1, action="click")

        assert "Error" in result
        assert "blocked" in result.lower() or "rebinding" in result.lower()
        mock_session.page.goto.assert_called_once_with("about:blank")
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_click_navigates_to_safe_url_passes(self):
        """Click navigating to safe public URL → normal stale ref behavior."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com/page2")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch(
                "pagemap.server.socket.getaddrinfo",
                _fake_getaddrinfo_factory(["93.184.216.34"]),
            ),
        ):
            result = await execute_action(ref=1, action="click")

        assert "Clicked [1]" in result
        assert "Page navigated" in result
        # about:blank NOT called
        mock_session.page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_click_same_url_no_ssrf_check(self):
        """Click without navigation → no SSRF check at all."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="https://example.com")

        with (
            patch("pagemap.server._get_session", return_value=mock_session),
            patch("pagemap.server._validate_url_with_dns") as mock_dns_check,
        ):
            result = await execute_action(ref=1, action="click")

        assert "Clicked [1]" in result
        assert "Page navigated" not in result
        mock_dns_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_about_blank_goto_fails_gracefully(self):
        """If about:blank navigation fails, error is still returned."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="http://127.0.0.1/")
        mock_session.page.goto = AsyncMock(side_effect=Exception("browser crashed"))

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="click")

        assert "Error" in result
        assert "blocked" in result.lower()
        assert srv._last_page_map is None

    @pytest.mark.asyncio
    async def test_press_key_navigates_to_private_blocked(self):
        """press_key (Enter) that navigates to private IP → blocked."""
        import pagemap.server as srv

        srv._last_page_map = _make_page_map("https://example.com")
        mock_session = _make_mock_session(current_url="http://192.168.1.1/router")

        with patch("pagemap.server._get_session", return_value=mock_session):
            result = await execute_action(ref=1, action="press_key", value="Enter")

        assert "Error" in result
        assert "blocked" in result.lower()
        assert srv._last_page_map is None


# ── TestSsrfRouteGuard ──────────────────────────────────────────────


class TestSsrfRouteGuard:
    """Tests for the browser context route guard (sync URL validation)."""

    @pytest.mark.asyncio
    async def test_route_guard_blocks_document_to_private_ip(self):
        """Route guard blocks document navigation to private IP."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        # Mock the context
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)

        # Verify route was registered
        mock_context.route.assert_called_once()
        pattern, handler = mock_context.route.call_args[0]
        assert pattern == "**/*"

        # Simulate a document request to private IP
        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://127.0.0.1:8080/admin"
        mock_request.resource_type = "document"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_route_guard_allows_document_to_public(self):
        """Route guard allows document navigation to public URL."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "https://www.google.com/"
        mock_request.resource_type = "document"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.continue_.assert_called_once()
        mock_route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_guard_skips_image_requests(self):
        """Route guard does NOT validate image requests (performance)."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        # Image request to private IP — should be allowed (not validated)
        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://127.0.0.1/logo.png"
        mock_request.resource_type = "image"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.continue_.assert_called_once()
        mock_route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_guard_skips_stylesheet_requests(self):
        """Route guard does NOT validate stylesheet requests."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://10.0.0.1/style.css"
        mock_request.resource_type = "stylesheet"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.continue_.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_guard_blocks_subdocument_to_private(self):
        """Route guard blocks iframe (subdocument) to private IP."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://192.168.1.1/router-panel"
        mock_request.resource_type = "subdocument"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_route_guard_blocks_metadata_endpoint(self):
        """Route guard blocks navigation to cloud metadata."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://169.254.169.254/latest/meta-data/"
        mock_request.resource_type = "document"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_route_guard_blocks_localhost(self):
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://localhost:3000/admin"
        mock_request.resource_type = "document"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_route_guard_skips_script_requests(self):
        """Route guard does NOT validate script requests."""
        from pagemap.browser_session import BrowserSession

        session = BrowserSession.__new__(BrowserSession)
        mock_context = MagicMock()
        mock_context.route = AsyncMock()
        session._context = mock_context

        await session.install_ssrf_route_guard(_validate_url)
        _, handler = mock_context.route.call_args[0]

        mock_route = AsyncMock()
        mock_request = MagicMock()
        mock_request.url = "http://10.0.0.1/script.js"
        mock_request.resource_type = "script"
        mock_route.request = mock_request

        await handler(mock_route)
        mock_route.continue_.assert_called_once()


# ── TestRouteGuardInstallation ───────────────────────────────────────


class TestRouteGuardInstallation:
    """Test that route guard is installed during session creation."""

    @pytest.mark.asyncio
    async def test_get_session_installs_route_guard(self):
        """_get_session installs route guard on new session."""
        import pagemap.server as srv

        srv._session = None

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.install_ssrf_route_guard = AsyncMock()
        mock_session.is_alive = AsyncMock(return_value=True)

        with (
            patch("pagemap.server.BrowserSession", return_value=mock_session),
        ):
            session = await srv._get_session()

        mock_session.install_ssrf_route_guard.assert_called_once_with(_validate_url)
        assert session is mock_session

        # Cleanup
        srv._session = None
