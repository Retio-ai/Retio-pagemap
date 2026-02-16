"""Tests for interactive_detector module.

Tests cover:
1. Tier 1-2 detection from AX tree snapshots
2. Affordance classification (role → affordance mapping)
3. Region assignment from landmark ancestors
4. Deduplication of same-name elements
5. Option extraction from combobox/listbox
6. Edge cases: empty tree, no interactive elements, deeply nested
"""

from __future__ import annotations

from pagemap import Interactable
from pagemap.interactive_detector import (
    AFFORDANCE_MAP,
    INTERACTIVE_ROLES,
    _classify_tier,
    _extract_options,
    _walk_ax_tree,
)

# ── Test fixtures ──────────────────────────────────────────────────────


def _make_ax_tree(nodes: list[dict]) -> dict:
    """Helper to build a minimal AX tree structure."""
    return {
        "role": "WebArea",
        "name": "Test Page",
        "children": nodes,
    }


SIMPLE_BUTTON_TREE = _make_ax_tree(
    [
        {"role": "button", "name": "장바구니 담기", "children": []},
    ]
)

SIMPLE_LINK_TREE = _make_ax_tree(
    [
        {"role": "link", "name": "홈페이지", "children": []},
    ]
)

SEARCHBOX_TREE = _make_ax_tree(
    [
        {
            "role": "banner",
            "name": "",
            "children": [
                {"role": "searchbox", "name": "쿠팡 검색", "children": []},
            ],
        },
    ]
)

COMBOBOX_WITH_OPTIONS = _make_ax_tree(
    [
        {
            "role": "combobox",
            "name": "사이즈 선택",
            "children": [
                {"role": "option", "name": "S", "children": []},
                {"role": "option", "name": "M", "children": []},
                {"role": "option", "name": "L", "children": []},
                {"role": "option", "name": "XL", "children": []},
            ],
        },
    ]
)

NESTED_LANDMARK_TREE = _make_ax_tree(
    [
        {
            "role": "banner",
            "name": "",
            "children": [
                {"role": "link", "name": "로고", "children": []},
                {"role": "searchbox", "name": "검색", "children": []},
            ],
        },
        {
            "role": "main",
            "name": "",
            "children": [
                {"role": "button", "name": "구매하기", "children": []},
                {"role": "checkbox", "name": "옵션 동의", "children": []},
            ],
        },
        {
            "role": "contentinfo",
            "name": "",
            "children": [
                {"role": "link", "name": "고객센터", "children": []},
            ],
        },
    ]
)

DUPLICATE_NAMES_TREE = _make_ax_tree(
    [
        {"role": "button", "name": "삭제", "children": []},
        {"role": "button", "name": "삭제", "children": []},
        {"role": "button", "name": "삭제", "children": []},
    ]
)

UNNAMED_ELEMENTS_TREE = _make_ax_tree(
    [
        {"role": "button", "name": "", "children": []},
        {"role": "textbox", "name": "", "children": []},
        {"role": "link", "name": "", "children": []},
    ]
)

EMPTY_TREE = _make_ax_tree([])

MIXED_ROLES_TREE = _make_ax_tree(
    [
        {"role": "heading", "name": "상품 정보", "children": []},
        {"role": "paragraph", "name": "", "children": []},
        {"role": "img", "name": "상품 이미지", "children": []},
        {"role": "button", "name": "장바구니", "children": []},
        {"role": "separator", "name": "", "children": []},
        {"role": "link", "name": "리뷰 보기", "children": []},
    ]
)


# ── Tests ──────────────────────────────────────────────────────────────


class TestTierClassification:
    """Test Tier 1 vs Tier 2 classification."""

    def test_named_element_is_tier1(self):
        assert _classify_tier("button", "장바구니 담기") == 1

    def test_unnamed_element_is_tier2(self):
        assert _classify_tier("button", "") == 2

    def test_whitespace_name_is_tier2(self):
        assert _classify_tier("button", "   ") == 2


class TestAffordanceMapping:
    """Test role → affordance mapping."""

    def test_button_is_click(self):
        assert AFFORDANCE_MAP["button"] == "click"

    def test_link_is_click(self):
        assert AFFORDANCE_MAP["link"] == "click"

    def test_searchbox_is_type(self):
        assert AFFORDANCE_MAP["searchbox"] == "type"

    def test_textbox_is_type(self):
        assert AFFORDANCE_MAP["textbox"] == "type"

    def test_combobox_is_select(self):
        assert AFFORDANCE_MAP["combobox"] == "select"

    def test_checkbox_is_click(self):
        assert AFFORDANCE_MAP["checkbox"] == "click"

    def test_all_affordances_are_valid_actions(self):
        """모든 affordance가 서버에서 실행 가능한 액션인지 검증."""
        from pagemap.server import VALID_ACTIONS
        for role, affordance in AFFORDANCE_MAP.items():
            assert affordance in VALID_ACTIONS, (
                f"Role '{role}' has affordance '{affordance}' "
                f"which is not in VALID_ACTIONS: {VALID_ACTIONS}"
            )

    def test_all_interactive_roles_have_affordance(self):
        for role in INTERACTIVE_ROLES:
            assert role in AFFORDANCE_MAP, f"Missing affordance for role: {role}"


class TestWalkAXTree:
    """Test AX tree walking and element extraction."""

    def test_simple_button(self):
        results: list[Interactable] = []
        _walk_ax_tree(SIMPLE_BUTTON_TREE, results, [0])
        assert len(results) == 1
        assert results[0].role == "button"
        assert results[0].name == "장바구니 담기"
        assert results[0].affordance == "click"
        assert results[0].ref == 1

    def test_simple_link(self):
        results: list[Interactable] = []
        _walk_ax_tree(SIMPLE_LINK_TREE, results, [0])
        assert len(results) == 1
        assert results[0].role == "link"
        assert results[0].affordance == "click"

    def test_searchbox_in_banner(self):
        results: list[Interactable] = []
        _walk_ax_tree(SEARCHBOX_TREE, results, [0])
        assert len(results) == 1
        assert results[0].role == "searchbox"
        assert results[0].affordance == "type"
        assert results[0].region == "header"  # banner → header

    def test_combobox_extracts_options(self):
        results: list[Interactable] = []
        _walk_ax_tree(COMBOBOX_WITH_OPTIONS, results, [0])
        # combobox itself + options
        combobox = [r for r in results if r.role == "combobox"]
        assert len(combobox) == 1
        assert combobox[0].options == ["S", "M", "L", "XL"]
        assert combobox[0].affordance == "select"

    def test_empty_tree(self):
        results: list[Interactable] = []
        _walk_ax_tree(EMPTY_TREE, results, [0])
        assert len(results) == 0

    def test_non_interactive_roles_skipped(self):
        results: list[Interactable] = []
        _walk_ax_tree(MIXED_ROLES_TREE, results, [0])
        roles = [r.role for r in results]
        assert "heading" not in roles
        assert "paragraph" not in roles
        assert "img" not in roles
        assert "separator" not in roles
        assert "button" in roles
        assert "link" in roles


class TestRegionAssignment:
    """Test region inheritance from landmark ancestors."""

    def test_landmark_regions(self):
        results: list[Interactable] = []
        _walk_ax_tree(NESTED_LANDMARK_TREE, results, [0])

        by_name = {r.name: r for r in results}
        assert by_name["로고"].region == "header"
        assert by_name["검색"].region == "header"
        assert by_name["구매하기"].region == "main"
        assert by_name["옵션 동의"].region == "main"
        assert by_name["고객센터"].region == "footer"


class TestDeduplication:
    """Test deduplication of same-name elements."""

    def test_duplicate_names_deduplicated(self):
        results: list[Interactable] = []
        _walk_ax_tree(DUPLICATE_NAMES_TREE, results, [0])
        # Should only get 1 "삭제" button (dedup by role:name)
        assert len(results) == 1
        assert results[0].name == "삭제"

    def test_unnamed_elements_not_deduplicated(self):
        results: list[Interactable] = []
        _walk_ax_tree(UNNAMED_ELEMENTS_TREE, results, [0])
        # Unnamed elements are NOT deduplicated (different roles anyway)
        assert len(results) == 3


class TestOptionExtraction:
    """Test option extraction from select-type nodes."""

    def test_direct_options(self):
        node = {
            "role": "combobox",
            "name": "Size",
            "children": [
                {"role": "option", "name": "Small", "children": []},
                {"role": "option", "name": "Medium", "children": []},
            ],
        }
        options = _extract_options(node)
        assert options == ["Small", "Medium"]

    def test_nested_group_options(self):
        node = {
            "role": "listbox",
            "name": "Color",
            "children": [
                {
                    "role": "group",
                    "name": "Basic",
                    "children": [
                        {"role": "option", "name": "Red", "children": []},
                        {"role": "option", "name": "Blue", "children": []},
                    ],
                },
            ],
        }
        options = _extract_options(node)
        assert options == ["Red", "Blue"]

    def test_empty_options(self):
        node = {"role": "combobox", "name": "Empty", "children": []}
        options = _extract_options(node)
        assert options == []


class TestSequentialNumbering:
    """Test that ref numbers are assigned sequentially."""

    def test_sequential_refs(self):
        tree = _make_ax_tree(
            [
                {"role": "button", "name": "First", "children": []},
                {"role": "link", "name": "Second", "children": []},
                {"role": "textbox", "name": "Third", "children": []},
            ]
        )
        results: list[Interactable] = []
        _walk_ax_tree(tree, results, [0])
        refs = [r.ref for r in results]
        assert refs == [1, 2, 3]
