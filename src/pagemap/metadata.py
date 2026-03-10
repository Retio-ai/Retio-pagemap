"""Backward-compat shim — import from pagemap.core.metadata instead."""

from pagemap.core.metadata import (  # noqa: F401
    _extract_image_url,
    _extract_price_from_html,
    _extract_price_from_offers,
    _extract_video_meta_from_dom,
    _find_type_in_jsonld,
    _parse_h1,
    _parse_json_ld_itemlist,
    _parse_json_ld_product,
    _parse_jsonld_chunks,
    _to_float,
    _to_int,
    extract_metadata,
)

__all__ = ["extract_metadata"]
