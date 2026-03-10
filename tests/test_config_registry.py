# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S1 — config_registry.py.

Regression tests verify DEFAULT values match ALL original module constants.
"""

from __future__ import annotations

import dataclasses

from pagemap.core.config_registry import (
    DEFAULT_CLASSIFIER_CONFIG,
    DEFAULT_PRUNING_CONFIG,
    ClassifierConfig,
    PruningConfig,
)


class TestPruningConfigDefaults:
    """Verify every default matches the original pruner.py module constant."""

    def test_in_main_text_min(self):
        assert DEFAULT_PRUNING_CONFIG.in_main_text_min == 50

    def test_in_main_media_min(self):
        assert DEFAULT_PRUNING_CONFIG.in_main_media_min == 10

    def test_no_main_text_min(self):
        assert DEFAULT_PRUNING_CONFIG.no_main_text_min == 30

    def test_no_main_form_min(self):
        assert DEFAULT_PRUNING_CONFIG.no_main_form_min == 20

    def test_no_main_media_min(self):
        assert DEFAULT_PRUNING_CONFIG.no_main_media_min == 20

    def test_news_body_min(self):
        assert DEFAULT_PRUNING_CONFIG.news_body_min == 50

    def test_wiki_summary_min(self):
        assert DEFAULT_PRUNING_CONFIG.wiki_summary_min == 100

    def test_wiki_section_min(self):
        assert DEFAULT_PRUNING_CONFIG.wiki_section_min == 30

    def test_saas_desc_min(self):
        assert DEFAULT_PRUNING_CONFIG.saas_desc_min == 50

    def test_gov_body_min(self):
        assert DEFAULT_PRUNING_CONFIG.gov_body_min == 30

    def test_coupang_price_count_limit(self):
        assert DEFAULT_PRUNING_CONFIG.coupang_price_count_limit == 10

    def test_faq_body_min(self):
        assert DEFAULT_PRUNING_CONFIG.faq_body_min == 30

    def test_event_desc_min(self):
        assert DEFAULT_PRUNING_CONFIG.event_desc_min == 50

    def test_local_biz_desc_min(self):
        assert DEFAULT_PRUNING_CONFIG.local_biz_desc_min == 50


class TestClassifierConfigDefaults:
    """Verify defaults match page_classifier.py constants."""

    def test_default_threshold(self):
        assert DEFAULT_CLASSIFIER_CONFIG.default_threshold == 50

    def test_dom_cap(self):
        assert DEFAULT_CLASSIFIER_CONFIG.dom_cap == 40

    def test_thresholds_product_detail(self):
        assert DEFAULT_CLASSIFIER_CONFIG.thresholds["product_detail"] == 20

    def test_thresholds_error(self):
        assert DEFAULT_CLASSIFIER_CONFIG.thresholds["error"] == 25

    def test_thresholds_landing(self):
        assert DEFAULT_CLASSIFIER_CONFIG.thresholds["landing"] == 25

    def test_thresholds_count(self):
        assert len(DEFAULT_CLASSIFIER_CONFIG.thresholds) == 16

    def test_type_priority_count(self):
        assert len(DEFAULT_CLASSIFIER_CONFIG.type_priority) == 16

    def test_type_priority_product_detail_is_highest(self):
        assert DEFAULT_CLASSIFIER_CONFIG.type_priority["product_detail"] == 0

    def test_jsonld_weights_product(self):
        assert DEFAULT_CLASSIFIER_CONFIG.jsonld_weights["product_detail"] == 40

    def test_jsonld_weights_count(self):
        assert len(DEFAULT_CLASSIFIER_CONFIG.jsonld_weights) == 9


class TestFrozenDataclasses:
    def test_pruning_config_is_frozen(self):
        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            DEFAULT_PRUNING_CONFIG.in_main_text_min = 999  # type: ignore[misc]

    def test_classifier_config_is_frozen(self):
        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            DEFAULT_CLASSIFIER_CONFIG.default_threshold = 999  # type: ignore[misc]

    def test_dataclasses_replace_works(self):
        new = dataclasses.replace(DEFAULT_PRUNING_CONFIG, in_main_text_min=99)
        assert new.in_main_text_min == 99
        assert DEFAULT_PRUNING_CONFIG.in_main_text_min == 50  # original unchanged

    def test_classifier_replace_works(self):
        new = dataclasses.replace(DEFAULT_CLASSIFIER_CONFIG, dom_cap=60)
        assert new.dom_cap == 60
        assert DEFAULT_CLASSIFIER_CONFIG.dom_cap == 40


class TestPruningConfigCustom:
    def test_custom_values(self):
        cfg = PruningConfig(in_main_text_min=100, news_body_min=80)
        assert cfg.in_main_text_min == 100
        assert cfg.news_body_min == 80
        # Other fields keep defaults
        assert cfg.no_main_text_min == 30

    def test_slots(self):
        assert hasattr(PruningConfig, "__slots__")

    def test_all_fields_have_defaults(self):
        # All fields should have defaults — PruningConfig() should work
        cfg = PruningConfig()
        assert cfg is not None


class TestMappingProxyImmutability:
    """L1: dict fields on ClassifierConfig are wrapped in MappingProxyType."""

    def test_thresholds_immutable(self):
        import types

        assert isinstance(DEFAULT_CLASSIFIER_CONFIG.thresholds, types.MappingProxyType)
        import pytest

        with pytest.raises(TypeError):
            DEFAULT_CLASSIFIER_CONFIG.thresholds["product_detail"] = 999  # type: ignore[index]

    def test_type_priority_immutable(self):
        import types

        assert isinstance(DEFAULT_CLASSIFIER_CONFIG.type_priority, types.MappingProxyType)

    def test_jsonld_weights_immutable(self):
        import types

        assert isinstance(DEFAULT_CLASSIFIER_CONFIG.jsonld_weights, types.MappingProxyType)

    def test_replace_rewraps_as_proxy(self):
        """dataclasses.replace with new dict → still wrapped as MappingProxyType."""
        import types

        new = dataclasses.replace(DEFAULT_CLASSIFIER_CONFIG, thresholds={"product_detail": 30})
        assert isinstance(new.thresholds, types.MappingProxyType)
        assert new.thresholds["product_detail"] == 30

    def test_proxy_read_access(self):
        """MappingProxyType supports normal read operations."""
        assert DEFAULT_CLASSIFIER_CONFIG.thresholds["product_detail"] == 20
        assert "product_detail" in DEFAULT_CLASSIFIER_CONFIG.thresholds
        assert len(DEFAULT_CLASSIFIER_CONFIG.thresholds) == 16


class TestReplaceNoDoubleWrap:
    """Fix 1: dataclasses.replace must not double-wrap MappingProxyType."""

    def test_replace_no_double_wrap(self):
        new = dataclasses.replace(DEFAULT_CLASSIFIER_CONFIG, dom_cap=60)
        inner = new.thresholds
        assert type(inner).__name__ == "mappingproxy"
        # Values must be plain ints, not wrapped in another MappingProxyType
        assert all(isinstance(v, int) for v in inner.values())

    def test_replace_all_proxy_fields_no_double_wrap(self):
        new = dataclasses.replace(DEFAULT_CLASSIFIER_CONFIG, default_threshold=30)
        for attr in ("thresholds", "type_priority", "jsonld_weights"):
            proxy = getattr(new, attr)
            assert type(proxy).__name__ == "mappingproxy"
            assert all(isinstance(v, int) for v in proxy.values())


class TestClassifierConfigCustom:
    def test_custom_threshold(self):
        custom_thresholds = {"product_detail": 30}
        cfg = ClassifierConfig(thresholds=custom_thresholds, dom_cap=50)
        assert cfg.thresholds["product_detail"] == 30
        assert cfg.dom_cap == 50
