# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Property-based fuzz tests using Hypothesis.

Verifies invariants hold for arbitrary inputs across the sanitizer,
page classifier, and script filter modules.
"""

from __future__ import annotations

try:
    from hypothesis import HealthCheck, example, given, settings
    from hypothesis import strategies as st
except ImportError:
    import pytest

    pytest.skip("hypothesis not installed", allow_module_level=True)

import pytest

from pagemap.page_classifier import THRESHOLDS, ClassificationResult, classify_page
from pagemap.sanitizer import _unescape_entities, sanitize_content_block, sanitize_text
from pagemap.script_filter import (
    FilterResult,
    Script,
    ScriptProfile,
    classify_char,
    filter_lines,
    profile_text,
)

# ---------------------------------------------------------------------------
# Module-level strategies
# ---------------------------------------------------------------------------

GENERAL_TEXT = st.text(min_size=0, max_size=5000)

HTML_LIKE = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        whitelist_characters="<>/=\"'&;#!.- \n\t",
    ),
    min_size=10,
    max_size=3000,
)

VALID_URL = st.from_regex(
    r"https?://[a-z0-9\-]+(\.[a-z]{2,6}){1,2}(/[a-z0-9\-._~:/?#\[\]@!$&'()*+,;=]*)?",
    fullmatch=True,
)

UNICODE_CODEPOINT = st.integers(0, 0x10FFFF).filter(lambda x: x not in range(0xD800, 0xE000))

STRING_LIST = st.lists(st.text(min_size=0, max_size=200), min_size=0, max_size=50)

VALID_PAGE_TYPES = set(THRESHOLDS) | {"unknown"}

# ---------------------------------------------------------------------------
# Shared settings
# ---------------------------------------------------------------------------

_fuzz_settings = settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# TestFuzzSanitizer
# ---------------------------------------------------------------------------


@pytest.mark.fuzz
class TestFuzzSanitizer:
    """Property-based tests for the sanitizer module."""

    @_fuzz_settings
    @given(text=GENERAL_TEXT)
    @example("hello&nbsp;world")
    @example("&#60;script&#62;")
    def test_sanitize_text_no_html_entities(self, text: str) -> None:
        result = sanitize_text(text)
        assert "&nbsp;" not in result
        assert "\xa0" not in result

    @_fuzz_settings
    @given(text=GENERAL_TEXT)
    @example("\x00hidden\x1ftext")
    def test_sanitize_text_no_control_chars(self, text: str) -> None:
        result = sanitize_text(text)
        for ch in result:
            cp = ord(ch)
            assert not (0x00 <= cp <= 0x08), f"control char U+{cp:04X} in result"
            assert not (0x0B <= cp <= 0x0C), f"control char U+{cp:04X} in result"
            assert not (0x0E <= cp <= 0x1F), f"control char U+{cp:04X} in result"

    @_fuzz_settings
    @given(text=GENERAL_TEXT, max_len=st.integers(1, 1000))
    @example("x" * 2000, 10)
    def test_sanitize_text_respects_max_length(self, text: str, max_len: int) -> None:
        result = sanitize_text(text, max_len=max_len)
        assert len(result) <= max_len

    @_fuzz_settings
    @given(text=GENERAL_TEXT)
    @example("&nbsp;")
    @example("hello\xa0world")
    def test_unescape_no_nbsp_char(self, text: str) -> None:
        result = _unescape_entities(text)
        assert "\xa0" not in result

    @_fuzz_settings
    @given(text=GENERAL_TEXT)
    @example("line1&nbsp;line2\nnext")
    def test_sanitize_content_block_no_nbsp_char(self, text: str) -> None:
        result = sanitize_content_block(text)
        assert "\xa0" not in result
        assert "&nbsp;" not in result


# ---------------------------------------------------------------------------
# TestFuzzPageClassifier
# ---------------------------------------------------------------------------


@pytest.mark.fuzz
class TestFuzzPageClassifier:
    """Property-based tests for the page classifier module."""

    @_fuzz_settings
    @given(url=VALID_URL, raw_html=st.one_of(HTML_LIKE, st.none()))
    def test_classify_never_crashes(self, url: str, raw_html: str | None) -> None:
        result = classify_page(url, raw_html)
        assert isinstance(result, ClassificationResult)

    @_fuzz_settings
    @given(url=VALID_URL, raw_html=st.one_of(HTML_LIKE, st.none()))
    def test_classify_returns_valid_page_type(self, url: str, raw_html: str | None) -> None:
        result = classify_page(url, raw_html)
        assert result.page_type in VALID_PAGE_TYPES

    @_fuzz_settings
    @given(url=VALID_URL, raw_html=st.one_of(HTML_LIKE, st.none()))
    def test_classify_confidence_in_range(self, url: str, raw_html: str | None) -> None:
        result = classify_page(url, raw_html)
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# TestFuzzScriptFilter
# ---------------------------------------------------------------------------


@pytest.mark.fuzz
class TestFuzzScriptFilter:
    """Property-based tests for the script filter module."""

    @_fuzz_settings
    @given(cp=UNICODE_CODEPOINT)
    @example(0)
    @example(0x10FFFF)
    @example(0xAC00)
    def test_classify_char_never_crashes(self, cp: int) -> None:
        result = classify_char(cp)
        assert isinstance(result, Script)

    @_fuzz_settings
    @given(text=GENERAL_TEXT)
    @example("")
    @example("Hello 世界")
    def test_profile_text_never_crashes(self, text: str) -> None:
        result = profile_text(text)
        assert isinstance(result, ScriptProfile)
        assert 0.0 <= result.dominant_ratio <= 1.0
        assert result.total_classified >= 0

    @_fuzz_settings
    @given(lines=STRING_LIST)
    @example([])
    @example(["hello", "world"])
    def test_filter_lines_never_crashes(self, lines: list[str]) -> None:
        result = filter_lines(lines)
        assert isinstance(result, FilterResult)

    @_fuzz_settings
    @given(lines=STRING_LIST)
    @example(["a", "b", "c"])
    @example([])
    def test_filter_lines_preserves_or_reduces(self, lines: list[str]) -> None:
        result = filter_lines(lines)
        assert len(result.lines) <= len(lines)
        assert result.removed_count + len(result.lines) == len(lines)
        assert result.removed_count >= 0
