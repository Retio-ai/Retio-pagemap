"""Backward-compat shim — import from pagemap.core.ecommerce.cookie_patterns instead."""

from pagemap.core.ecommerce.cookie_patterns import CookieConsentPattern, detect_cookie_provider  # noqa: F401

__all__ = ["CookieConsentPattern", "detect_cookie_provider"]
