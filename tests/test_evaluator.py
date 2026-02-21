"""Unit tests for pagemap.benchmark.evaluator validator functions."""

from __future__ import annotations

import pytest

pytest.importorskip("pagemap.benchmark", reason="benchmark module excluded from release")

from pagemap.benchmark.evaluator import (
    _eval_contains_discount_info,
    _eval_contains_measurement,
    _eval_count_in_range,
    _eval_multi_field_complete,
    evaluate_result,
)
from pagemap.benchmark.runner import TaskResult

# ---------------------------------------------------------------------------
# 1. TestContainsDiscountInfo
# ---------------------------------------------------------------------------


class TestContainsDiscountInfo:
    """Tests for _eval_contains_discount_info validator."""

    def test_percentage_discount(self):
        """'30% 할인' contains a percentage — should pass."""
        assert _eval_contains_discount_info("30% 할인", {}) is True

    def test_korean_percent(self):
        """'30퍼센트 할인' uses Korean percent word — should pass."""
        assert _eval_contains_discount_info("30퍼센트 할인", {}) is True

    def test_two_prices(self):
        """Two different prices (59,000 vs 39,000) — should pass."""
        assert _eval_contains_discount_info("정가 59,000원, 할인가 39,000원", {}) is True

    def test_single_price(self):
        """Only one number >= 10 — should fail."""
        assert _eval_contains_discount_info("가격: 59,000원", {}) is False

    def test_same_price_twice(self):
        """Same numeric value repeated — set has only one entry, should fail."""
        assert _eval_contains_discount_info("59000, 59000", {}) is False

    def test_empty(self):
        """Empty string — should fail."""
        assert _eval_contains_discount_info("", {}) is False

    def test_currency_prices(self):
        """Two different dollar prices — should pass."""
        assert _eval_contains_discount_info("$59.99 → $39.99", {}) is True

    def test_low_price_global(self):
        """Prices above threshold 10 — should pass."""
        assert _eval_contains_discount_info("Was $29.99, Now $19.99", {}) is True

    def test_non_price_small_numbers(self):
        """Small numbers below threshold 10 — should fail."""
        assert _eval_contains_discount_info("ref 3, ref 5", {}) is False


# ---------------------------------------------------------------------------
# 2. TestMultiFieldComplete
# ---------------------------------------------------------------------------


class TestMultiFieldComplete:
    """Tests for _eval_multi_field_complete validator."""

    def test_all_fields_present(self):
        """Answer contains all 4 expected fields — should pass."""
        task = {
            "expected_fields": ["브랜드", "가격", "소재", "색상"],
            "min_fields": 3,
        }
        answer = "브랜드: 나이키, 가격: 59,000원, 소재: 면, 색상: 검정"
        assert _eval_multi_field_complete(answer, task) is True

    def test_partial_fields(self):
        """Answer has 3 of 4 fields with min_fields=3 — should pass."""
        task = {
            "expected_fields": ["브랜드", "가격", "소재", "색상"],
            "min_fields": 3,
        }
        answer = "브랜드: 나이키, 가격: 59,000원, 소재: 면"
        assert _eval_multi_field_complete(answer, task) is True

    def test_too_few_fields(self):
        """Answer has 2 of 4 fields with min_fields=3 — should fail."""
        task = {
            "expected_fields": ["브랜드", "가격", "소재", "색상"],
            "min_fields": 3,
        }
        answer = "브랜드: 나이키, 가격: 59,000원"
        assert _eval_multi_field_complete(answer, task) is False

    def test_no_fields_fallback_long(self):
        """No expected_fields, answer > 20 chars — should pass."""
        task: dict = {}
        answer = "이 제품은 매우 훌륭한 품질을 가지고 있습니다"
        assert len(answer) > 20
        assert _eval_multi_field_complete(answer, task) is True

    def test_no_fields_negative_answer(self):
        """No expected_fields, answer contains negative phrase — should fail."""
        task: dict = {}
        answer = "확인할 수 없습니다"
        assert _eval_multi_field_complete(answer, task) is False

    def test_case_insensitive(self):
        """Fields should match case-insensitively."""
        task = {
            "expected_fields": ["Brand", "Price", "Material"],
            "min_fields": 3,
        }
        answer = "brand: Nike, price: $59.99, material: cotton"
        assert _eval_multi_field_complete(answer, task) is True


# ---------------------------------------------------------------------------
# 3. TestContainsMeasurement
# ---------------------------------------------------------------------------


class TestContainsMeasurement:
    """Tests for _eval_contains_measurement validator."""

    def test_cm(self):
        """'가슴둘레 55cm' contains cm measurement — should pass."""
        assert _eval_contains_measurement("가슴둘레 55cm", {}) is True

    def test_mm(self):
        """'두께 3.5mm' contains mm measurement — should pass."""
        assert _eval_contains_measurement("두께 3.5mm", {}) is True

    def test_m(self):
        """'길이 170 m' contains m measurement — should pass."""
        assert _eval_contains_measurement("길이 170 m", {}) is True

    def test_inch(self):
        """'32 inch' contains inch measurement — should pass."""
        assert _eval_contains_measurement("32 inch", {}) is True

    def test_korean_cm(self):
        """'55센티미터' uses Korean cm word — should pass."""
        assert _eval_contains_measurement("55센티미터", {}) is True

    def test_korean_m(self):
        """'1.7미터' uses Korean meter word — should pass."""
        assert _eval_contains_measurement("1.7미터", {}) is True

    def test_no_unit(self):
        """'사이즈 95' has number but no measurement unit — should fail."""
        assert _eval_contains_measurement("사이즈 95", {}) is False

    def test_word_boundary_men(self):
        """'5 men' should NOT match — m must be a standalone unit."""
        assert _eval_contains_measurement("5 men", {}) is False

    def test_word_boundary_minutes(self):
        """'100 minutes' should NOT match — m must be a standalone unit."""
        assert _eval_contains_measurement("100 minutes", {}) is False

    def test_empty(self):
        """Empty string — should fail."""
        assert _eval_contains_measurement("", {}) is False


# ---------------------------------------------------------------------------
# 4. TestCountInRange
# ---------------------------------------------------------------------------


class TestCountInRange:
    """Tests for _eval_count_in_range validator."""

    def test_in_range(self):
        """'15개' with min=5, max=30 — should pass."""
        task = {"min_count": 5, "max_count": 30}
        assert _eval_count_in_range("15개", task) is True

    def test_below_range(self):
        """'2개' with min=5, max=30 — should fail."""
        task = {"min_count": 5, "max_count": 30}
        assert _eval_count_in_range("2개", task) is False

    def test_above_range(self):
        """'50개' with min=5, max=30 — should fail."""
        task = {"min_count": 5, "max_count": 30}
        assert _eval_count_in_range("50개", task) is False

    def test_multi_number_one_matches(self):
        """'총 15개 중 3번째' — 15 is in [10,20], should pass."""
        task = {"min_count": 10, "max_count": 20}
        assert _eval_count_in_range("총 15개 중 3번째", task) is True

    def test_no_number(self):
        """'많이 있어요' has no digits — should fail."""
        assert _eval_count_in_range("많이 있어요", {}) is False

    def test_defaults(self):
        """'42' with default min=1, max=1000 — should pass."""
        assert _eval_count_in_range("42", {}) is True


# ---------------------------------------------------------------------------
# 5. TestEvaluateResultIntegration
# ---------------------------------------------------------------------------


class TestEvaluateResultIntegration:
    """End-to-end tests using evaluate_result with TaskResult objects."""

    def test_discount_info_via_evaluate_result(self):
        """evaluate_result with contains_discount_info validation."""
        task = {
            "id": "discount_01",
            "validation": "contains_discount_info",
        }
        result = TaskResult(
            task_id="discount_01",
            condition="page_map",
            answer="정가 59,000원, 할인가 39,000원",
        )
        assert evaluate_result(task, result) is True

    def test_multi_field_via_evaluate_result(self):
        """evaluate_result with multi_field_complete validation."""
        task = {
            "id": "field_01",
            "validation": "multi_field_complete",
            "expected_fields": ["브랜드", "가격", "소재"],
            "min_fields": 2,
        }
        result = TaskResult(
            task_id="field_01",
            condition="page_map",
            answer="브랜드: 나이키, 가격: 59,000원, 소재: 면",
        )
        assert evaluate_result(task, result) is True

    def test_measurement_via_evaluate_result(self):
        """evaluate_result with contains_measurement validation."""
        task = {
            "id": "measure_01",
            "validation": "contains_measurement",
        }
        result = TaskResult(
            task_id="measure_01",
            condition="page_map",
            answer="가슴둘레 55cm, 총장 70cm",
        )
        assert evaluate_result(task, result) is True

    def test_count_range_via_evaluate_result(self):
        """evaluate_result with count_in_range validation."""
        task = {
            "id": "count_01",
            "validation": "count_in_range",
            "min_count": 5,
            "max_count": 30,
        }
        result = TaskResult(
            task_id="count_01",
            condition="page_map",
            answer="총 15개의 상품이 있습니다",
        )
        assert evaluate_result(task, result) is True


# ---------------------------------------------------------------------------
# 6. TestMinStepsLiveValidator
# ---------------------------------------------------------------------------


class TestMinStepsLiveValidator:
    """Tests for min_steps dict validator in live tasks."""

    def test_live_task_meets_min_steps(self):
        """Live task with steps >= min_steps -- should pass."""
        task = {"id": "G4", "validators": ["contains_number", {"min_steps": 3}]}
        result = TaskResult(task_id="G4", condition="page_map_live", answer="2,847 open issues", steps=5)
        assert evaluate_result(task, result) is True

    def test_live_task_below_min_steps(self):
        """Live task with steps < min_steps -- should fail."""
        task = {"id": "G4", "validators": ["contains_number", {"min_steps": 3}]}
        result = TaskResult(task_id="G4", condition="page_map_live", answer="2,847 open issues", steps=2)
        assert evaluate_result(task, result) is False

    def test_live_task_single_step_fails_min_steps(self):
        """Live task answered without navigation (steps=1) -- should fail when min_steps > 1."""
        task = {"id": "G4", "validators": ["contains_number", {"min_steps": 3}]}
        result = TaskResult(task_id="G4", condition="page_map_live", answer="2,847 open issues", steps=1)
        assert evaluate_result(task, result) is False

    def test_static_task_skips_min_steps(self):
        """Static task (steps=1, non-live) -- min_steps should be skipped."""
        task = {"id": "test", "validators": ["contains_number", {"min_steps": 3}]}
        result = TaskResult(task_id="test", condition="page_map", answer="42", steps=1)
        assert evaluate_result(task, result) is True


# ---------------------------------------------------------------------------
# 7. TestUrlContainsLiveValidator
# ---------------------------------------------------------------------------


class TestUrlContainsLiveValidator:
    """Tests for url_contains with final_url (live tasks)."""

    def test_url_contains_final_url(self):
        """url_contains checks final_url -- should pass."""
        task = {
            "id": "H1",
            "validation": "url_contains",
            "expected_url_pattern": "l=Python",
        }
        result = TaskResult(
            task_id="H1",
            condition="page_map_live",
            answer="",
            final_url="https://github.com/search?q=ml&l=Python",
        )
        assert evaluate_result(task, result) is True

    def test_url_contains_no_match(self):
        """url_contains with non-matching URL -- should fail."""
        task = {
            "id": "H1",
            "validation": "url_contains",
            "expected_url_pattern": "l=Python",
        }
        result = TaskResult(
            task_id="H1",
            condition="page_map_live",
            answer="",
            final_url="https://github.com/search?q=ml",
        )
        assert evaluate_result(task, result) is False
