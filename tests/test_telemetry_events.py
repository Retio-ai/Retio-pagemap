# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pagemap.telemetry.events — TypedDict payloads and builder functions."""

from __future__ import annotations

import json
from collections import Counter

from pagemap.telemetry import events


class TestEventTypeConstants:
    def test_all_constants_are_strings(self):
        """All event type constants should be dotted strings."""
        constants = [
            events.NAVIGATION_START,
            events.CACHE_HIT,
            events.CACHE_REFRESH,
            events.FULL_BUILD,
            events.PIPELINE_COMPLETED,
            events.PIPELINE_TIMEOUT,
            events.ACTION_START,
            events.ACTION_DOM_CHANGE,
            events.ACTION_RESULT,
            events.FILL_FORM_DOM_CHANGE,
            events.SCROLL,
            events.WAIT_FOR_RESULT,
            events.BATCH_START,
            events.BATCH_URL_RESULT,
            events.BATCH_COMPLETE,
            events.PREPROCESS_COMPLETE,
            events.CHUNK_DECOMPOSE,
            events.AOM_FILTER_COMPLETE,
            events.PRUNE_DECISIONS,
            events.COMPRESSION_COMPLETE,
            events.PRUNED_CONTEXT_COMPLETE,
            events.RESOURCE_GUARD_TRIGGERED,
            events.HIDDEN_CONTENT_REMOVED,
            events.TOOL_ERROR,
        ]
        for c in constants:
            assert isinstance(c, str)
            assert c.startswith("pagemap.")

    def test_all_constants_unique(self):
        constants = [
            events.NAVIGATION_START,
            events.CACHE_HIT,
            events.CACHE_REFRESH,
            events.FULL_BUILD,
            events.PIPELINE_COMPLETED,
            events.PIPELINE_TIMEOUT,
            events.ACTION_START,
            events.ACTION_DOM_CHANGE,
            events.ACTION_RESULT,
            events.FILL_FORM_DOM_CHANGE,
            events.SCROLL,
            events.WAIT_FOR_RESULT,
            events.BATCH_START,
            events.BATCH_URL_RESULT,
            events.BATCH_COMPLETE,
            events.PREPROCESS_COMPLETE,
            events.CHUNK_DECOMPOSE,
            events.AOM_FILTER_COMPLETE,
            events.PRUNE_DECISIONS,
            events.COMPRESSION_COMPLETE,
            events.PRUNED_CONTEXT_COMPLETE,
            events.RESOURCE_GUARD_TRIGGERED,
            events.HIDDEN_CONTENT_REMOVED,
            events.TOOL_ERROR,
        ]
        assert len(constants) == len(set(constants))


class TestPayloadBuilders:
    def test_navigation_start(self):
        p = events.navigation_start(url="https://example.com")
        assert p["url"] == "https://example.com"
        assert json.dumps(p)  # JSON serializable

    def test_cache_hit(self):
        p = events.cache_hit(tier="A")
        assert p["tier"] == "A"

    def test_cache_refresh(self):
        p = events.cache_refresh(tier="B")
        assert p["tier"] == "B"

    def test_full_build(self):
        p = events.full_build(tier="C")
        assert p["tier"] == "C"

    def test_pipeline_completed(self):
        p = events.pipeline_completed(
            tier="C",
            interactables=15,
            pruned_tokens=2000,
            stage_timings={"navigation": 100.5, "build": 500.2},
            page_type="article",
        )
        assert p["tier"] == "C"
        assert p["interactables"] == 15
        assert p["pruned_tokens"] == 2000
        assert p["page_type"] == "article"
        assert json.dumps(p)

    def test_pipeline_timeout(self):
        p = events.pipeline_timeout(timed_out_at="build", hint="Page is slow")
        assert p["timed_out_at"] == "build"

    def test_action_start(self):
        p = events.action_start(ref=5, action="click", role="button", affordance="click")
        assert p["ref"] == 5
        assert p["action"] == "click"

    def test_action_dom_change(self):
        p = events.action_dom_change(severity="major", reasons=["content_hash_changed"])
        assert p["severity"] == "major"
        assert isinstance(p["reasons"], list)

    def test_action_result(self):
        p = events.action_result(change="navigation", refs_expired=True)
        assert p["change"] == "navigation"
        assert p["refs_expired"] is True

    def test_fill_form_dom_change(self):
        p = events.fill_form_dom_change(severity="minor", reasons=["text_changed"])
        assert p["severity"] == "minor"

    def test_scroll(self):
        p = events.scroll(direction="down", pixels=800, scroll_percent=45)
        assert p["direction"] == "down"
        assert p["pixels"] == 800

    def test_wait_for_result(self):
        p = events.wait_for_result(elapsed=2.5, success=True, mode="appear")
        assert p["elapsed"] == 2.5
        assert p["success"] is True

    def test_batch_start(self):
        p = events.batch_start(urls_count=5, valid_count=4)
        assert p["urls_count"] == 5

    def test_batch_url_result(self):
        p = events.batch_url_result(url="https://example.com", success=True)
        assert p["url"] == "https://example.com"

    def test_batch_complete(self):
        p = events.batch_complete(elapsed_ms=5000, success=4, failed=1)
        assert p["elapsed_ms"] == 5000

    def test_preprocess_complete(self):
        p = events.preprocess_complete(json_ld_count=2, og_count=5, rsc_count=0)
        assert p["json_ld_count"] == 2

    def test_chunk_decompose(self):
        p = events.chunk_decompose(chunk_count=42, has_main=True)
        assert p["chunk_count"] == 42
        assert p["has_main"] is True

    def test_aom_filter_complete(self):
        p = events.aom_filter_complete(
            total_nodes=100,
            removed_nodes=30,
            removal_reasons=Counter({"semantic-nav": 20, "noise-class": 10}),
        )
        assert p["total_nodes"] == 100
        assert p["removed_nodes"] == 30
        assert isinstance(p["removal_reasons"], dict)
        assert p["removal_reasons"]["semantic-nav"] == 20

    def test_prune_decisions(self):
        p = events.prune_decisions(
            kept=15,
            removed=25,
            schema_name="Product",
            kept_reasons=Counter({"KEEP_HEADING": 10, "KEEP_META": 5}),
            removed_reasons=Counter({"NO_MATCH": 25}),
        )
        assert p["kept"] == 15
        assert p["removed"] == 25
        assert isinstance(p["kept_reasons"], dict)
        assert isinstance(p["removed_reasons"], dict)

    def test_compression_complete(self):
        p = events.compression_complete(before_len=10000, after_len=5000)
        assert p["before_len"] == 10000
        assert p["after_len"] == 5000

    def test_pruned_context_complete(self):
        p = events.pruned_context_complete(
            tokens=2500,
            budget=5000,
            prune_ms=120.5,
            meta_ms=15.2,
            compress_ms=30.1,
            template_status="hit",
            page_type="product_detail",
            schema_name="Product",
        )
        assert p["tokens"] == 2500
        assert p["budget"] == 5000
        assert p["template_status"] == "hit"
        assert p["schema_name"] == "Product"

    def test_resource_guard_triggered(self):
        p = events.resource_guard_triggered(guard="html_size", value=6_000_000, limit=5_242_880)
        assert p["guard"] == "html_size"
        assert p["value"] == 6_000_000
        assert p["limit"] == 5_242_880
        assert json.dumps(p)

    def test_hidden_content_removed(self):
        p = events.hidden_content_removed(hidden_removed=12)
        assert p["hidden_removed"] == 12
        assert json.dumps(p)

    def test_tool_error(self):
        p = events.tool_error(context="get_page_map", error_type="TimeoutError")
        assert p["context"] == "get_page_map"
        assert p["error_type"] == "TimeoutError"
        assert json.dumps(p)


class TestJsonSerializable:
    """All payloads should be JSON serializable."""

    def test_all_builders_produce_json_serializable(self):
        payloads = [
            events.navigation_start(url="https://x.com"),
            events.cache_hit(tier="A"),
            events.cache_refresh(tier="B"),
            events.full_build(tier="C"),
            events.pipeline_completed(
                tier="C", interactables=1, pruned_tokens=100, stage_timings={}, page_type="unknown"
            ),
            events.pipeline_timeout(timed_out_at="build", hint="slow"),
            events.action_start(ref=1, action="click", role="button", affordance="click"),
            events.action_dom_change(severity="major", reasons=["a"]),
            events.action_result(change="none", refs_expired=False),
            events.fill_form_dom_change(severity="minor", reasons=[]),
            events.scroll(direction="up", pixels=500, scroll_percent=0),
            events.wait_for_result(elapsed=1.0, success=True, mode="appear"),
            events.batch_start(urls_count=2, valid_count=2),
            events.batch_url_result(url="https://x.com", success=True),
            events.batch_complete(elapsed_ms=100, success=1, failed=1),
            events.preprocess_complete(json_ld_count=0, og_count=0, rsc_count=0),
            events.chunk_decompose(chunk_count=10, has_main=False),
            events.aom_filter_complete(total_nodes=50, removed_nodes=10, removal_reasons={}),
            events.prune_decisions(kept=5, removed=5, schema_name="Product", kept_reasons={}, removed_reasons={}),
            events.compression_complete(before_len=1000, after_len=500),
            events.pruned_context_complete(
                tokens=100,
                budget=500,
                prune_ms=10,
                meta_ms=5,
                compress_ms=3,
                template_status="miss",
                page_type="unknown",
                schema_name="Generic",
            ),
            events.resource_guard_triggered(guard="dom_nodes", value=60000, limit=50000),
            events.hidden_content_removed(hidden_removed=5),
            events.tool_error(context="execute_action", error_type="ValueError"),
        ]
        for p in payloads:
            serialized = json.dumps(p)
            assert isinstance(serialized, str)
