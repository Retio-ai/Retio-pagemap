# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Unicode script-based language filtering for mixed-content pages.

On multilingual e-commerce pages (e.g., Amazon with Spanish specs + Korean UI
+ English reviews), irrelevant language content creates noise for AI agents.

Strategy:
- Detect the dominant script of the page (LATIN, CJK, HANGUL, etc.)
- Short UI noise (< 50 chars, > 80% non-dominant script) → remove
- Long non-dominant content (≥ 50 chars, > 50%) → tag with [lang]
- Passthrough exceptions: URLs, numbers/units, brand names, ≤ 5 chars
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from enum import Enum, auto


class Script(Enum):
    LATIN = auto()
    CJK = auto()
    HANGUL = auto()
    HIRAGANA = auto()
    KATAKANA = auto()
    CYRILLIC = auto()
    ARABIC = auto()
    COMMON = auto()  # digits, punctuation, whitespace — always pass
    UNKNOWN = auto()


# Unicode ranges → Script mapping, sorted by start codepoint.
# Covers the major blocks; COMMON handles digits/punct/space.
_RANGES: list[tuple[int, int, Script]] = sorted(
    [
        # Basic Latin letters
        (0x0041, 0x005A, Script.LATIN),  # A-Z
        (0x0061, 0x007A, Script.LATIN),  # a-z
        # Latin Extended
        (0x00C0, 0x024F, Script.LATIN),  # Latin Extended-A/B
        (0x1E00, 0x1EFF, Script.LATIN),  # Latin Extended Additional
        # Cyrillic
        (0x0400, 0x04FF, Script.CYRILLIC),
        (0x0500, 0x052F, Script.CYRILLIC),  # Cyrillic Supplement
        # Arabic
        (0x0600, 0x06FF, Script.ARABIC),
        (0x0750, 0x077F, Script.ARABIC),  # Arabic Supplement
        (0x08A0, 0x08FF, Script.ARABIC),  # Arabic Extended-A
        # Hangul
        (0x1100, 0x11FF, Script.HANGUL),  # Hangul Jamo
        (0x3130, 0x318F, Script.HANGUL),  # Hangul Compatibility Jamo
        (0xAC00, 0xD7AF, Script.HANGUL),  # Hangul Syllables
        (0xD7B0, 0xD7FF, Script.HANGUL),  # Hangul Jamo Extended-B
        # Hiragana
        (0x3040, 0x309F, Script.HIRAGANA),
        # Katakana
        (0x30A0, 0x30FF, Script.KATAKANA),
        (0x31F0, 0x31FF, Script.KATAKANA),  # Katakana Phonetic Extensions
        # CJK Unified Ideographs
        (0x4E00, 0x9FFF, Script.CJK),
        (0x3400, 0x4DBF, Script.CJK),  # CJK Extension A
        (0x20000, 0x2A6DF, Script.CJK),  # CJK Extension B
        (0x2A700, 0x2B73F, Script.CJK),  # CJK Extension C
        (0x2B740, 0x2B81F, Script.CJK),  # CJK Extension D
        (0xF900, 0xFAFF, Script.CJK),  # CJK Compatibility Ideographs
        # CJK symbols & punctuation (treated as COMMON for scoring)
        (0x3000, 0x303F, Script.COMMON),
        # Common: digits, basic punctuation, whitespace, symbols
        (0x0000, 0x0040, Script.COMMON),  # control + digits + basic punct
        (0x005B, 0x0060, Script.COMMON),  # [ \ ] ^ _ `
        (0x007B, 0x00BF, Script.COMMON),  # { | } ~ ... latin punct
        (0x2000, 0x206F, Script.COMMON),  # General Punctuation
        (0x2070, 0x209F, Script.COMMON),  # Superscripts and Subscripts
        (0x20A0, 0x20CF, Script.COMMON),  # Currency Symbols
        (0x2100, 0x214F, Script.COMMON),  # Letterlike Symbols
        (0x2150, 0x218F, Script.COMMON),  # Number Forms
        (0xFF01, 0xFF0F, Script.COMMON),  # Fullwidth punctuation
        (0xFF10, 0xFF19, Script.COMMON),  # Fullwidth digits
        (0xFF1A, 0xFF20, Script.COMMON),  # Fullwidth symbols
        (0xFE30, 0xFE4F, Script.COMMON),  # CJK Compatibility Forms
    ],
    key=lambda r: r[0],
)

_STARTS = [r[0] for r in _RANGES]


def classify_char(cp: int) -> Script:
    """Classify a Unicode codepoint to a Script. O(log k)."""
    if cp > 0x10FFFF:
        return Script.UNKNOWN
    idx = bisect.bisect_right(_STARTS, cp) - 1
    if idx >= 0 and _RANGES[idx][0] <= cp <= _RANGES[idx][1]:
        return _RANGES[idx][2]
    return Script.UNKNOWN


@dataclass(frozen=True, slots=True)
class ScriptProfile:
    """Script distribution for a text."""

    total_classified: int  # non-COMMON, non-UNKNOWN chars
    dominant: Script
    dominant_ratio: float
    counts: dict[Script, int]


def profile_text(text: str) -> ScriptProfile:
    """Compute script distribution for a text string."""
    counts: dict[Script, int] = {}
    for ch in text:
        s = classify_char(ord(ch))
        if s in (Script.COMMON, Script.UNKNOWN):
            continue
        counts[s] = counts.get(s, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return ScriptProfile(
            total_classified=0,
            dominant=Script.COMMON,
            dominant_ratio=0.0,
            counts=counts,
        )

    dominant = max(counts, key=counts.get)  # type: ignore[arg-type]
    return ScriptProfile(
        total_classified=total,
        dominant=dominant,
        dominant_ratio=counts[dominant] / total,
        counts=counts,
    )


# Japanese pages use both Hiragana and Katakana alongside CJK
_SCRIPT_GROUPS: dict[Script, set[Script]] = {
    Script.HANGUL: {Script.HANGUL},
    Script.CJK: {Script.CJK, Script.HIRAGANA, Script.KATAKANA},
    Script.HIRAGANA: {Script.CJK, Script.HIRAGANA, Script.KATAKANA},
    Script.KATAKANA: {Script.CJK, Script.HIRAGANA, Script.KATAKANA},
    Script.LATIN: {Script.LATIN},
    Script.CYRILLIC: {Script.CYRILLIC},
    Script.ARABIC: {Script.ARABIC},
}


def _is_same_group(s1: Script, s2: Script) -> bool:
    """Check if two scripts belong to the same language group."""
    group = _SCRIPT_GROUPS.get(s1)
    if group:
        return s2 in group
    return s1 == s2


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_NUMERIC_UNIT_RE = re.compile(r"^[\d.,]+\s*[a-zA-Z%℃㎜㎝㎏㎖㎡]*$")


def _is_passthrough(line: str) -> bool:
    """Check if a line should always pass through regardless of script."""
    stripped = line.strip()
    if len(stripped) <= 5:
        return True
    if _URL_RE.search(stripped):
        return True
    return bool(_NUMERIC_UNIT_RE.match(stripped))


def _dominant_script_ratio(text: str, page_script: Script) -> float:
    """Compute ratio of non-dominant script chars in text.

    For page_script HANGUL on a CJK+HANGUL mixed line, both are considered
    "page-group" scripts. Only truly foreign scripts count as non-dominant.
    """
    group = _SCRIPT_GROUPS.get(page_script, {page_script})
    foreign = 0
    page_group = 0
    for ch in text:
        s = classify_char(ord(ch))
        if s in (Script.COMMON, Script.UNKNOWN):
            continue
        if s in group:
            page_group += 1
        else:
            foreign += 1
    total = foreign + page_group
    if total == 0:
        return 0.0
    return foreign / total


def _script_label(text: str) -> str:
    """Return short script label for tagging."""
    prof = profile_text(text)
    _labels = {
        Script.LATIN: "en",
        Script.HANGUL: "ko",
        Script.CJK: "zh",
        Script.HIRAGANA: "ja",
        Script.KATAKANA: "ja",
        Script.CYRILLIC: "ru",
        Script.ARABIC: "ar",
    }
    return _labels.get(prof.dominant, "other")


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Result of language filtering."""

    lines: list[str]
    removed_count: int
    tagged_count: int
    page_script: Script


def detect_page_script(lines: list[str]) -> Script:
    """Detect the dominant script of a page from its text lines."""
    all_text = "\n".join(lines)
    prof = profile_text(all_text)
    if prof.total_classified == 0:
        return Script.COMMON
    return prof.dominant


def filter_lines(
    lines: list[str],
    page_script: Script | None = None,
    *,
    remove_threshold: float = 0.8,
    tag_threshold: float = 0.5,
) -> FilterResult:
    """Filter lines by script affinity to page's dominant script.

    Args:
        lines: Text lines to filter.
        page_script: Expected dominant script. Auto-detected if None.
        remove_threshold: Foreign ratio above which short lines are removed.
        tag_threshold: Foreign ratio above which long lines get [lang] tags.

    Returns:
        FilterResult with filtered lines and statistics.
    """
    if page_script is None:
        page_script = detect_page_script(lines)

    if page_script in (Script.COMMON, Script.UNKNOWN):
        return FilterResult(
            lines=list(lines),
            removed_count=0,
            tagged_count=0,
            page_script=page_script,
        )

    result_lines: list[str] = []
    removed = 0
    tagged = 0

    for line in lines:
        if _is_passthrough(line):
            result_lines.append(line)
            continue

        foreign_ratio = _dominant_script_ratio(line, page_script)

        if len(line) < 50 and foreign_ratio > remove_threshold:
            # Short UI noise in foreign script → remove
            removed += 1
            continue

        if foreign_ratio > tag_threshold:
            # Long foreign content → tag
            label = _script_label(line)
            result_lines.append(f"[{label}] {line}")
            tagged += 1
        else:
            result_lines.append(line)

    return FilterResult(
        lines=result_lines,
        removed_count=removed,
        tagged_count=tagged,
        page_script=page_script,
    )
