"""Tests for Phase C: Text density signal + expanded content rescue.

C-1: Text density
  - Large div with low text density penalized
  - Elements inside <main>/<article> exempt
  - Small elements (< 200 bytes HTML) skipped

C-2: Expanded rescue
  - h1, rating, review count patterns rescued (link-density removals only)
  - Non-link-density removals never rescued
"""

from __future__ import annotations

import lxml.html

from pagemap.pruning.aom_filter import (
    _TEXT_DENSITY_WEIGHT,
    _compute_weight,
    aom_filter,
)


def _make_el(html_str: str) -> lxml.html.HtmlElement:
    """Parse HTML fragment and return the first body child."""
    doc = lxml.html.document_fromstring(f"<html><body>{html_str}</body></html>")
    body = doc.body
    for child in body:
        if isinstance(child.tag, str):
            return child
    return body


def _make_doc(html_str: str) -> lxml.html.HtmlElement:
    return lxml.html.document_fromstring(f"<html><body>{html_str}</body></html>")


# ---------------------------------------------------------------------------
# C-1: Text density signal
# ---------------------------------------------------------------------------


class TestTextDensity:
    def test_large_div_low_density_penalized(self):
        """A large div with mostly HTML markup (low text/html ratio) gets penalized."""
        # Build a div with lots of attributes/markup but very little text
        inner = '<a href="#">' + "x" * 5 + "</a>" * 1 + '<span class="foo bar baz">' * 10 + "</span>" * 10
        html = f"<div>{inner}</div>"
        el = _make_el(html)
        weight, reason = _compute_weight(el)
        assert weight <= _TEXT_DENSITY_WEIGHT + 0.01
        assert "text-density-low" in reason

    def test_main_descendant_exempt(self):
        """Elements inside <main> are exempt from text density penalty."""
        inner = '<a href="#">' + "x" * 5 + "</a>" + '<span class="foo">' * 10 + "</span>" * 10
        html = f"<main><div>{inner}</div></main>"
        doc = _make_doc(html)
        # Find the div inside main
        main = doc.body.find(".//main")
        div = main.find(".//div")
        if div is not None:
            weight, reason = _compute_weight(div)
            # Should NOT be penalized for text density
            assert "text-density-low" not in reason

    def test_small_element_skipped(self):
        """Elements with HTML < 200 bytes are not checked for text density."""
        el = _make_el("<div>tiny</div>")
        weight, reason = _compute_weight(el)
        assert "text-density" not in reason
        assert weight >= 0.5


# ---------------------------------------------------------------------------
# C-2: Expanded content rescue
# ---------------------------------------------------------------------------


class TestExpandedRescue:
    def test_h1_rescued(self):
        """Link-density removed element containing <h1> is rescued."""
        html = (
            "<div>"
            "<h1>Product Name</h1>"
            '<a href="/a">link1</a> <a href="/b">link2</a> '
            '<a href="/c">link3</a> <a href="/d">link4</a> '
            '<a href="/e">link5</a> <a href="/f">link6</a> '
            '<a href="/g">link7</a> <a href="/h">link8</a>'
            "</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        # h1 content should survive (either not removed or rescued)
        text = (doc.text_content() or "").strip()
        assert "Product Name" in text

    def test_rating_rescued(self):
        """Link-density removed element containing rating pattern is rescued."""
        html = (
            "<div>"
            "<span>4.5 / 5 rating</span>"
            '<a href="/a">link1</a> <a href="/b">link2</a> '
            '<a href="/c">link3</a> <a href="/d">link4</a> '
            '<a href="/e">link5</a> <a href="/f">link6</a> '
            '<a href="/g">link7</a> <a href="/h">link8</a>'
            "</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        text = (doc.text_content() or "").strip()
        assert "4.5" in text

    def test_review_count_rescued(self):
        """Link-density removed element containing review count is rescued."""
        html = (
            "<div>"
            "<span>253 reviews</span>"
            '<a href="/a">link1</a> <a href="/b">link2</a> '
            '<a href="/c">link3</a> <a href="/d">link4</a> '
            '<a href="/e">link5</a> <a href="/f">link6</a> '
            '<a href="/g">link7</a> <a href="/h">link8</a>'
            "</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        text = (doc.text_content() or "").strip()
        assert "253" in text

    def test_aria_hidden_never_rescued(self):
        """Security-critical removals (aria-hidden) are never rescued."""
        html = (
            '<div aria-hidden="true">'
            "<span>₩99,000</span>"
            "</div>"
            "<div>Main content here with enough text to prevent thin-DOM rescue</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        # aria-hidden removal should not be rescued
        text = (doc.text_content() or "").strip()
        # The price should NOT be in the output (aria-hidden is security-critical)
        assert "99,000" not in text

    def test_display_none_never_rescued(self):
        """Security-critical display:none removals are never rescued."""
        html = (
            '<div style="display:none">'
            "<span>Hidden price ₩50,000</span>"
            "</div>"
            "<div>Main content here with enough text</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        text = (doc.text_content() or "").strip()
        assert "50,000" not in text


# ---------------------------------------------------------------------------
# Hidden DOM Injection — 6 CSS hiding patterns (Phase 6.4)
# ---------------------------------------------------------------------------


class TestHiddenDomInjectionWeight:
    """Unit tests for _compute_weight with hidden DOM injection patterns."""

    def test_offscreen_position_left(self):
        el = _make_el('<div style="position:absolute;left:-9999px">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "offscreen-position"

    def test_offscreen_position_top(self):
        el = _make_el('<div style="top: -10000px">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "offscreen-position"

    def test_offscreen_position_small_value_no_trigger(self):
        """left:-10px (3 digits) should NOT trigger the pattern."""
        el = _make_el('<div style="left:-10px">visible</div>')
        weight, reason = _compute_weight(el)
        assert weight != 0.0 or reason != "offscreen-position"

    def test_clip_rect_hidden(self):
        el = _make_el('<div style="clip:rect(0,0,0,0)">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "clip-hidden"

    def test_clip_path_inset_hidden(self):
        el = _make_el('<div style="clip-path:inset(100%)">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "clip-hidden"

    def test_text_indent_hidden(self):
        el = _make_el('<div style="text-indent:-9999px">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "text-indent-hidden"

    def test_text_indent_small_value_no_trigger(self):
        """text-indent:-20px should NOT trigger (only 2 digits)."""
        el = _make_el('<div style="text-indent:-20px">visible</div>')
        weight, reason = _compute_weight(el)
        assert weight != 0.0 or reason != "text-indent-hidden"

    def test_transform_offscreen(self):
        el = _make_el('<div style="transform:translateX(-9999px)">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "transform-offscreen"

    def test_sr_only_hidden(self):
        el = _make_el('<div style="position:absolute;width:1px;height:1px;overflow:hidden">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "sr-only-hidden"

    def test_zero_dimension(self):
        el = _make_el('<div style="width:0px;overflow:hidden;">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "zero-dimension"

    def test_zero_dimension_no_trailing_semicolon(self):
        """width:0 at end of style string (no semicolon) should still match."""
        el = _make_el('<div style="overflow:hidden; width:0">hidden</div>')
        weight, reason = _compute_weight(el)
        assert weight == 0.0
        assert reason == "zero-dimension"


class TestHiddenDomInjectionNeverRescued:
    """Integration tests: hidden DOM injection removals are never rescued."""

    def test_offscreen_never_rescued(self):
        html = (
            '<div style="position:absolute;left:-9999px">'
            "<span>₩99,000 hidden prompt injection</span>"
            "</div>"
            "<div>Main content</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        text = (doc.text_content() or "").strip()
        assert "hidden prompt injection" not in text

    def test_clip_hidden_never_rescued(self):
        html = '<div style="clip:rect(0,0,0,0)"><span>₩50,000 injected</span></div><div>Main content</div>'
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        text = (doc.text_content() or "").strip()
        assert "injected" not in text

    def test_sr_only_hidden_never_rescued(self):
        html = (
            '<div style="position:absolute;width:1px;height:1px;overflow:hidden">'
            "<span>₩30,000 secret</span>"
            "</div>"
            "<div>Main content</div>"
        )
        doc = _make_doc(html)
        aom_filter(doc, schema_name="Product")
        text = (doc.text_content() or "").strip()
        assert "secret" not in text
