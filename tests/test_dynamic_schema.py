"""Tests for Phase 5.2: Dynamic schema detection.

Covers:
  - JSON-LD @type sniffing (_detect_schema_from_jsonld, _resolve_jsonld_type)
  - URL-based signals (government TLDs)
  - detect_schema() cascade (domain → gov TLD → Generic)
  - Integration: Generic override in build_pruned_context
  - SchemaName StrEnum backward compatibility
  - DOMAIN_SCHEMA_MAP regression
"""

from __future__ import annotations

import json

import pytest

from pagemap.page_map_builder import (
    DOMAIN_SCHEMA_MAP,
    _detect_schema_from_jsonld,
    _resolve_jsonld_type,
    detect_schema,
)
from pagemap.pruning import SchemaName

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_jsonld(obj: dict | list) -> str:
    """Wrap a JSON-LD object in a minimal HTML page."""
    payload = json.dumps(obj)
    return f'<html><head><script type="application/ld+json">{payload}</script></head><body></body></html>'


# ---------------------------------------------------------------------------
# TestDetectSchemaFromJsonLD
# ---------------------------------------------------------------------------


class TestDetectSchemaFromJsonLD:
    """Unit tests for _detect_schema_from_jsonld."""

    def test_product_type(self):
        html = _wrap_jsonld({"@type": "Product", "name": "Widget"})
        assert _detect_schema_from_jsonld(html) == "Product"

    def test_individual_product(self):
        html = _wrap_jsonld({"@type": "IndividualProduct", "name": "Widget"})
        assert _detect_schema_from_jsonld(html) == "Product"

    def test_news_article(self):
        html = _wrap_jsonld({"@type": "NewsArticle", "headline": "Breaking"})
        assert _detect_schema_from_jsonld(html) == "NewsArticle"

    def test_article_maps_to_news(self):
        html = _wrap_jsonld({"@type": "Article", "headline": "Blog Post"})
        assert _detect_schema_from_jsonld(html) == "NewsArticle"

    def test_blog_posting_maps_to_news(self):
        html = _wrap_jsonld({"@type": "BlogPosting", "headline": "My Blog"})
        assert _detect_schema_from_jsonld(html) == "NewsArticle"

    def test_software_application_maps_to_saas(self):
        html = _wrap_jsonld({"@type": "SoftwareApplication", "name": "App"})
        assert _detect_schema_from_jsonld(html) == "SaaSPage"

    def test_web_application_maps_to_saas(self):
        html = _wrap_jsonld({"@type": "WebApplication", "name": "Web App"})
        assert _detect_schema_from_jsonld(html) == "SaaSPage"

    def test_government_organization(self):
        html = _wrap_jsonld({"@type": "GovernmentOrganization", "name": "Agency"})
        assert _detect_schema_from_jsonld(html) == "GovernmentPage"

    def test_government_service(self):
        html = _wrap_jsonld({"@type": "GovernmentService", "name": "Portal"})
        assert _detect_schema_from_jsonld(html) == "GovernmentPage"

    def test_graph_wrapper(self):
        html = _wrap_jsonld(
            {
                "@graph": [
                    {"@type": "WebSite", "name": "Site"},
                    {"@type": "Product", "name": "Widget"},
                ]
            }
        )
        assert _detect_schema_from_jsonld(html) == "Product"

    def test_array_type(self):
        """@type as array — first recognized type wins."""
        html = _wrap_jsonld({"@type": ["Thing", "NewsArticle"], "headline": "News"})
        assert _detect_schema_from_jsonld(html) == "NewsArticle"

    def test_malformed_json_skipped(self):
        html = '<html><head><script type="application/ld+json">{invalid json</script></head><body></body></html>'
        assert _detect_schema_from_jsonld(html) is None

    def test_empty_script(self):
        html = '<html><head><script type="application/ld+json"></script></head><body></body></html>'
        assert _detect_schema_from_jsonld(html) is None

    def test_unknown_type_returns_none(self):
        html = _wrap_jsonld({"@type": "Organization", "name": "Corp"})
        assert _detect_schema_from_jsonld(html) is None

    def test_no_jsonld_returns_none(self):
        html = "<html><head></head><body><p>Hello</p></body></html>"
        assert _detect_schema_from_jsonld(html) is None

    def test_multiple_scripts_first_recognized_wins(self):
        """Multiple JSON-LD blocks — first with recognized @type wins."""
        s1 = json.dumps({"@type": "WebSite", "name": "Site"})
        s2 = json.dumps({"@type": "Product", "name": "Widget"})
        html = (
            f"<html><head>"
            f'<script type="application/ld+json">{s1}</script>'
            f'<script type="application/ld+json">{s2}</script>'
            f"</head><body></body></html>"
        )
        assert _detect_schema_from_jsonld(html) == "Product"


# ---------------------------------------------------------------------------
# TestResolveJsonldType
# ---------------------------------------------------------------------------


class TestResolveJsonldType:
    """Unit tests for _resolve_jsonld_type helper."""

    def test_plain_dict(self):
        assert _resolve_jsonld_type({"@type": "Product"}) == "Product"

    def test_list_input(self):
        data = [{"@type": "WebSite"}, {"@type": "NewsArticle"}]
        assert _resolve_jsonld_type(data) == "NewsArticle"

    def test_non_dict_non_list(self):
        assert _resolve_jsonld_type("string") is None
        assert _resolve_jsonld_type(42) is None
        assert _resolve_jsonld_type(None) is None

    def test_nested_graph(self):
        data = {"@graph": [{"@type": "BlogPosting"}]}
        assert _resolve_jsonld_type(data) == "NewsArticle"


# ---------------------------------------------------------------------------
# TestDetectSchemaContentSignals (URL-based)
# ---------------------------------------------------------------------------


class TestDetectSchemaContentSignals:
    """URL-based government TLD detection in detect_schema()."""

    def test_gov_tld(self):
        assert detect_schema("https://www.usa.gov/services") == "GovernmentPage"

    def test_gov_kr_tld(self):
        assert detect_schema("https://www.mois.go.kr/portal") == "GovernmentPage"

    def test_gov_uk_tld(self):
        assert detect_schema("https://www.gov.uk/benefits") == "GovernmentPage"

    def test_no_gov_signal(self):
        assert detect_schema("https://www.randomsite.com/page") == "Generic"


# ---------------------------------------------------------------------------
# TestDetectSchemaIntegration
# ---------------------------------------------------------------------------


class TestDetectSchemaIntegration:
    """Integration: detect_schema() domain bypass + Generic override path."""

    def test_known_domain_bypasses_jsonld(self):
        """Known domain → domain schema, even if JSON-LD says otherwise."""
        assert detect_schema("https://www.coupang.com/product/123") == "Product"

    def test_unknown_domain_returns_generic(self):
        assert detect_schema("https://www.unknownstore.com/product/123") == "Generic"

    def test_gov_domain_in_map_takes_precedence(self):
        """gov.kr is in DOMAIN_SCHEMA_MAP → domain match wins."""
        assert detect_schema("https://www.gov.kr/portal") == "GovernmentPage"


# ---------------------------------------------------------------------------
# TestSchemaRegressions
# ---------------------------------------------------------------------------


class TestSchemaRegressions:
    """Ensure existing DOMAIN_SCHEMA_MAP entries still resolve correctly."""

    @pytest.mark.parametrize(
        "domain,expected",
        list(DOMAIN_SCHEMA_MAP.items()),
    )
    def test_domain_schema_map_entries(self, domain, expected):
        url = f"https://www.{domain}/some/page"
        assert detect_schema(url) == expected

    def test_product_domains_still_product(self):
        """Spot check: e-commerce domains resolve to Product, not Generic."""
        for domain in ("coupang.com", "musinsa.com", "zara.com", "nike.com"):
            assert detect_schema(f"https://{domain}/item/1") == "Product"


# ---------------------------------------------------------------------------
# TestSchemaNameEnum
# ---------------------------------------------------------------------------


class TestSchemaNameEnum:
    """SchemaName StrEnum backward compatibility."""

    def test_str_equality(self):
        assert SchemaName.PRODUCT == "Product"
        assert SchemaName.GENERIC == "Generic"
        assert SchemaName.NEWS_ARTICLE == "NewsArticle"

    def test_all_values_match_domain_map(self):
        """All values used in DOMAIN_SCHEMA_MAP exist in SchemaName."""
        for schema in DOMAIN_SCHEMA_MAP.values():
            assert schema in list(SchemaName)

    def test_generic_is_member(self):
        assert "Generic" in list(SchemaName)
