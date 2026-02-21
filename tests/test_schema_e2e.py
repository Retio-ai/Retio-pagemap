# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema-specific E2E integration tests for WikiArticle, SaaSPage, GovernmentPage.

Phase 7.1: These three schemas previously had only smoke tests
(test_valid_schemas_no_warning). This module adds full behavioural/E2E tests:
  - Synthetic HTML → prune_page() → key field preservation + token reduction
  - prune_chunks() unit tests for field matching boundaries
"""

from __future__ import annotations

import pytest

from pagemap.pruning import ChunkType, HtmlChunk
from pagemap.pruning.pipeline import prune_page
from pagemap.pruning.pruner import PruneDecision, prune_chunks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    text: str,
    chunk_type: ChunkType = ChunkType.TEXT_BLOCK,
    in_main: bool = True,
    tag: str = "div",
    attrs: dict | None = None,
    parent_xpath: str | None = None,
) -> HtmlChunk:
    """Build a test HtmlChunk — extends test_pruning_fixes.py pattern with parent_xpath override."""
    default_xpath = "/html/body/main/div[1]" if in_main else "/html/body/div[1]"
    default_parent = "/html/body/main" if in_main else "/html/body"
    return HtmlChunk(
        xpath=default_xpath,
        html=f"<{tag}>{text}</{tag}>",
        text=text,
        tag=tag,
        chunk_type=chunk_type,
        attrs=attrs or {},
        parent_xpath=parent_xpath if parent_xpath is not None else default_parent,
        depth=3,
        in_main=in_main,
    )


def _prune_single(
    chunk: HtmlChunk,
    schema: str,
    has_main: bool = True,
) -> PruneDecision:
    """Prune a single chunk — shortcut reused from test_pruning_fixes.py."""
    results = prune_chunks([chunk], schema_name=schema, has_main=has_main)
    assert len(results) == 1
    return results[0][1]


# ---------------------------------------------------------------------------
# HTML Fixtures (module-level constants, per project convention)
# ---------------------------------------------------------------------------

_WIKI_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="History of Computing" />
</head>
<body>
<nav><ul><li><a href="/">Home</a></li><li><a href="/about">About</a></li></ul></nav>
<main>
  <h1>History of Computing</h1>
  <p>The history of computing spans several centuries, from the abacus and early mechanical calculators
  to modern electronic computers that have transformed every aspect of human civilization and daily life.</p>
  <h2>Early Mechanical Devices</h2>
  <p>Charles Babbage designed the Analytical Engine in 1837, which contained many features of modern computers.</p>
  <h2>Electronic Era</h2>
  <p>The development of vacuum tubes and transistors in the twentieth century led to the creation of
  programmable electronic computers that could process data at unprecedented speeds and scale.</p>
</main>
<aside>
  <h3>Related Articles</h3>
  <ul><li><a href="/alan-turing">Alan Turing</a></li><li><a href="/eniac">ENIAC</a></li></ul>
</aside>
</body>
</html>"""

_WIKI_NO_MAIN_HTML = """\
<!DOCTYPE html>
<html>
<head><meta property="og:title" content="Simple Article" /></head>
<body>
<h1>Simple Article Title</h1>
<p>This article does not use a main element but still contains substantial content that should be
preserved through the keep-if-unsure fallback mechanism in the pruning pipeline.</p>
<h2>Section Heading</h2>
<p>More content in this section that provides additional detail about the topic being discussed.</p>
</body>
</html>"""

_WIKI_KOREAN_HTML = """\
<!DOCTYPE html>
<html>
<head><meta property="og:title" content="한국의 역사" /></head>
<body>
<main>
  <h1>한국의 역사</h1>
  <p>한국의 역사는 수천 년에 걸쳐 이어져 왔으며, 고조선부터 현대 대한민국에 이르기까지 다양한 왕조와 정치 체제를 거쳐 발전해 왔습니다. 이 문서에서는 한국 역사의 주요 시기와 사건들을 개괄적으로 살펴봅니다.</p>
  <h2>고대 시대</h2>
  <p>고조선은 한반도와 만주 지역에서 가장 오래된 국가로 알려져 있으며 기원전 2333년에 건국되었다고 전해집니다.</p>
</main>
</body>
</html>"""

_SAAS_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="CloudSync Pro" />
  <meta property="og:site_name" content="CloudSync" />
</head>
<body>
<nav><a href="/">Home</a> <a href="/pricing">Pricing</a> <a href="/docs">Docs</a></nav>
<main>
  <h1>CloudSync Pro — Enterprise Cloud Platform</h1>
  <p>CloudSync Pro provides enterprise-grade cloud synchronization with real-time collaboration tools
  designed for teams that need reliable and secure data management across multiple environments.</p>
  <div>Pricing starts at $49/month for teams, $199/month for enterprise with unlimited storage.</div>
  <ul>Features: Real-time sync, End-to-end encryption, API access, Team collaboration, Custom integrations</ul>
  <h2>Features Overview</h2>
  <p>Our platform includes advanced features for modern cloud workflows and enterprise data management.</p>
</main>
<footer>
  <p>Copyright 2024 CloudSync Inc. All rights reserved. Terms of Service. Privacy Policy.</p>
</footer>
</body>
</html>"""

_SAAS_KOREAN_HTML = """\
<!DOCTYPE html>
<html>
<head><meta property="og:title" content="클라우드싱크 프로" /></head>
<body>
<main>
  <h1>클라우드싱크 프로</h1>
  <p>기업용 클라우드 동기화 서비스로 실시간 협업 도구를 제공하며 안전한 데이터 관리를 보장합니다.</p>
  <div>요금: 월 49,000원 (팀), 월 199,000원 (엔터프라이즈)</div>
  <ul>기능: 실시간 동기화, 엔드투엔드 암호화, API 접근, 팀 협업, 맞춤 통합</ul>
</main>
</body>
</html>"""

_SAAS_NO_MAIN_HTML = """\
<!DOCTYPE html>
<html>
<head><meta property="og:title" content="PriceTool" /></head>
<body>
<h1>PriceTool Analytics</h1>
<table><tr><th>Plan</th><th>Price</th></tr><tr><td>Basic</td><td>$29/month</td></tr><tr><td>Pro</td><td>$99/month</td></tr></table>
<p>PriceTool helps businesses track and analyze pricing trends across competitive markets and industries.</p>
</body>
</html>"""

_GOV_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="공공서비스 안내" />
  <meta property="og:site_name" content="관세청" />
</head>
<body>
<nav><a href="/">홈</a> <a href="/services">서비스</a></nav>
<main>
  <h1>관세청 공공서비스 안내</h1>
  <p>관세청에서 제공하는 다양한 공공서비스에 대해 안내드립니다. 본 페이지에서는 수출입 통관 절차와 관련 서비스를 확인할 수 있습니다.</p>
  <p>발표일: 2024-03-15 관세청 정책조정과에서 작성한 공식 안내문입니다.</p>
</main>
<footer role="contentinfo">
  <p>연락처: customs@korea.kr 전화: 042-481-7714</p>
  <p>주소: 대전광역시 서구 청사로 189</p>
</footer>
</body>
</html>"""

_GOV_FOOTER_HTML = """\
<!DOCTYPE html>
<html>
<head><meta property="og:title" content="Notice" /></head>
<body>
<main>
  <h1>Service Notice</h1>
  <p>This is a government service notice with important information about policy changes and updates.</p>
</main>
<footer role="contentinfo">
  <p>Contact: customs@korea.kr Tel: 042-481-7714</p>
</footer>
</body>
</html>"""

_GOV_ENGLISH_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="Trade Policy Update" />
  <meta property="og:site_name" content="Department of Commerce" />
</head>
<body>
<main>
  <h1>Trade Policy Update</h1>
  <p>The Department of Commerce announces updated trade policy guidelines effective immediately for all importers.</p>
  <p>Effective date: 2024-06-01. Published by the Department of Commerce trade division.</p>
</main>
<footer role="contentinfo">
  <p>Tel: 1-800-555-0100 Email: info@commerce.gov</p>
  <p>Address: 1401 Constitution Ave NW, Washington DC</p>
</footer>
</body>
</html>"""

# Noisy version with extra boilerplate for token reduction comparison
_WIKI_NOISY_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="History of Computing" />
  <style>.nav{color:red} .sidebar{width:200px} .footer{background:#333} body{font:sans-serif}</style>
</head>
<body>
<header><div class="logo">WikiSite</div><div class="search"><input placeholder="Search" /></div></header>
<nav>
  <ul><li><a href="/">Home</a></li><li><a href="/about">About</a></li>
  <li><a href="/contact">Contact</a></li><li><a href="/help">Help</a></li>
  <li><a href="/donate">Donate</a></li><li><a href="/random">Random</a></li></ul>
</nav>
<main>
  <h1>History of Computing</h1>
  <p>The history of computing spans several centuries, from the abacus and early mechanical calculators
  to modern electronic computers that have transformed every aspect of human civilization and daily life.</p>
  <h2>Early Mechanical Devices</h2>
  <p>Charles Babbage designed the Analytical Engine in 1837, which contained many features of modern computers.</p>
</main>
<aside>
  <h3>Related Articles</h3>
  <ul><li><a href="/a1">Article 1</a></li><li><a href="/a2">Article 2</a></li>
  <li><a href="/a3">Article 3</a></li><li><a href="/a4">Article 4</a></li></ul>
  <h3>Did You Know?</h3>
  <p>Random trivia snippet</p>
  <h3>In The News</h3>
  <ul><li><a href="/n1">News 1</a></li><li><a href="/n2">News 2</a></li></ul>
</aside>
<footer>
  <div class="footer-links">
    <a href="/privacy">Privacy</a> <a href="/terms">Terms</a> <a href="/about">About</a>
    <a href="/disclaimer">Disclaimer</a> <a href="/contact">Contact</a>
  </div>
  <p>Copyright 2024 WikiSite. Content available under CC BY-SA.</p>
</footer>
</body>
</html>"""


# ===========================================================================
# Class 1: WikiArticle E2E
# ===========================================================================


class TestWikiArticleE2E:
    def test_happy_path_all_fields_preserved(self):
        """Main content (h1, summary, h2, sections) preserved; nav/aside removed."""
        result = prune_page(_WIKI_HTML, "test", "p1", "WikiArticle")
        html = result.pruned_html

        # Key content preserved
        assert "History of Computing" in html
        assert "several centuries" in html  # summary paragraph
        assert "Early Mechanical Devices" in html  # h2
        assert "Analytical Engine" in html  # section text

        # Navigation and aside removed
        assert "Related Articles" not in html

    def test_no_main_fallback(self):
        """Page without <main> uses keep-if-unsure fallback."""
        result = prune_page(_WIKI_NO_MAIN_HTML, "test", "p1", "WikiArticle")
        html = result.pruned_html

        # Headings and substantial text preserved via fallback
        assert "Simple Article Title" in html
        assert "Section Heading" in html

    def test_korean_content_preserved(self):
        """Korean content with char length > thresholds is preserved."""
        result = prune_page(_WIKI_KOREAN_HTML, "test", "p1", "WikiArticle")
        html = result.pruned_html

        assert "한국의 역사" in html  # title
        assert "고조선" in html  # section content

    @pytest.mark.parametrize(
        "chunk,expected_fields,unexpected_fields",
        [
            pytest.param(
                _make_chunk("Article Title", ChunkType.HEADING, tag="h1"),
                ["title"], [], id="h1-title",
            ),
            pytest.param(
                _make_chunk("A" * 101),
                ["summary", "sections"], [], id="long-text-summary-sections",
            ),
            pytest.param(
                _make_chunk("Section Title", ChunkType.HEADING, tag="h2"),
                ["sections"], [], id="h2-sections",
            ),
            pytest.param(
                _make_chunk("A" * 80),
                ["sections"], ["summary"], id="80char-sections-not-summary",
            ),
            pytest.param(
                _make_chunk("Short text"),
                [], ["summary", "sections"], id="short-no-match",
            ),
        ],
    )
    def test_field_matching(self, chunk, expected_fields, unexpected_fields):
        """Direct prune_chunks verification of field matching rules."""
        decision = _prune_single(chunk, "WikiArticle")
        for f in expected_fields:
            assert f in decision.matched_fields
        for f in unexpected_fields:
            assert f not in decision.matched_fields


# ===========================================================================
# Class 2: SaaSPage E2E
# ===========================================================================


class TestSaaSPageE2E:
    def test_happy_path_all_fields_preserved(self):
        """Name, pricing, features, description preserved; footer removed."""
        result = prune_page(_SAAS_HTML, "test", "p1", "SaaSPage")
        html = result.pruned_html

        # Key fields
        assert "CloudSync Pro" in html  # name (h1)
        assert "$49/month" in html  # pricing
        assert "$199/month" in html
        assert "Real-time sync" in html  # features
        assert "enterprise-grade" in html  # description

        # Footer removed (non-gov schema)
        assert "Terms of Service" not in html

    def test_korean_pricing_and_features(self):
        """Korean pricing (요금/PRICING_RE) and features (기능/FEATURE_RE) recognized."""
        result = prune_page(_SAAS_KOREAN_HTML, "test", "p1", "SaaSPage")
        html = result.pruned_html

        assert "클라우드싱크 프로" in html  # name
        assert "요금" in html  # pricing
        assert "49,000원" in html
        assert "기능" in html  # features
        assert "실시간 동기화" in html

    def test_no_main_pricing_table(self):
        """TABLE with pricing pattern matches pricing:pricing-table without <main>."""
        result = prune_page(_SAAS_NO_MAIN_HTML, "test", "p1", "SaaSPage")
        html = result.pruned_html

        # Pricing table preserved
        assert "$29/month" in html
        assert "$99/month" in html
        # Name preserved
        assert "PriceTool" in html

    @pytest.mark.parametrize(
        "length,expect_description",
        [(49, False), (50, False), (51, True)],
        ids=["below-50", "at-50", "above-50"],
    )
    def test_description_threshold_boundary(self, length: int, expect_description: bool):
        """_SAAS_DESC_MIN=50, strict > comparison."""
        decision = _prune_single(_make_chunk("A" * length), "SaaSPage")
        if expect_description:
            assert "description" in decision.matched_fields
        else:
            assert "description" not in decision.matched_fields

    @pytest.mark.parametrize(
        "chunk_type,tag,text,expected_reason",
        [
            pytest.param(ChunkType.LIST, "ul", "Features: sync, encryption, API", "feature-list", id="feature-list"),
            pytest.param(ChunkType.HEADING, "h2", "Features Overview", "feature-heading", id="feature-heading"),
        ],
    )
    def test_feature_list_vs_heading_match(self, chunk_type, tag, text, expected_reason):
        """LIST vs HEADING both match features via different reason paths."""
        chunk = _make_chunk(text, chunk_type=chunk_type, tag=tag)
        decision = _prune_single(chunk, "SaaSPage")
        assert decision.keep is True
        assert "features" in decision.matched_fields
        assert expected_reason in decision.reason_detail


# ===========================================================================
# Class 3: GovernmentPage E2E
# ===========================================================================


class TestGovernmentPageE2E:
    def test_happy_path_all_fields_preserved(self):
        """Title, department, description, date, and footer contact all preserved."""
        result = prune_page(_GOV_HTML, "test", "p1", "GovernmentPage")
        html = result.pruned_html

        # Core content
        assert "관세청" in html  # title + department
        assert "공공서비스" in html  # title
        assert "수출입 통관" in html  # description
        assert "2024-03-15" in html  # date

        # Footer contact preserved (GovernmentPage AOM exception)
        assert "customs@korea.kr" in html
        assert "042-481-7714" in html

    def test_footer_contact_preserved_via_aom_exception(self):
        """<footer role="contentinfo"> gets AOM weight 0.6 for GovernmentPage → contact preserved."""
        result = prune_page(_GOV_FOOTER_HTML, "test", "p1", "GovernmentPage")
        html = result.pruned_html
        assert "customs@korea.kr" in html
        assert "042-481-7714" in html

    @pytest.mark.parametrize("schema", ["Product", "NewsArticle", "Generic"])
    def test_footer_removed_for_non_gov_schema(self, schema: str):
        """Same HTML, non-GovernmentPage schema → footer removed."""
        result = prune_page(_GOV_FOOTER_HTML, "test", "p1", schema)
        assert "customs@korea.kr" not in result.pruned_html

    def test_english_department_and_contact(self):
        """English department, Tel, Email recognized."""
        result = prune_page(_GOV_ENGLISH_HTML, "test", "p1", "GovernmentPage")
        html = result.pruned_html

        assert "Department of Commerce" in html
        assert "Trade Policy" in html
        assert "info@commerce.gov" in html
        assert "1-800-555-0100" in html

    def test_description_requires_main_or_article_parent(self):
        """description field: in_main=False needs 'article' in parent_xpath."""
        text = "A" * 40  # > _GOV_BODY_MIN (30)

        # in_main=True → matches description
        d = _prune_single(_make_chunk(text, in_main=True), "GovernmentPage")
        assert "description" in d.matched_fields

        # in_main=False, parent has "article" → matches description
        chunk_article = _make_chunk(
            text,
            in_main=False,
            parent_xpath="/html/body/article/div[1]",
        )
        d = _prune_single(chunk_article, "GovernmentPage", has_main=False)
        assert "description" in d.matched_fields

        # in_main=False, parent has no "article" → no description match
        chunk_no_article = _make_chunk(
            text,
            in_main=False,
            parent_xpath="/html/body/div[1]",
        )
        d = _prune_single(chunk_no_article, "GovernmentPage", has_main=False)
        assert "description" not in d.matched_fields

    def test_date_datetime_attr_path(self):
        """Chunk with datetime attr → date field matched."""
        chunk = _make_chunk(
            "March 15",
            tag="time",
            attrs={"datetime": "2024-03-15"},
        )
        decision = _prune_single(chunk, "GovernmentPage")
        assert decision.keep is True
        assert "date" in decision.matched_fields

    def test_date_text_pattern_path(self):
        """Text containing YYYY-MM-DD pattern → date field matched."""
        chunk = _make_chunk("Published on 2024-03-15")
        decision = _prune_single(chunk, "GovernmentPage")
        assert "date" in decision.matched_fields

    @pytest.mark.parametrize(
        "text,expect_match",
        [
            pytest.param("기관 안내", True, id="기관"),
            pytest.param("부처 소개", True, id="부처"),
            pytest.param("189,000원", False, id="price-won"),
        ],
    )
    def test_department_regex_korean(self, text: str, expect_match: bool):
        """Department regex matches Korean gov terms but not prices with 원."""
        chunk = _make_chunk(text)
        decision = _prune_single(chunk, "GovernmentPage")
        if expect_match:
            assert "department" in decision.matched_fields
        else:
            assert "department" not in decision.matched_fields


# ===========================================================================
# Class 4: Token Reduction Regression
# ===========================================================================


class TestTokenReductionRegression:
    @pytest.mark.parametrize(
        "schema,html,floor,ceil",
        [
            pytest.param("WikiArticle", _WIKI_HTML, 25.0, 65.0, id="wiki"),
            pytest.param("SaaSPage", _SAAS_HTML, 20.0, 60.0, id="saas"),
            pytest.param("GovernmentPage", _GOV_HTML, 10.0, 45.0, id="gov"),
        ],
    )
    def test_token_reduction_in_expected_range(self, schema, html, floor, ceil):
        """Token reduction percentage falls within expected bounds."""
        result = prune_page(html, "test", "p1", schema)
        assert result.token_reduction_pct >= floor, (
            f"{schema}: reduction {result.token_reduction_pct:.1f}% < floor {floor}%"
        )
        assert result.token_reduction_pct <= ceil, (
            f"{schema}: reduction {result.token_reduction_pct:.1f}% > ceil {ceil}%"
        )

    @pytest.mark.parametrize(
        "schema,html",
        [
            pytest.param("WikiArticle", _WIKI_HTML, id="wiki"),
            pytest.param("SaaSPage", _SAAS_HTML, id="saas"),
            pytest.param("GovernmentPage", _GOV_HTML, id="gov"),
        ],
    )
    def test_chunk_selection_ratio(self, schema, html):
        """Pruning pipeline selects at least 1 chunk from well-formed HTML."""
        result = prune_page(html, "test", "p1", schema)
        assert result.chunk_count_total > 0, "No chunks produced"
        assert result.chunk_count_selected > 0, "No chunks selected"
        assert result.chunk_count_selected <= result.chunk_count_total

    def test_noisy_page_higher_reduction(self):
        """Noisy Wiki HTML has higher reduction than clean Wiki HTML."""
        clean = prune_page(_WIKI_HTML, "test", "p1", "WikiArticle")
        noisy = prune_page(_WIKI_NOISY_HTML, "test", "p1", "WikiArticle")
        assert noisy.token_reduction_pct > clean.token_reduction_pct

    @pytest.mark.parametrize(
        "schema,html",
        [
            pytest.param("WikiArticle", _WIKI_HTML, id="wiki"),
            pytest.param("SaaSPage", _SAAS_HTML, id="saas"),
            pytest.param("GovernmentPage", _GOV_HTML, id="gov"),
        ],
    )
    def test_no_errors_on_well_formed_html(self, schema, html):
        """Well-formed HTML produces no pipeline errors."""
        result = prune_page(html, "test", "p1", schema)
        assert result.errors == []
