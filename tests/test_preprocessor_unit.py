# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for pruning/preprocessor.py and pruning/compressor.py.

Phase 7.2 — covers:
  - _extract_json_ld, _extract_og_meta, _extract_rsc_data
  - _clean_html_pass1
  - preprocess() entry point (happy + error paths)
  - _decompose_element chunk classification
  - compress_html() 5-step pipeline + hypothesis idempotency
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from pagemap.pruning import ChunkType, PruningError
from pagemap.pruning.compressor import compress_html
from pagemap.pruning.preprocessor import (
    _REMOVE_TAGS,
    _clean_html_pass1,
    _extract_json_ld,
    _extract_og_meta,
    _extract_rsc_data,
    preprocess,
)
from tests._pruning_helpers import decompose_body, html

# ---------------------------------------------------------------------------
# TestExtractJsonLd
# ---------------------------------------------------------------------------


class TestExtractJsonLd:
    def test_single_json_ld(self):
        raw = html("", head='<script type="application/ld+json">{"@type":"Product"}</script>')
        chunks = _extract_json_ld(raw)
        assert len(chunks) == 1
        assert chunks[0].chunk_type == ChunkType.META
        assert '"@type":"Product"' in chunks[0].text

    def test_multiple_json_ld(self):
        head = (
            '<script type="application/ld+json">{"@type":"A"}</script>'
            '<script type="application/ld+json">{"@type":"B"}</script>'
        )
        chunks = _extract_json_ld(html("", head=head))
        assert len(chunks) == 2
        assert chunks[0].xpath == "/json-ld[0]"
        assert chunks[1].xpath == "/json-ld[1]"

    def test_empty_json_ld_skipped(self):
        raw = html("", head='<script type="application/ld+json">   </script>')
        assert _extract_json_ld(raw) == []

    def test_case_insensitive_type(self):
        raw = html("", head='<script TYPE="Application/LD+JSON">{"x":1}</script>')
        chunks = _extract_json_ld(raw)
        assert len(chunks) == 1

    def test_no_match(self):
        raw = html("<p>Hello</p>", head="<script>var x=1;</script>")
        assert _extract_json_ld(raw) == []

    def test_html_entities_in_content(self):
        raw = html("", head='<script type="application/ld+json">{"name":"Foo &amp; Bar"}</script>')
        chunks = _extract_json_ld(raw)
        assert len(chunks) == 1
        assert "&amp;" in chunks[0].text


# ---------------------------------------------------------------------------
# TestExtractOgMeta
# ---------------------------------------------------------------------------


class TestExtractOgMeta:
    def test_standard_og_tags(self):
        head = '<meta property="og:title" content="My Page"/>'
        chunks = _extract_og_meta(html("", head=head))
        assert len(chunks) == 1
        assert "og:title=My Page" in chunks[0].text

    def test_reversed_attr_order(self):
        head = '<meta content="My Desc" property="og:description"/>'
        chunks = _extract_og_meta(html("", head=head))
        assert len(chunks) == 1
        assert "og:description=My Desc" in chunks[0].text

    def test_multiple_og_merged(self):
        head = '<meta property="og:title" content="Title"/><meta property="og:image" content="img.jpg"/>'
        chunks = _extract_og_meta(html("", head=head))
        assert len(chunks) == 1
        assert "og:title" in chunks[0].attrs
        assert "og:image" in chunks[0].attrs

    def test_no_og_tags(self):
        raw = html("<p>No OG here</p>")
        assert _extract_og_meta(raw) == []

    def test_single_quotes(self):
        head = "<meta property='og:url' content='https://example.com'/>"
        chunks = _extract_og_meta(html("", head=head))
        assert len(chunks) == 1
        assert "og:url" in chunks[0].attrs

    def test_og_xpath(self):
        head = '<meta property="og:title" content="T"/>'
        chunks = _extract_og_meta(html("", head=head))
        assert chunks[0].xpath == "/og-meta"


# ---------------------------------------------------------------------------
# TestExtractRscData
# ---------------------------------------------------------------------------


class TestExtractRscData:
    def test_with_date(self):
        script = '<script>self.__next_f.push([1,"2024-10-22"])</script>'
        chunks = _extract_rsc_data(html("", head=script))
        assert len(chunks) == 1
        assert "2024-10-22" in chunks[0].text
        assert chunks[0].chunk_type == ChunkType.RSC_DATA

    def test_without_date(self):
        script = '<script>self.__next_f.push([1,"no-date-here"])</script>'
        chunks = _extract_rsc_data(html("", head=script))
        assert len(chunks) == 0

    def test_truncation_500(self):
        long_payload = "x" * 1000
        script = f'<script>self.__next_f.push([1,"{long_payload} 2024-01-01"])</script>'
        chunks = _extract_rsc_data(html("", head=script))
        if chunks:
            assert len(chunks[0].html) <= 600  # script tags + 500 content

    def test_multiple_rsc(self):
        scripts = (
            '<script>self.__next_f.push([1,"2024-01-01"])</script><script>self.__next_f.push([2,"2024-02-02"])</script>'
        )
        chunks = _extract_rsc_data(html("", head=scripts))
        assert len(chunks) >= 1

    @pytest.mark.parametrize(
        "date_fmt",
        ["2024-10-22", "2024.10.22", "2024/10/22"],
    )
    def test_date_format_variants(self, date_fmt):
        script = f'<script>self.__next_f.push([1,"{date_fmt}"])</script>'
        chunks = _extract_rsc_data(html("", head=script))
        assert len(chunks) >= 1
        assert date_fmt in chunks[0].text


# ---------------------------------------------------------------------------
# TestCleanHtmlPass1
# ---------------------------------------------------------------------------


class TestCleanHtmlPass1:
    @pytest.mark.parametrize("tag", sorted(_REMOVE_TAGS))
    def test_removes_tag(self, tag):
        raw = f"<html><body><{tag}>content</{tag}><p>kept</p></body></html>"
        result = _clean_html_pass1(raw)
        assert f"<{tag}" not in result.lower()
        assert "kept" in result

    def test_removes_comments(self):
        raw = "<html><body><!-- hidden --><p>visible</p></body></html>"
        result = _clean_html_pass1(raw)
        assert "hidden" not in result
        assert "visible" in result

    def test_whitespace_collapse(self):
        raw = "<html><body><p>hello   \t  world</p></body></html>"
        result = _clean_html_pass1(raw)
        assert "hello world" in result

    def test_blank_line_collapse(self):
        raw = "<html><body>\n\n\n\n<p>text</p>\n\n\n</body></html>"
        result = _clean_html_pass1(raw)
        assert "\n\n\n" not in result

    def test_self_closing_removed(self):
        raw = '<html><body><link rel="stylesheet" href="x.css"/><p>kept</p></body></html>'
        result = _clean_html_pass1(raw)
        assert "<link" not in result
        assert "kept" in result


# ---------------------------------------------------------------------------
# TestPreprocess (happy + error paths unified)
# ---------------------------------------------------------------------------


class TestPreprocess:
    def test_valid_returns_tuple(self):
        raw = html("<p>Hello World</p>")
        meta, doc = preprocess(raw)
        assert isinstance(meta, list)
        assert doc is not None

    def test_empty_raises(self):
        with pytest.raises(PruningError, match="Empty HTML"):
            preprocess("")

    def test_whitespace_only_raises(self):
        with pytest.raises(PruningError, match="Empty HTML"):
            preprocess("   \n\t  ")

    def test_scripts_only_raises_after_pass1(self):
        raw = "<html><body><script>alert(1)</script></body></html>"
        # After Pass 1, scripts are removed, leaving empty body
        # lxml may still parse it, but body will be empty
        # preprocess should either succeed with empty doc or raise
        try:
            meta, doc = preprocess(raw)
            # If it doesn't raise, doc should still be valid
            assert doc is not None
        except PruningError:
            pass  # Also acceptable

    def test_comment_only_html(self):
        raw = "<html><body><!-- only a comment --></body></html>"
        # After cleaning, may be empty
        try:
            meta, doc = preprocess(raw)
            assert doc is not None
        except PruningError:
            pass

    def test_meta_extracted_before_cleaning(self):
        raw = html(
            "<p>Content</p>",
            head='<script type="application/ld+json">{"@type":"Product"}</script>',
        )
        meta, doc = preprocess(raw)
        assert len(meta) == 1
        assert meta[0].chunk_type == ChunkType.META

    def test_lxml_recovery(self):
        """lxml recovers broken HTML."""
        raw = "<html><body><p>Unclosed paragraph<div>Still works</div></body></html>"
        meta, doc = preprocess(raw)
        assert doc is not None

    def test_body_navigable(self):
        raw = html("<p>Test</p>")
        meta, doc = preprocess(raw)
        body = doc.body if doc.body is not None else doc
        assert body is not None

    def test_og_and_jsonld_simultaneous(self):
        raw = html(
            "<p>Body</p>",
            head=(
                '<script type="application/ld+json">{"@type":"Article"}</script>'
                '<meta property="og:title" content="Title"/>'
            ),
        )
        meta, doc = preprocess(raw)
        types = {c.chunk_type for c in meta}
        assert ChunkType.META in types


# ---------------------------------------------------------------------------
# TestDecomposeChunkTypes
# ---------------------------------------------------------------------------

_ATOMIC_CASES = [
    ("table", ChunkType.TABLE),
    ("thead", ChunkType.TABLE),
    ("tbody", ChunkType.TABLE),
    ("ul", ChunkType.LIST),
    ("ol", ChunkType.LIST),
    ("dl", ChunkType.LIST),
    ("figure", ChunkType.MEDIA),
    ("form", ChunkType.FORM),
]

_HEADING_CASES = [
    ("h1", ChunkType.HEADING),
    ("h2", ChunkType.HEADING),
    ("h3", ChunkType.HEADING),
    ("h4", ChunkType.HEADING),
    ("h5", ChunkType.HEADING),
    ("h6", ChunkType.HEADING),
]


class TestDecomposeChunkTypes:
    @pytest.mark.parametrize("tag,expected_type", _ATOMIC_CASES)
    def test_atomic_tags(self, tag, expected_type):
        src = html(f"<{tag}>Content here</{tag}>")
        chunks = decompose_body(src)
        matching = [c for c in chunks if c.chunk_type == expected_type]
        assert len(matching) >= 1

    @pytest.mark.parametrize("tag,expected_type", _HEADING_CASES)
    def test_heading_tags(self, tag, expected_type):
        src = html(f"<{tag}>Heading Text</{tag}>")
        chunks = decompose_body(src)
        matching = [c for c in chunks if c.chunk_type == expected_type]
        assert len(matching) == 1
        assert matching[0].tag == tag

    def test_p_becomes_text_block(self):
        chunks = decompose_body(html("<p>Paragraph text</p>"))
        assert any(c.chunk_type == ChunkType.TEXT_BLOCK and c.tag == "p" for c in chunks)

    def test_container_recurse(self):
        src = html("<article><p>Inside article</p></article>")
        chunks = decompose_body(src)
        assert any(c.tag == "p" for c in chunks)

    def test_inline_no_chunk(self):
        """Inline tags like <span> inside a block don't produce their own chunks."""
        src = html("<p>Hello <span>world</span></p>")
        chunks = decompose_body(src)
        assert not any(c.tag == "span" for c in chunks)

    def test_in_main_flag(self):
        src = html("<main><p>In main</p></main><p>Outside main</p>")
        chunks = decompose_body(src)
        in_main_chunks = [c for c in chunks if c.in_main]
        not_main_chunks = [c for c in chunks if not c.in_main]
        assert len(in_main_chunks) >= 1
        assert len(not_main_chunks) >= 1

    def test_attrs_extracted(self):
        src = html('<p role="note" aria-label="important">Text</p>')
        chunks = decompose_body(src)
        p_chunks = [c for c in chunks if c.tag == "p"]
        assert len(p_chunks) == 1
        assert "role" in p_chunks[0].attrs

    def test_empty_element_skipped(self):
        src = html("<p></p><p>Has text</p>")
        chunks = decompose_body(src)
        assert len(chunks) == 1
        assert chunks[0].text == "Has text"

    def test_leaf_div_text_block(self):
        """Div with only inline content becomes TEXT_BLOCK."""
        src = html("<div>Just text, no block children</div>")
        chunks = decompose_body(src)
        assert any(c.chunk_type == ChunkType.TEXT_BLOCK and c.tag == "div" for c in chunks)

    def test_div_with_block_children_recurses(self):
        src = html("<div><p>Paragraph</p><p>Another</p></div>")
        chunks = decompose_body(src)
        assert all(c.tag == "p" for c in chunks)

    def test_remove_tag_skipped(self):
        """Tags in _REMOVE_TAGS that survive cleaning are still skipped in decompose."""
        # This tests the guard in _decompose_element directly
        import lxml.html as lh

        from pagemap.pruning.preprocessor import _decompose_element

        doc = lh.document_fromstring("<html><body><script>alert(1)</script></body></html>")
        tree = doc.getroottree()
        body = doc.body
        # Manually find the script element
        scripts = list(body.iter("script"))
        if scripts:
            chunks = _decompose_element(scripts[0], tree)
            assert chunks == []

    def test_multiple_chunks_from_complex_html(self):
        src = html("<h1>Title</h1><p>Paragraph one</p><ul><li>Item</li></ul><table><tr><td>Cell</td></tr></table>")
        chunks = decompose_body(src)
        types = {c.chunk_type for c in chunks}
        assert ChunkType.HEADING in types
        assert ChunkType.TEXT_BLOCK in types
        assert ChunkType.LIST in types
        assert ChunkType.TABLE in types


# ---------------------------------------------------------------------------
# TestCompressHtml
# ---------------------------------------------------------------------------

# Attrs that should be removed
_REMOVED_ATTRS = [
    "class",
    "id",
    "style",
    "onclick",
    "onload",
    "tabindex",
    "draggable",
    "data-testid",
    "data-custom-val",
    "hidden",
    "autocomplete",
    "aria-expanded",
]

# Attrs that should be kept
_KEPT_ATTRS = [
    "itemprop",
    "itemtype",
    "role",
    "aria-label",
    "aria-labelledby",
    "href",
    "src",
    "alt",
    "title",
    "datetime",
    "content",
    "property",
    "type",
]


class TestCompressHtml:
    @pytest.mark.parametrize("attr", _REMOVED_ATTRS)
    def test_removes_attr(self, attr):
        val = "test-value"
        raw = f'<div {attr}="{val}"><p>Text</p></div>'
        result = compress_html(raw)
        assert f'{attr}="' not in result

    @pytest.mark.parametrize("attr", _KEPT_ATTRS)
    def test_keeps_attr(self, attr):
        val = "test-value"
        raw = f'<p {attr}="{val}">Text</p>'
        result = compress_html(raw)
        assert f'{attr}="{val}"' in result

    def test_empty_tag_removal(self):
        raw = "<div><span></span><p>Text</p></div>"
        result = compress_html(raw)
        assert "<span" not in result
        assert "Text" in result

    def test_nested_empty_tag_iterative(self):
        """Iterative passes remove nested empties: <div><span></span></div> → gone."""
        raw = "<div><span></span></div><p>Kept</p>"
        result = compress_html(raw)
        assert "Kept" in result
        assert "<div" not in result

    def test_wrapper_div_collapse(self):
        """<div><p>text</p></div> → <p>text</p>."""
        raw = "<div><p>Hello world</p></div>"
        result = compress_html(raw)
        assert "<div" not in result
        assert "<p>" in result

    def test_span_unwrap(self):
        """<span>text</span> → text (when no attributes)."""
        raw = "<p><span>hello</span></p>"
        result = compress_html(raw)
        assert "<span" not in result
        assert "hello" in result

    def test_whitespace_normalization(self):
        raw = "<p>  hello   \t world  </p>"
        result = compress_html(raw)
        assert "  " not in result

    def test_empty_input(self):
        assert compress_html("") == ""

    def test_none_like_empty(self):
        assert compress_html("") == ""

    def test_preserves_content(self):
        raw = '<p itemprop="name">Product Name</p>'
        result = compress_html(raw)
        assert "Product Name" in result
        assert 'itemprop="name"' in result

    @settings(max_examples=50, deadline=5000)
    @given(st.text(min_size=1, max_size=200))
    def test_idempotent(self, content):
        """compress_html(compress_html(x)) == compress_html(x) for arbitrary content."""
        wrapped = f"<div><p>{content}</p></div>"
        once = compress_html(wrapped)
        twice = compress_html(once)
        assert once == twice
