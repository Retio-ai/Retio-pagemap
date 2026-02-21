# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Phase 6.1: Token counting O(N²) → O(1-per-line) optimization."""

from __future__ import annotations

import pytest

from pagemap.preprocessing.preprocess import count_tokens, count_tokens_approx


class TestCountTokensApprox:
    """count_tokens_approx accuracy vs exact."""

    def test_english_within_25pct(self):
        text = "The quick brown fox jumps over the lazy dog. " * 100
        exact = count_tokens(text)
        approx = count_tokens_approx(text)
        assert abs(approx - exact) / exact < 0.25, f"approx={approx}, exact={exact}"

    def test_korean_within_55pct(self):
        # Korean BPE is ~1.1 chars/tok; approx heuristic assumes ~2 chars/tok.
        # This is intentionally loose — approx is used only for metrics, not budgets.
        text = "한국어 텍스트 샘플입니다. 이것은 테스트를 위한 문장입니다. " * 100
        exact = count_tokens(text)
        approx = count_tokens_approx(text)
        assert abs(approx - exact) / exact < 0.55, f"approx={approx}, exact={exact}"

    def test_empty_returns_zero(self):
        assert count_tokens_approx("") == 0

    def test_mixed_language(self):
        text = "Hello 안녕하세요 World 세상 " * 100
        exact = count_tokens(text)
        approx = count_tokens_approx(text)
        assert abs(approx - exact) / exact < 0.35, f"approx={approx}, exact={exact}"


class TestCalibrateCharsPerToken:
    """_calibrate_chars_per_token correctness."""

    def test_english_ratio_near_4(self):
        from pagemap.pruned_context_builder import _calibrate_chars_per_token

        lines = ["This is a sample line of English text for calibration testing."] * 30
        cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
        assert 3.0 <= cpt <= 5.5, f"English cpt={cpt}"

    def test_korean_ratio_near_2(self):
        from pagemap.pruned_context_builder import _calibrate_chars_per_token

        lines = ["한국어 텍스트 샘플입니다 이것은 테스트를 위한 문장입니다"] * 30
        cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
        assert 1.5 <= cpt <= 3.5, f"Korean cpt={cpt}"

    def test_empty_returns_default(self):
        from pagemap.pruned_context_builder import _calibrate_chars_per_token

        cpt = _calibrate_chars_per_token([], min_len=5, max_line_len=300)
        assert cpt == 4.0

    def test_short_lines_skipped(self):
        from pagemap.pruned_context_builder import _calibrate_chars_per_token

        lines = ["ab", "cd", "ef"]  # all < min_len=5
        cpt = _calibrate_chars_per_token(lines, min_len=5, max_line_len=300)
        assert cpt == 4.0


class TestCompressDefaultCharBudget:
    """_compress_default respects token budget with char-based loop."""

    def test_200_lines_within_budget(self):
        from pagemap.pruned_context_builder import _compress_default

        lines = [f"Line {i}: This is a moderately long line of text for testing purposes." for i in range(200)]
        html = "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>"
        max_tokens = 200
        result = _compress_default(html, max_tokens)
        assert count_tokens(result) <= max_tokens

    def test_small_input_fully_included(self):
        from pagemap.pruned_context_builder import _compress_default

        html = "<html><body><p>Hello world</p><p>Short page</p></body></html>"
        result = _compress_default(html, max_tokens=500)
        assert "Hello world" in result


class TestCollectReExport:
    """collect.py re-export of _count_tokens_approx."""

    def test_import_works(self):
        collect = pytest.importorskip("pagemap.collect", reason="collect module excluded from release")
        _count_tokens_approx = collect._count_tokens_approx

        assert callable(_count_tokens_approx)
        result = _count_tokens_approx("Hello world")
        assert result > 0

    def test_matches_canonical(self):
        collect = pytest.importorskip("pagemap.collect", reason="collect module excluded from release")
        _count_tokens_approx = collect._count_tokens_approx

        text = "Test string for comparison" * 50
        assert _count_tokens_approx(text) == count_tokens_approx(text)


class TestBudgetFilterCJKTrim:
    """_budget_filter_interactables exact trim loop with CJK names."""

    def test_cjk_elements_within_budget(self):
        from pagemap import Interactable
        from pagemap.page_map_builder import (
            _MIN_INTERACTABLE_BUDGET,
            _OVERHEAD_TOKEN_ESTIMATE,
            _budget_filter_interactables,
        )

        # Create elements with Korean names (higher token density)
        elements = [
            Interactable(
                ref=i,
                role="button",
                name=f"한국어 버튼 이름 번호 {i} 입니다",
                affordance="click",
                region="main",
                tier=1,
            )
            for i in range(50)
        ]
        # Tight budget
        selected = _budget_filter_interactables(elements, pruned_tokens=800, total_budget=1200)
        # Verify exact token count fits
        total_text = "\n".join(str(e) for e in selected)
        available = 1200 - 800 - _OVERHEAD_TOKEN_ESTIMATE
        if available < _MIN_INTERACTABLE_BUDGET:
            available = _MIN_INTERACTABLE_BUDGET
        assert count_tokens(total_text) <= available or len(selected) <= 1
