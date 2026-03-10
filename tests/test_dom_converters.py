# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.core.dom_converters — CDP AX tree conversion."""

from pagemap.core.dom_converters import _cdp_ax_nodes_to_tree, _extract_name_source


class TestExtractNameSource:
    """Tests for _extract_name_source()."""

    def test_attribute_aria_label(self):
        name_obj = {
            "value": "Submit",
            "sources": [
                {"type": "attribute", "attribute": "aria-label", "value": {"value": "Submit"}},
            ],
        }
        assert _extract_name_source(name_obj) == "aria-label"

    def test_attribute_title(self):
        name_obj = {
            "value": "Help",
            "sources": [
                {"type": "attribute", "attribute": "title", "value": {"value": "Help"}},
            ],
        }
        assert _extract_name_source(name_obj) == "title"

    def test_attribute_placeholder(self):
        name_obj = {
            "value": "Search...",
            "sources": [
                {"type": "attribute", "attribute": "placeholder", "value": {"value": "Search..."}},
            ],
        }
        assert _extract_name_source(name_obj) == "placeholder"

    def test_contents_source(self):
        name_obj = {
            "value": "Click me",
            "sources": [
                {"type": "contents", "value": {"value": "Click me"}},
            ],
        }
        assert _extract_name_source(name_obj) == "contents"

    def test_related_element(self):
        name_obj = {
            "value": "Username",
            "sources": [
                {"type": "relatedElement", "value": {"value": "Username"}},
            ],
        }
        assert _extract_name_source(name_obj) == "labelledby"

    def test_first_valid_source_wins(self):
        """When multiple sources have values, the first one wins."""
        name_obj = {
            "value": "Submit",
            "sources": [
                {"type": "attribute", "attribute": "aria-label", "value": {"value": "Submit"}},
                {"type": "contents", "value": {"value": "Submit"}},
            ],
        }
        assert _extract_name_source(name_obj) == "aria-label"

    def test_skips_empty_value_sources(self):
        """Sources with empty values are skipped."""
        name_obj = {
            "value": "Click me",
            "sources": [
                {"type": "attribute", "attribute": "aria-label", "value": {"value": ""}},
                {"type": "contents", "value": {"value": "Click me"}},
            ],
        }
        assert _extract_name_source(name_obj) == "contents"

    def test_no_sources_returns_empty(self):
        name_obj = {"value": "something"}
        assert _extract_name_source(name_obj) == ""

    def test_empty_sources_list_returns_empty(self):
        name_obj = {"value": "something", "sources": []}
        assert _extract_name_source(name_obj) == ""

    def test_non_dict_returns_empty(self):
        assert _extract_name_source("not a dict") == ""

    def test_none_returns_empty(self):
        assert _extract_name_source(None) == ""


class TestCdpAxNodesToTreeNameSource:
    """Tests that name_source is propagated through _cdp_ax_nodes_to_tree."""

    def test_name_source_in_tree_node(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "button"},
                "name": {
                    "value": "Submit",
                    "sources": [
                        {"type": "attribute", "attribute": "aria-label", "value": {"value": "Submit"}},
                    ],
                },
                "properties": [],
                "childIds": [],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree is not None
        assert tree["name_source"] == "aria-label"

    def test_name_source_empty_when_no_sources(self):
        nodes = [
            {
                "nodeId": "1",
                "role": {"value": "button"},
                "name": {"value": "OK"},
                "properties": [],
                "childIds": [],
            }
        ]
        tree = _cdp_ax_nodes_to_tree(nodes)
        assert tree is not None
        assert tree["name_source"] == ""
