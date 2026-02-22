# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helper utilities for pruning test files.

Underscore prefix prevents pytest collection.
These are plain utility functions (not fixtures — conftest.py is reserved
for fixtures).
"""

from __future__ import annotations

import lxml.html
from lxml import etree

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.preprocessor import _decompose_element
from pagemap.pruning.pruner import PruneDecision, prune_chunks


def make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    in_main: bool = True,
    tag: str = "div",
    attrs: dict | None = None,
    xpath: str | None = None,
    parent_xpath: str | None = None,
) -> HtmlChunk:
    """Build an HtmlChunk for testing."""
    if xpath is None:
        xpath = "/html/body/main/div[1]" if in_main else "/html/body/div[1]"
    if parent_xpath is None:
        parent_xpath = "/html/body/main" if in_main else "/html/body"
    return HtmlChunk(
        xpath=xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs=attrs or {},
        parent_xpath=parent_xpath,
        depth=3,
        in_main=in_main,
    )


def prune_single(
    chunk: HtmlChunk,
    schema: str = "Product",
    has_main: bool = True,
) -> PruneDecision:
    """Prune a single chunk and return the decision."""
    results = prune_chunks([chunk], schema_name=schema, has_main=has_main)
    assert len(results) == 1
    return results[0][1]


def parse_el(html_str: str) -> lxml.html.HtmlElement:
    """Parse an HTML fragment and return the first element inside <body>."""
    doc = lxml.html.fromstring(f"<html><body>{html_str}</body></html>")
    body = doc.find(".//body")
    assert body is not None
    children = list(body)
    assert len(children) >= 1
    return children[0]


def parse_doc(html_str: str) -> tuple[lxml.html.HtmlElement, etree._ElementTree]:
    """Parse a full HTML document and return (doc, tree)."""
    doc = lxml.html.document_fromstring(html_str)
    tree = doc.getroottree()
    return doc, tree


def decompose_body(html_str: str) -> list[HtmlChunk]:
    """Parse HTML and decompose <body> into chunks."""
    doc, tree = parse_doc(html_str)
    body = doc.body if doc.body is not None else doc
    return _decompose_element(body, tree)


def html(body: str, head: str = "") -> str:
    """Build a complete HTML document from body (and optional head) content."""
    head_section = f"<head>{head}</head>" if head else ""
    return f"<html>{head_section}<body>{body}</body></html>"
