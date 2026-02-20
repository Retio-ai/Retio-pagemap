"""Tests for template cache module.

Tests: TemplateKey, TemplateData, PageTemplate, InMemoryTemplateCache
(store/lookup/invalidate, LRU, TTL, stats), learn_template, validate_template,
_infer_metadata_source, extract_template_domain, edge cases.
"""

from __future__ import annotations

import time

import pytest

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.aom_filter import AomFilterStats
from pagemap.pruning.pipeline import PruningResult
from pagemap.template_cache import (
    MAX_CONSECUTIVE_FAILURES,
    InMemoryTemplateCache,
    PageTemplate,
    TemplateCacheStats,
    TemplateData,
    TemplateKey,
    ValidationResult,
    _infer_card_strategy,
    _infer_pagination_param,
    extract_template_domain,
    infer_metadata_source,
    learn_template,
    validate_template,
)

# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def _make_template_key(**overrides) -> TemplateKey:
    defaults = {"domain": "coupang.com", "page_type": "product_detail"}
    defaults.update(overrides)
    return TemplateKey(**defaults)


def _make_template_data(**overrides) -> TemplateData:
    defaults = {
        "schema_name": "Product",
        "has_main": True,
        "has_json_ld": True,
        "metadata_source": "json_ld",
        "metadata_fields_found": frozenset({"name", "price", "rating"}),
        "card_strategy": None,
        "has_pagination": False,
        "pagination_param": None,
        "aom_removal_ratio": 0.6,
        "chunk_selection_ratio": 0.4,
    }
    defaults.update(overrides)
    return TemplateData(**defaults)


def _make_template(**overrides) -> PageTemplate:
    key = overrides.pop("key", _make_template_key())
    data = overrides.pop("data", _make_template_data())
    defaults = {
        "data": data,
        "key": key,
        "created_at": time.monotonic(),
        "source_url": "https://www.coupang.com/vp/products/123",
    }
    defaults.update(overrides)
    return PageTemplate(**defaults)


def _make_meta_chunk(text: str = "", attrs: dict | None = None) -> HtmlChunk:
    return HtmlChunk(
        xpath="/html/head/script[1]",
        html=f"<script>{text}</script>",
        text=text,
        tag="script",
        chunk_type=ChunkType.META,
        attrs=attrs or {},
    )


def _make_pruning_result(**overrides) -> PruningResult:
    defaults = {
        "site_id": "coupang.com",
        "page_id": "product_123",
        "chunk_count_total": 100,
        "chunk_count_selected": 40,
        "aom_filter_stats": AomFilterStats(total_nodes=200, removed_nodes=120),
        "meta_chunks": [],
        "heading_chunks": [],
        "selected_chunks": [],
    }
    defaults.update(overrides)
    return PruningResult(**defaults)


# =========================================================================
# extract_template_domain
# =========================================================================


class TestExtractTemplateDomain:
    def test_basic_domain(self):
        assert extract_template_domain("https://coupang.com/vp/products/123") == "coupang.com"

    def test_www_prefix_stripped(self):
        assert extract_template_domain("https://www.coupang.com/vp/products/123") == "coupang.com"

    def test_subdomain_preserved(self):
        assert extract_template_domain("https://m.coupang.com/products/1") == "m.coupang.com"

    def test_port_stripped(self):
        # urlparse().hostname strips port
        assert extract_template_domain("https://example.com:8080/page") == "example.com"

    def test_ip_address(self):
        assert extract_template_domain("http://192.168.1.1/page") == "192.168.1.1"

    def test_co_kr_domain(self):
        assert extract_template_domain("https://www.ssg.co.kr/item/123") == "ssg.co.kr"

    def test_empty_url(self):
        assert extract_template_domain("") == ""

    def test_invalid_url(self):
        assert extract_template_domain("not-a-url") == ""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://WWW.Example.COM/path", "example.com"),
            ("https://www.test.co.jp/search", "test.co.jp"),
        ],
    )
    def test_case_insensitive(self, url, expected):
        assert extract_template_domain(url) == expected


# =========================================================================
# TemplateKey
# =========================================================================


class TestTemplateKey:
    def test_frozen(self):
        key = _make_template_key()
        with pytest.raises(AttributeError):
            key.domain = "other.com"  # type: ignore[misc]

    def test_equality(self):
        a = TemplateKey("coupang.com", "product_detail")
        b = TemplateKey("coupang.com", "product_detail")
        assert a == b

    def test_hash_as_dict_key(self):
        a = TemplateKey("coupang.com", "product_detail")
        b = TemplateKey("coupang.com", "product_detail")
        d = {a: 1}
        assert d[b] == 1

    def test_different_keys_not_equal(self):
        a = TemplateKey("coupang.com", "product_detail")
        b = TemplateKey("coupang.com", "search_results")
        assert a != b


# =========================================================================
# TemplateData
# =========================================================================


class TestTemplateData:
    def test_frozen(self):
        data = _make_template_data()
        with pytest.raises(AttributeError):
            data.has_main = False  # type: ignore[misc]

    def test_equality(self):
        a = _make_template_data()
        b = _make_template_data()
        assert a == b

    def test_defaults(self):
        data = TemplateData()
        assert data.schema_name == ""
        assert data.has_main is False
        assert data.metadata_source == ""
        assert data.card_strategy is None


# =========================================================================
# InMemoryTemplateCache — Store
# =========================================================================


class TestTemplateCacheStore:
    def test_store_and_lookup(self):
        cache = InMemoryTemplateCache()
        tmpl = _make_template()
        cache.store(tmpl)
        found = cache.lookup(tmpl.key)
        assert found is not None
        assert found.data == tmpl.data

    def test_store_overwrite(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        tmpl1 = _make_template(key=key, data=_make_template_data(has_main=True))
        tmpl2 = _make_template(key=key, data=_make_template_data(has_main=False))

        cache.store(tmpl1)
        cache.store(tmpl2)
        found = cache.lookup(key)
        assert found is not None
        assert found.data.has_main is False
        assert cache.size == 1

    def test_different_keys(self):
        cache = InMemoryTemplateCache()
        key1 = TemplateKey("a.com", "product_detail")
        key2 = TemplateKey("b.com", "product_detail")
        cache.store(_make_template(key=key1))
        cache.store(_make_template(key=key2))
        assert cache.size == 2
        assert cache.lookup(key1) is not None
        assert cache.lookup(key2) is not None

    def test_store_increments_templates_created(self):
        cache = InMemoryTemplateCache()
        cache.store(_make_template())
        assert cache.stats.templates_created == 1

    def test_overwrite_does_not_increment_created(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))
        cache.store(_make_template(key=key))
        assert cache.stats.templates_created == 1


# =========================================================================
# InMemoryTemplateCache — Invalidate
# =========================================================================


class TestTemplateCacheInvalidate:
    def test_invalidate_single(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))
        assert cache.invalidate(key) is True
        assert cache.lookup(key) is None
        assert cache.stats.invalidations == 1

    def test_invalidate_nonexistent(self):
        cache = InMemoryTemplateCache()
        assert cache.invalidate(_make_template_key()) is False

    def test_invalidate_domain(self):
        cache = InMemoryTemplateCache()
        cache.store(_make_template(key=TemplateKey("coupang.com", "product_detail")))
        cache.store(_make_template(key=TemplateKey("coupang.com", "search_results")))
        cache.store(_make_template(key=TemplateKey("naver.com", "product_detail")))

        removed = cache.invalidate_domain("coupang.com")
        assert removed == 2
        assert cache.size == 1
        assert cache.lookup(TemplateKey("naver.com", "product_detail")) is not None

    def test_invalidate_domain_nonexistent(self):
        cache = InMemoryTemplateCache()
        assert cache.invalidate_domain("nope.com") == 0

    def test_invalidate_all(self):
        cache = InMemoryTemplateCache()
        cache.store(_make_template(key=TemplateKey("a.com", "product_detail")))
        cache.store(_make_template(key=TemplateKey("b.com", "product_detail")))
        cache.invalidate_all()
        assert cache.size == 0


# =========================================================================
# InMemoryTemplateCache — LRU
# =========================================================================


class TestTemplateCacheLRU:
    def test_lru_eviction(self):
        cache = InMemoryTemplateCache(max_entries=2)
        k1 = TemplateKey("a.com", "product_detail")
        k2 = TemplateKey("b.com", "product_detail")
        k3 = TemplateKey("c.com", "product_detail")

        cache.store(_make_template(key=k1))
        cache.store(_make_template(key=k2))
        cache.store(_make_template(key=k3))

        assert cache.size == 2
        assert cache.lookup(k1) is None  # evicted (oldest)
        assert cache.lookup(k2) is not None
        assert cache.lookup(k3) is not None
        assert cache.stats.evictions == 1

    def test_lru_access_refresh(self):
        cache = InMemoryTemplateCache(max_entries=2)
        k1 = TemplateKey("a.com", "product_detail")
        k2 = TemplateKey("b.com", "product_detail")
        k3 = TemplateKey("c.com", "product_detail")

        cache.store(_make_template(key=k1))
        cache.store(_make_template(key=k2))

        # Access k1 to move it to end
        cache.lookup(k1)

        # Now k2 is oldest — should be evicted
        cache.store(_make_template(key=k3))
        assert cache.lookup(k1) is not None
        assert cache.lookup(k2) is None  # evicted
        assert cache.lookup(k3) is not None


# =========================================================================
# InMemoryTemplateCache — TTL
# =========================================================================


class TestTemplateCacheTTL:
    def test_expired_returns_none(self):
        cache = InMemoryTemplateCache(ttl=0.01)  # 10ms TTL
        key = _make_template_key()
        cache.store(_make_template(key=key))
        time.sleep(0.02)
        assert cache.lookup(key) is None

    def test_non_expired_ok(self):
        cache = InMemoryTemplateCache(ttl=10.0)
        key = _make_template_key()
        cache.store(_make_template(key=key))
        assert cache.lookup(key) is not None


# =========================================================================
# InMemoryTemplateCache — Stats
# =========================================================================


class TestTemplateCacheStats:
    def test_hit_miss_tracking(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))

        cache.lookup(key)  # hit
        cache.lookup(TemplateKey("nope.com", "unknown"))  # miss

        assert cache.stats.hits == 1
        assert cache.stats.misses == 1

    def test_hit_rate(self):
        stats = TemplateCacheStats(hits=3, misses=7)
        assert stats.hit_rate == pytest.approx(0.3)

    def test_hit_rate_zero_total(self):
        stats = TemplateCacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_count_incremented(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))
        cache.lookup(key)
        cache.lookup(key)
        entry = cache.lookup(key)
        assert entry is not None
        assert entry.hit_count == 3

    def test_validation_counters(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))

        cache.record_validation_pass(key)
        cache.record_validation_pass(key)
        cache.record_validation_failure(key)

        assert cache.stats.validations_passed == 2
        assert cache.stats.validations_failed == 1


# =========================================================================
# Auto-invalidation on consecutive failures
# =========================================================================


class TestAutoInvalidation:
    def test_three_consecutive_failures_invalidates(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))

        for _ in range(MAX_CONSECUTIVE_FAILURES):
            cache.record_validation_failure(key)

        # Should be auto-invalidated
        assert cache.lookup(key) is None
        assert cache.stats.invalidations == 1

    def test_pass_resets_failure_count(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))

        cache.record_validation_failure(key)
        cache.record_validation_failure(key)
        cache.record_validation_pass(key)  # reset

        # Not invalidated yet — need 3 more consecutive
        cache.record_validation_failure(key)
        assert cache.lookup(key) is not None

    def test_failure_on_nonexistent_key(self):
        cache = InMemoryTemplateCache()
        # Should not raise
        cache.record_validation_failure(_make_template_key())


# =========================================================================
# validate_template
# =========================================================================


class TestValidateTemplate:
    def test_all_match(self):
        tmpl = _make_template(
            data=_make_template_data(
                has_main=True,
                metadata_source="json_ld",
                aom_removal_ratio=0.6,
                chunk_selection_ratio=0.4,
            )
        )
        result = validate_template(tmpl, True, "json_ld", 0.6, 0.4)
        assert result.passed is True
        assert result.mismatches == ()

    def test_has_main_mismatch(self):
        tmpl = _make_template(data=_make_template_data(has_main=True))
        result = validate_template(tmpl, False, "json_ld", 0.6, 0.4)
        assert result.passed is False
        assert any("has_main" in m for m in result.mismatches)

    def test_metadata_source_mismatch(self):
        tmpl = _make_template(data=_make_template_data(metadata_source="json_ld"))
        result = validate_template(tmpl, True, "og", 0.6, 0.4)
        assert result.passed is False
        assert any("metadata_source" in m for m in result.mismatches)

    def test_metadata_source_empty_actual_ok(self):
        """Empty actual source should not trigger mismatch."""
        tmpl = _make_template(data=_make_template_data(metadata_source="json_ld"))
        result = validate_template(tmpl, True, "", 0.6, 0.4)
        assert result.passed is True

    def test_aom_ratio_within_tolerance(self):
        tmpl = _make_template(data=_make_template_data(aom_removal_ratio=0.6))
        result = validate_template(tmpl, True, "json_ld", 0.85, 0.4)
        assert result.passed is True  # 0.85 - 0.6 = 0.25 < 0.3

    def test_aom_ratio_exceeds_tolerance(self):
        tmpl = _make_template(data=_make_template_data(aom_removal_ratio=0.6))
        result = validate_template(tmpl, True, "json_ld", 0.95, 0.4)
        assert result.passed is False
        assert any("aom_removal_ratio" in m for m in result.mismatches)

    def test_chunk_ratio_exceeds_tolerance(self):
        tmpl = _make_template(data=_make_template_data(chunk_selection_ratio=0.4))
        result = validate_template(tmpl, True, "json_ld", 0.6, 0.8)
        assert result.passed is False
        assert any("chunk_selection_ratio" in m for m in result.mismatches)

    def test_multiple_mismatches(self):
        tmpl = _make_template(
            data=_make_template_data(
                has_main=True,
                metadata_source="json_ld",
            )
        )
        result = validate_template(tmpl, False, "og", 0.6, 0.4)
        assert result.passed is False
        assert len(result.mismatches) == 2


# =========================================================================
# learn_template
# =========================================================================


class TestLearnTemplate:
    def test_basic_learning(self):
        key = _make_template_key()
        chunk_in_main = HtmlChunk(
            xpath="/html/body/main/div",
            html="<div>Test</div>",
            text="Test",
            tag="div",
            chunk_type=ChunkType.TEXT_BLOCK,
            in_main=True,
        )
        json_ld_chunk = _make_meta_chunk(
            '{"@type":"Product","name":"Test"}',
            attrs={"type": "application/ld+json"},
        )
        result = _make_pruning_result(
            selected_chunks=[chunk_in_main],
            meta_chunks=[json_ld_chunk],
        )
        metadata = {"name": "Test Product", "price": "10000"}

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata=metadata,
            source_url="https://coupang.com/vp/products/123",
        )

        assert tmpl.key == key
        assert tmpl.data.has_main is True
        assert tmpl.data.has_json_ld is True
        assert tmpl.data.schema_name == "Product"
        assert "name" in tmpl.data.metadata_fields_found
        assert "price" in tmpl.data.metadata_fields_found

    def test_no_main_no_json_ld(self):
        key = _make_template_key()
        chunk = HtmlChunk(
            xpath="/html/body/div",
            html="<div>Test</div>",
            text="Test",
            tag="div",
            chunk_type=ChunkType.TEXT_BLOCK,
            in_main=False,
        )
        result = _make_pruning_result(selected_chunks=[chunk], meta_chunks=[])
        metadata = {"name": "Test"}

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata=metadata,
            source_url="https://example.com/product/1",
        )

        assert tmpl.data.has_main is False
        assert tmpl.data.has_json_ld is False

    def test_aom_removal_ratio(self):
        key = _make_template_key()
        result = _make_pruning_result(
            aom_filter_stats=AomFilterStats(total_nodes=100, removed_nodes=70),
        )

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata={},
            source_url="https://example.com/p/1",
        )
        assert tmpl.data.aom_removal_ratio == pytest.approx(0.7)

    def test_chunk_selection_ratio(self):
        key = _make_template_key()
        result = _make_pruning_result(chunk_count_total=200, chunk_count_selected=50)

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata={},
            source_url="https://example.com/p/1",
        )
        assert tmpl.data.chunk_selection_ratio == pytest.approx(0.25)

    def test_card_strategy_json_ld_itemlist(self):
        key = _make_template_key(page_type="search_results")
        result = _make_pruning_result()
        metadata = {
            "items": [
                {"name": "Product A", "url": "/p/1", "position": 1},
                {"name": "Product B", "url": "/p/2", "position": 2},
            ]
        }

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata=metadata,
            source_url="https://example.com/search?q=test",
        )
        assert tmpl.data.card_strategy == "json_ld_itemlist"

    def test_pagination_detection(self):
        key = _make_template_key(page_type="search_results")
        result = _make_pruning_result()
        html = '<a href="/search?q=test&page=2">2</a><a href="/search?q=test&page=3">3</a>'

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata={},
            source_url="https://example.com/search?q=test",
            raw_html=html,
        )
        assert tmpl.data.has_pagination is True
        assert tmpl.data.pagination_param == "page"

    def test_no_pagination_for_product_detail(self):
        key = _make_template_key(page_type="product_detail")
        result = _make_pruning_result()
        html = '<a href="/search?page=2">2</a>'

        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata={},
            source_url="https://example.com/product/1",
            raw_html=html,
        )
        # product_detail pages don't check pagination
        assert tmpl.data.has_pagination is False
        assert tmpl.data.pagination_param is None


# =========================================================================
# _infer_metadata_source
# =========================================================================


class TestInferMetadataSource:
    def test_json_ld_detected(self):
        chunk = _make_meta_chunk(
            '{"@type":"Product","name":"Test","offers":{"price":"100"}}',
            attrs={"type": "application/ld+json"},
        )
        metadata = {"name": "Test", "price": 100}
        assert infer_metadata_source(metadata, [chunk]) == "json_ld"

    def test_empty_metadata(self):
        assert infer_metadata_source({}, []) == ""

    def test_og_fallback(self):
        og_chunk = HtmlChunk(
            xpath="/html/head/meta[1]",
            html='<meta property="og:title" content="Test"/>',
            text="",
            tag="meta",
            chunk_type=ChunkType.META,
            attrs={"property": "og:title", "content": "Test"},
        )
        metadata = {"name": "Test"}
        assert infer_metadata_source(metadata, [og_chunk]) == "og"


# =========================================================================
# _infer_card_strategy
# =========================================================================


class TestInferCardStrategy:
    def test_json_ld_itemlist(self):
        metadata = {
            "items": [
                {"name": "A", "url": "/a", "position": 1},
            ]
        }
        assert _infer_card_strategy(metadata) == "json_ld_itemlist"

    def test_no_items(self):
        assert _infer_card_strategy({"name": "Product"}) is None

    def test_empty_items(self):
        assert _infer_card_strategy({"items": []}) is None

    def test_none_metadata(self):
        assert _infer_card_strategy(None) is None


# =========================================================================
# _infer_pagination_param
# =========================================================================


class TestInferPaginationParam:
    def test_page_param(self):
        html = '<a href="/list?page=2">Next</a>'
        assert _infer_pagination_param(html) == "page"

    def test_p_param(self):
        html = '<a href="/list?p=3">3</a>'
        assert _infer_pagination_param(html) == "p"

    def test_no_pagination(self):
        html = "<div>No pagination here</div>"
        assert _infer_pagination_param(html) is None

    def test_pageNo_param(self):
        html = '<a href="/list?pageNo=5">5</a>'
        assert _infer_pagination_param(html) == "pageNo"


# =========================================================================
# Edge cases
# =========================================================================


class TestEdgeCases:
    def test_unknown_page_type_not_cached(self):
        """Verify the design: unknown page_type templates are skippable."""
        cache = InMemoryTemplateCache()
        key = TemplateKey("example.com", "unknown")
        tmpl = _make_template(key=key)
        # The cache itself doesn't enforce this — the caller (page_map_builder) does
        cache.store(tmpl)
        assert cache.lookup(key) is not None

    def test_empty_cache_lookup(self):
        cache = InMemoryTemplateCache()
        assert cache.lookup(_make_template_key()) is None

    def test_zero_total_nodes_aom(self):
        """Zero total nodes should not cause division by zero."""
        key = _make_template_key()
        result = _make_pruning_result(
            aom_filter_stats=AomFilterStats(total_nodes=0, removed_nodes=0),
            chunk_count_total=0,
            chunk_count_selected=0,
        )
        tmpl = learn_template(
            key=key,
            schema_name="Product",
            pruning_result=result,
            metadata={},
            source_url="https://example.com/p/1",
        )
        assert tmpl.data.aom_removal_ratio == 0.0
        assert tmpl.data.chunk_selection_ratio == 0.0

    def test_validation_result_frozen(self):
        vr = ValidationResult(passed=True, mismatches=())
        with pytest.raises(AttributeError):
            vr.passed = False  # type: ignore[misc]

    def test_template_data_frozen(self):
        td = TemplateData()
        with pytest.raises(AttributeError):
            td.has_main = True  # type: ignore[misc]

    def test_last_used_at_updated_on_lookup(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key))
        entry = cache.lookup(key)
        assert entry is not None
        assert entry.last_used_at > 0

    def test_store_overwrites_with_different_data(self):
        cache = InMemoryTemplateCache()
        key = _make_template_key()
        cache.store(_make_template(key=key, data=_make_template_data(schema_name="Product")))
        cache.store(_make_template(key=key, data=_make_template_data(schema_name="NewsArticle")))
        entry = cache.lookup(key)
        assert entry is not None
        assert entry.data.schema_name == "NewsArticle"
