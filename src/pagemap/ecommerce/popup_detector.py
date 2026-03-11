"""Backward-compat shim — import from pagemap.core.ecommerce.popup_detector instead."""

from pagemap.core.ecommerce.popup_detector import PopupOverlayResult, detect_popup_overlay  # noqa: F401

__all__ = ["PopupOverlayResult", "detect_popup_overlay"]
