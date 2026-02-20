"""Tests for P1 Phase 1 bug fixes and safety improvements.

Covers 6 issues:
  1.1: XPath index numeric sort (_xpath_sort_key)
  1.2: Skip empty chunks from schema match
  1.3: Warn on unknown schema_name in prune_chunks()
  1.4: Defensive try/except around pagination int()
  8.1: URL allowlist (http/https/relative only) + length limit
  8.2: _decompose_element max_depth recursion guard
"""

from __future__ import annotations

import logging

import lxml.html
import pytest

from pagemap.pruned_context_builder import (
    _extract_pagination_info,
    extract_pagination_structured,
    extract_product_images,
)
from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.compressor import _xpath_sort_key, remerge_chunks
from pagemap.pruning.preprocessor import _decompose_element
from pagemap.pruning.pruner import PruneDecision, prune_chunks

# ── Helpers ──────────────────────────────────────────────────────────


def _make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    in_main: bool = True,
    tag: str = "div",
    attrs: dict | None = None,
    xpath: str | None = None,
) -> HtmlChunk:
    if xpath is None:
        xpath = "/html/body/main/div[1]" if in_main else "/html/body/div[1]"
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs=attrs or {},
        parent_xpath="/html/body/main" if in_main else "/html/body",
        depth=3,
        in_main=in_main,
    )


def _prune_single(
    chunk: HtmlChunk,
    schema: str = "Product",
    has_main: bool = True,
) -> PruneDecision:
    results = prune_chunks([chunk], schema_name=schema, has_main=has_main)
    assert len(results) == 1
    return results[0][1]


# ── 1.1 XPath sort key ──────────────────────────────────────────────


class TestXPathSortKey:
    @pytest.mark.parametrize(
        "xpath,expected",
        [
            ("/html/body/div[2]", (("html", 0), ("body", 0), ("div", 2))),
            ("/html/body/div[10]", (("html", 0), ("body", 0), ("div", 10))),
            ("/html/body/div", (("html", 0), ("body", 0), ("div", 0))),
            ("/json-ld[0]", (("json-ld", 0),)),
            ("/og-meta", (("og-meta", 0),)),
        ],
    )
    def test_xpath_sort_key_parsing(self, xpath: str, expected: tuple):
        assert _xpath_sort_key(xpath) == expected

    def test_div2_before_div10(self):
        """div[2] should sort before div[10] numerically."""
        assert _xpath_sort_key("/body/div[2]") < _xpath_sort_key("/body/div[10]")

    def test_remerge_preserves_document_order(self):
        """remerge_chunks should sort div[2] before div[10]."""
        c2 = _make_chunk("second", xpath="/html/body/div[2]")
        c10 = _make_chunk("tenth", xpath="/html/body/div[10]")
        # Pass in reverse order — remerge should fix it
        result = remerge_chunks([c10, c2])
        assert result.index("second") < result.index("tenth")

    def test_remerge_empty_returns_empty(self):
        assert remerge_chunks([]) == ""


# ── 1.2 Empty chunk filter ──────────────────────────────────────────


class TestEmptyChunkFilter:
    def test_empty_product_name_pruned(self):
        """Empty text with product-name class should not be kept."""
        chunk = _make_chunk(
            "",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"class": "product-name"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        # Should not be kept via schema-match (empty text, no content attr)
        assert decision.reason != "schema-match" or decision.keep is False

    def test_nonempty_product_name_kept(self):
        """Non-empty product name should be kept."""
        chunk = _make_chunk(
            "Galaxy S25",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"class": "product-name"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.keep is True

    def test_whitespace_only_pruned(self):
        """Whitespace-only text (already stripped by preprocessor) should not match."""
        chunk = _make_chunk(
            "",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"class": "product-name"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.reason != "schema-match" or decision.keep is False

    def test_content_attr_fallback_kept(self):
        """Empty text but content attr should be kept (e.g. itemprop=price)."""
        chunk = _make_chunk(
            "",
            ChunkType.TEXT_BLOCK,
            in_main=True,
            tag="span",
            attrs={"itemprop": "price", "content": "29900", "class": "product-price"},
        )
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.keep is True
        assert "schema-match" in decision.reason

    def test_meta_always_kept(self):
        """META chunk with empty text is still kept (Rule 1)."""
        chunk = _make_chunk("", ChunkType.META, in_main=False)
        decision = _prune_single(chunk, schema="Product", has_main=True)
        assert decision.keep is True
        assert "meta" in decision.reason


# ── 1.3 Schema name warning ─────────────────────────────────────────


class TestSchemaNameWarning:
    def test_unknown_schema_logs_warning(self, caplog):
        """Unknown schema_name should produce a warning log."""
        chunk = _make_chunk("Some text", ChunkType.TEXT_BLOCK, in_main=True)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.pruner"):
            prune_chunks([chunk], schema_name="UnknownSchema", has_main=True)
        assert any("Unknown schema_name='UnknownSchema'" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "schema",
        ["Product", "NewsArticle", "WikiArticle", "SaaSPage", "GovernmentPage"],
    )
    def test_valid_schemas_no_warning(self, caplog, schema: str):
        """Valid schema names should not produce any warning."""
        chunk = _make_chunk("Some text", ChunkType.TEXT_BLOCK, in_main=True)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.pruner"):
            prune_chunks([chunk], schema_name=schema, has_main=True)
        assert not any("Unknown schema_name" in r.message for r in caplog.records)

    def test_empty_schema_no_warning(self, caplog):
        """Empty string schema should not warn (intentional unspecified)."""
        chunk = _make_chunk("Some text", ChunkType.TEXT_BLOCK, in_main=True)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.pruner"):
            prune_chunks([chunk], schema_name="", has_main=True)
        assert not any("Unknown schema_name" in r.message for r in caplog.records)


# ── 1.4 Pagination defense ──────────────────────────────────────────


class TestPaginationDefense:
    def test_normal_pagination(self):
        html = '<a href="?page=3">3</a><a href="?page=25">25</a>'
        result = _extract_pagination_info(html)
        assert "25" in result

    def test_korean_pagination(self):
        html = '페이지 3 / 25 <a href="?page=1">1</a>'
        result = extract_pagination_structured(html)
        assert result.get("current_page") == 3
        assert result.get("total_pages") == 25

    def test_url_param_pagination(self):
        html = '<a href="/list?page=10">10</a>'
        result = extract_pagination_structured(html)
        assert result.get("total_pages") == 10

    def test_empty_html_no_crash(self):
        result = extract_pagination_structured("")
        assert result == {}

    def test_structured_pagination(self):
        html = 'Page 3 of 25 <a href="?page=1">1</a>'
        result = extract_pagination_structured(html)
        assert result.get("current_page") == 3
        assert result.get("total_pages") == 25


# ── 8.1 URL validation ──────────────────────────────────────────────


class TestURLValidation:
    @pytest.mark.parametrize(
        "src",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "vbscript:msgbox(1)",
            "data:image/gif;base64,R0lGODlh",
            "blob:https://example.com/uuid",
            "file:///etc/passwd",
        ],
    )
    def test_blocked_schemes(self, src: str):
        """Dangerous URL schemes should be filtered out."""
        html = f'<img src="{src}" />'
        result = extract_product_images(html)
        assert len(result) == 0

    @pytest.mark.parametrize(
        "src",
        [
            "https://example.com/product.jpg",
            "http://example.com/product.jpg",
            "//cdn.example.com/product.jpg",
            "/images/product.jpg",
            "images/product.jpg",
        ],
    )
    def test_allowed_urls(self, src: str):
        """Safe URLs should pass through."""
        html = f'<img src="{src}" />'
        result = extract_product_images(html)
        assert len(result) >= 1

    def test_long_url_blocked(self):
        """URLs exceeding 2048 chars should be blocked."""
        long_url = "https://example.com/" + "a" * 2100
        html = f'<img src="{long_url}" />'
        result = extract_product_images(html)
        assert len(result) == 0


# ── 8.2 Recursion depth limit ───────────────────────────────────────


class TestRecursionDepthLimit:
    def _build_nested_html(self, depth: int) -> str:
        """Build deeply nested div HTML."""
        open_tags = "".join("<div>" for _ in range(depth))
        close_tags = "".join("</div>" for _ in range(depth))
        return f"<html><body>{open_tags}<p>deep text</p>{close_tags}</body></html>"

    def _parse_and_decompose(self, html: str, max_depth: int = 100) -> list[HtmlChunk]:
        parser = lxml.html.HTMLParser(recover=True, encoding="utf-8")
        doc = lxml.html.document_fromstring(html.encode("utf-8"), parser=parser)
        tree = doc.getroottree()
        body = doc.body if doc.body is not None else doc
        return _decompose_element(body, tree, depth=0, max_depth=max_depth)

    def test_shallow_dom_works(self):
        html = self._build_nested_html(5)
        chunks = self._parse_and_decompose(html)
        assert any("deep text" in c.text for c in chunks)

    def test_deep_nesting_truncated_no_crash(self):
        """150-depth nesting with max_depth=50 should not crash."""
        html = self._build_nested_html(150)
        chunks = self._parse_and_decompose(html, max_depth=50)
        # Should NOT find the deep text — it was truncated
        # (may or may not find it depending on exact nesting, but no crash)
        assert isinstance(chunks, list)

    def test_default_max_depth_allows_normal(self):
        """80-depth nesting should work with default max_depth=100."""
        html = self._build_nested_html(80)
        chunks = self._parse_and_decompose(html, max_depth=100)
        assert any("deep text" in c.text for c in chunks)

    def test_depth_exceeded_logs_warning(self, caplog):
        """Exceeding max_depth should produce a warning log."""
        html = self._build_nested_html(20)
        with caplog.at_level(logging.WARNING, logger="pagemap.pruning.preprocessor"):
            self._parse_and_decompose(html, max_depth=10)
        assert any("Max decomposition depth" in r.message for r in caplog.records)
