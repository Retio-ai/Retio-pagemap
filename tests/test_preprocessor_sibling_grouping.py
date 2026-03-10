"""Tests for Phase B: Sibling context grouping in preprocessor.

Covers:
  - Small chunks with same parent_xpath merged
  - Budget exceeded → new group
  - Different parent_xpath → separate groups
  - Large chunks excluded from grouping
  - Heading starts new group (section boundary)
  - in_main propagation via any()
"""

from __future__ import annotations

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.preprocessor import (
    _group_small_siblings,
)


def _make_chunk(
    text: str,
    parent_xpath: str = "/html/body/main",
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    tag: str = "p",
    in_main: bool = True,
    xpath: str = "",
) -> HtmlChunk:
    if not xpath:
        xpath = "/html/body/main/p[1]"
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs={},
        parent_xpath=parent_xpath,
        depth=3,
        in_main=in_main,
    )


class TestSiblingGrouping:
    """_group_small_siblings() tests."""

    def test_empty_input(self):
        assert _group_small_siblings([]) == []

    def test_single_chunk_unchanged(self):
        c = _make_chunk("hello")
        result = _group_small_siblings([c])
        assert len(result) == 1
        assert result[0].text == "hello"

    def test_two_small_chunks_same_parent_merged(self):
        parent = "/html/body/main"
        c1 = _make_chunk("first paragraph text here", parent, xpath="/html/body/main/p[1]")
        c2 = _make_chunk("second paragraph text here", parent, xpath="/html/body/main/p[2]")
        result = _group_small_siblings([c1, c2])
        assert len(result) == 1
        assert "first paragraph text here" in result[0].text
        assert "second paragraph text here" in result[0].text

    def test_budget_exceeded_splits_group(self):
        parent = "/html/body/main"
        # Each chunk ~400 chars, combined exceeds 700
        c1 = _make_chunk("A" * 400, parent, xpath="/html/body/main/p[1]")
        c2 = _make_chunk("B" * 400, parent, xpath="/html/body/main/p[2]")
        result = _group_small_siblings([c1, c2])
        assert len(result) == 2  # Can't merge: 800 > 700

    def test_different_parent_not_merged(self):
        c1 = _make_chunk("first", "/html/body/main/div[1]", xpath="/html/body/main/div[1]/p[1]")
        c2 = _make_chunk("second", "/html/body/main/div[2]", xpath="/html/body/main/div[2]/p[1]")
        result = _group_small_siblings([c1, c2])
        assert len(result) == 2

    def test_large_chunk_not_grouped(self):
        parent = "/html/body/main"
        c1 = _make_chunk("small text", parent, xpath="/html/body/main/p[1]")
        c2 = _make_chunk("X" * 550, parent, xpath="/html/body/main/p[2]")  # > 500 chars
        c3 = _make_chunk("another small", parent, xpath="/html/body/main/p[3]")
        result = _group_small_siblings([c1, c2, c3])
        # c1 alone (flushed by large c2), c2 alone, c3 alone
        assert len(result) == 3

    def test_heading_breaks_group_and_stays_standalone(self):
        parent = "/html/body/main"
        c1 = _make_chunk("text before", parent, xpath="/html/body/main/p[1]")
        c2 = _make_chunk(
            "Section Title",
            parent,
            chunk_type=ChunkType.HEADING,
            tag="h2",
            xpath="/html/body/main/h2[1]",
        )
        c3 = _make_chunk("text after heading", parent, xpath="/html/body/main/p[2]")
        result = _group_small_siblings([c1, c2, c3])
        # c1 flushed, h2 standalone, c3 standalone (only 1 text block after heading)
        assert len(result) == 3
        assert result[0].text == "text before"
        assert result[1].text == "Section Title"
        assert result[2].text == "text after heading"

    def test_heading_between_text_blocks_separates_groups(self):
        parent = "/html/body/main"
        c1 = _make_chunk("paragraph one content text", parent, xpath="/html/body/main/p[1]")
        c2 = _make_chunk("paragraph two content text", parent, xpath="/html/body/main/p[2]")
        c3 = _make_chunk(
            "Section Title",
            parent,
            chunk_type=ChunkType.HEADING,
            tag="h2",
            xpath="/html/body/main/h2[1]",
        )
        c4 = _make_chunk("paragraph three content text", parent, xpath="/html/body/main/p[3]")
        c5 = _make_chunk("paragraph four content text", parent, xpath="/html/body/main/p[4]")
        result = _group_small_siblings([c1, c2, c3, c4, c5])
        # [c1+c2 merged], [h2 standalone], [c4+c5 merged]
        assert len(result) == 3
        assert "paragraph one content text" in result[0].text
        assert "paragraph two content text" in result[0].text
        assert result[1].text == "Section Title"
        assert "paragraph three content text" in result[2].text
        assert "paragraph four content text" in result[2].text

    def test_in_main_propagation(self):
        parent = "/html/body/main"
        c1 = _make_chunk("outside main content here", parent, in_main=False, xpath="/html/body/main/p[1]")
        c2 = _make_chunk("inside main content here", parent, in_main=True, xpath="/html/body/main/p[2]")
        result = _group_small_siblings([c1, c2])
        assert len(result) == 1
        # any() should be True since c2 is in_main
        assert result[0].in_main is True

    def test_three_small_chunks_fit_in_budget(self):
        parent = "/html/body/main"
        chunks = [
            _make_chunk(f"chunk number {i} content text", parent, xpath=f"/html/body/main/p[{i}]") for i in range(3)
        ]
        result = _group_small_siblings(chunks)
        assert len(result) == 1
        assert "chunk number 0 content text" in result[0].text
        assert "chunk number 2 content text" in result[0].text
