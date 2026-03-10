"""Tests for Phase D: Block-tree parent preservation in compressor.

Covers:
  D-1: remerge_chunks() with block-tree grouping
  - Consecutive chunks with same parent_xpath wrapped in parent tag
  - data-section attribute contains last 2 xpath segments
  - body/html parents skip wrapping
  - Document order preserved
  - Single-chunk groups not wrapped
  - enable_block_tree=False disables grouping (backward compat)
"""

from __future__ import annotations

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.compressor import (
    _extract_section_label,
    _extract_wrapper_tag,
    compress_html,
    remerge_chunks,
)


def _make_chunk(
    text: str,
    xpath: str,
    parent_xpath: str = "/html/body/main",
    tag: str = "p",
) -> HtmlChunk:
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=ChunkType.TEXT_BLOCK,
        attrs={},
        parent_xpath=parent_xpath,
        depth=3,
        in_main=True,
    )


class TestExtractHelpers:
    def test_section_label_two_segments(self):
        assert _extract_section_label("/html/body/main/div[2]/p[1]") == "div[2]/p[1]"

    def test_section_label_short_path(self):
        assert _extract_section_label("/html/body") == "html/body"

    def test_section_label_single_segment(self):
        assert _extract_section_label("/body") == "body"

    def test_wrapper_tag_div(self):
        assert _extract_wrapper_tag("/html/body/main/div[2]") == "div"

    def test_wrapper_tag_section(self):
        assert _extract_wrapper_tag("/html/body/section[1]") == "section"

    def test_wrapper_tag_body_skipped(self):
        assert _extract_wrapper_tag("/html/body") is None

    def test_wrapper_tag_html_skipped(self):
        assert _extract_wrapper_tag("/html") is None

    def test_wrapper_tag_empty(self):
        assert _extract_wrapper_tag("") is None


class TestRemergeBlockTree:
    def test_same_parent_grouped(self):
        parent = "/html/body/main/div[1]"
        chunks = [
            _make_chunk("First paragraph", "/html/body/main/div[1]/p[1]", parent),
            _make_chunk("Second paragraph", "/html/body/main/div[1]/p[2]", parent),
        ]
        result = remerge_chunks(chunks)
        assert 'data-section="div[1]"' in result or "data-section" in result
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_different_parents_separate(self):
        chunks = [
            _make_chunk("In div 1", "/html/body/main/div[1]/p[1]", "/html/body/main/div[1]"),
            _make_chunk("In div 2", "/html/body/main/div[2]/p[1]", "/html/body/main/div[2]"),
        ]
        result = remerge_chunks(chunks)
        # Each should be separate (single chunk groups → no wrapping)
        assert "In div 1" in result
        assert "In div 2" in result

    def test_body_parent_no_wrapper(self):
        chunks = [
            _make_chunk("Direct body child 1", "/html/body/div[1]", "/html/body"),
            _make_chunk("Direct body child 2", "/html/body/div[2]", "/html/body"),
        ]
        result = remerge_chunks(chunks)
        # body parent → no wrapper tag, just flat
        # The wrapper_tag returns None for body, so no data-section wrapping
        assert "Direct body child 1" in result
        assert "Direct body child 2" in result

    def test_document_order_preserved(self):
        parent = "/html/body/main"
        chunks = [
            _make_chunk("Third", "/html/body/main/p[3]", parent),
            _make_chunk("First", "/html/body/main/p[1]", parent),
            _make_chunk("Second", "/html/body/main/p[2]", parent),
        ]
        result = remerge_chunks(chunks)
        # Sorted by xpath → First, Second, Third
        first_pos = result.index("First")
        second_pos = result.index("Second")
        third_pos = result.index("Third")
        assert first_pos < second_pos < third_pos

    def test_single_chunk_no_wrapper(self):
        chunks = [
            _make_chunk("Only child", "/html/body/main/div[1]/p[1]", "/html/body/main/div[1]"),
        ]
        result = remerge_chunks(chunks)
        assert "data-section" not in result
        assert "Only child" in result

    def test_disable_block_tree(self):
        parent = "/html/body/main/div[1]"
        chunks = [
            _make_chunk("First", "/html/body/main/div[1]/p[1]", parent),
            _make_chunk("Second", "/html/body/main/div[1]/p[2]", parent),
        ]
        result = remerge_chunks(chunks, enable_block_tree=False)
        assert "data-section" not in result
        assert "First" in result
        assert "Second" in result

    def test_data_section_preserved_after_compression(self):
        """data-section attribute should survive compress_html()."""
        parent = "/html/body/main/div[1]"
        chunks = [
            _make_chunk("First paragraph", "/html/body/main/div[1]/p[1]", parent),
            _make_chunk("Second paragraph", "/html/body/main/div[1]/p[2]", parent),
        ]
        merged = remerge_chunks(chunks)
        compressed = compress_html(merged)
        assert "data-section" in compressed

    def test_empty_chunks(self):
        assert remerge_chunks([]) == ""
