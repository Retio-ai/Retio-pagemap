# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [0.7.2] - 2026-02-25

### Security

- **`_find_type_in_jsonld()` recursion depth limit** — Added `max_depth=5` parameter to prevent `RecursionError` from maliciously nested `@graph` structures (DoS vector)
- **metadata.py field sanitization** — Applied `sanitize_text()` to `currency`, `telephone`, `price_range`, `datePublished`, `upload_date`, `duration`, `start_date`/`end_date`, BreadcrumbList `name`, and `_parse_h1()` return value — eliminates prompt injection vector from 8+ previously unsanitized fields
- **OG image/thumbnail URL validation** — Applied `_is_valid_url()` to `image_url`/`thumbnail_url` OG fields; `javascript:` and `data:` URLs no longer pass through

### Added

- **`_extract_price_from_html()`** — lxml DOM-based last-resort price extractor from raw/pruned HTML; priority: `a-offscreen` text > price-class `text_content()` > `aria-label` (handles Amazon nested span structures)
- **`_extract_video_meta_from_dom()`** — Class-name and regex-based video metadata extraction from heading chunks (channel, view_count, duration); called as last-resort fallback for VideoObject schema
- **`_is_news_portal()` / `_compress_for_news_portal()`** — Detects news portal pattern (≥3 `<article>` elements or ≥3 headline links) in dashboard-classified pages; dedicated numbered headline list compressor with optional per-article summaries (BBC News improvement)
- **`pagemap serve --help` forwarded options** — `_ServeHelpAction` + `_get_server_options_help()` dynamically append server options (e.g. `--transport`, `--port`, `--allow-local`) to `pagemap serve --help` output
- **3 new test files** — `test_hn_regression.py`, `test_news_portal_compression.py`, `test_pruned_context_builder_fixes.py`

### Fixed

- **HN/forum table-based grid whitelist** — Added `table`/`tbody` to `_GRID_CONTAINER_TAGS`; table-based content listings (Hacker News, forums) now receive link-density penalty exemption (fixes HN content regression: 1,010 → 511 tok in v0.7.1)
- **AOM content rescue detached parent** — Added `tree.getpath(parent)` check before re-inserting rescued elements; skips rescue when parent was detached during removal phase (prevents silent ghost subtree insertion)
- **`removed_nodes` stat overcounting** — Rescued nodes are now subtracted from `removed_nodes` and `removal_reasons` counters (previously inflated telemetry)
- **`_extract_price_from_dom_chunks()` empty-text fallback** — Added `aria-label` and `data-*` attribute fallbacks when chunk text is empty
- **`_to_float()` European number format** — `"1.500,99"` now correctly parses as `1500.99` instead of `1.5`
- **`_to_int()` silent truncation** — Changed from `int(float)` to `round(float)`: `"4.9"` → `5` instead of `4` (fixes silent corruption in reviewCount etc.)
- **`_extract_price_from_offers()` zero-price falsy** — `lowPrice or price` pattern replaced with explicit `None` check; price=0 is preserved correctly
- **`_extract_image_url()` ImageObject dict** — Added support for `"image": {"@type": "ImageObject", "url": "..."}` pattern (previously missed)
- **`extract_metadata()` pruned_html parameter** — Product and VideoObject schemas receive pruned HTML for lxml-based price extraction fallback; VideoObject also gets DOM-based metadata fallback

### Changed

- **`_is_inside_article_or_main()` O(1) lookup** — Pre-computes `_article_main_descendants` set before filtering loop; passes to `_compute_weight()` via new `article_main_descendants` parameter. Eliminates O(nodes × depth) traversal on large documents
- **`_compress_for_dashboard()` news portal delegation** — Now accepts `doc` parameter and delegates to `_compress_for_news_portal()` when `_is_news_portal()` detects news portal structure
- **`VideoObject` added to `_SCHEMA_OVERRIDES`** — VideoObject schema now overrides page_type-based compressor selection; `_SCHEMA_OVERRIDES` moved to module level (eliminates per-call frozenset recreation)
- **Phase 4 product price regex** — Replaced inline regex with pre-compiled `_PRICE_CLASS_RE` (named groups, handles single-quote class attributes); extracted price injected back into metadata dict for downstream consumers
- **VideoObject itemprop `author` → `channel`** — Added `"author": "channel"` to `_ITEMPROP_FIELD_MAP["VideoObject"]`
- **VideoObject OG `og:site_name` removed** — Removed `og:site_name` → `channel` mapping (was incorrectly using site name e.g. "YouTube" as channel name)
- **Video description CJK budget factor** — Reduced from 0.95 to 0.85 to account for CJK-heavy descriptions (~1.5 chars/token vs English ~4 chars/token); `_truncate_to_tokens()` guard handles overshoot
- 4014 → 4194 tests passing (+180)

## [0.7.1] - 2026-02-24

### Added

- **`video` page type** — New page classifier signals for YouTube (`youtube.com/watch`, `youtu.be`), Vimeo, and generic video pages. URL signals (30pts), `og:type="video.*"` meta signal (25pts), DOM signal (`<video>`, `ytd-player`), JSON-LD `VideoObject` signal (40pts). Threshold 20
- **VideoObject JSON-LD metadata parser** — `_parse_json_ld_video()` extracts name, description, duration, upload_date, channel (from author field), thumbnail_url. `interactionStatistic` parsing: WatchAction→view_count, LikeAction→like_count, CommentAction→comment_count, DislikeAction→dislike_count. Registered in `_JSONLD_PARSERS`, `_OG_FIELD_MAP`, `_ITEMPROP_FIELD_MAP`
- **Video compressor** — `_compress_for_video()` formats metadata with K/M suffix for large numbers (`1.5M views`, `25.0K likes`). Budget-aware description inclusion. Text-line fallback when metadata is sparse
- **DOM price fallback for Product schema** — `_extract_price_from_dom_chunks()` scans HtmlChunk attributes for Amazon price patterns (`a-price`, `a-offscreen` classes) and currency symbol regex (`$€£¥₩` + digits). Shipping/handling false positive filtering. Called when JSON-LD/itemprop/OG sources lack price
- **Product compressor price fallback** — Phase 4 scans `pruned_html` for `class="...price..."` patterns when metadata has no price
- **48 new tests** — `test_qr_v070_improvements.py` covering AOM article exemption, video page classification, VideoObject metadata, video compression, Product content rescue, DOM price fallback

### Changed

- **Article compressor → budget-based** — Replaced fixed "title + max 2 paragraphs" with `_calibrate_chars_per_token()` budget-based extraction. 3-phase approach: metadata → chunk-based structural extraction (heading/body) → text-line fallback. Outputs richer content for article pages (Wikipedia 84→400+ tok)
- **AOM filter: Readability-inspired article `<p>` exemption** — `<p>` tags inside `<article>`/`<main>` with `non_link_text > 80` chars survive moderate link-density penalty (Wikipedia reference links `[1][2]` no longer cause paragraph removal). High density (>0.8) still penalized. `<div>`, `<li>` and short text retain existing behavior
- **Product schema content rescue** — AOM content rescue now triggers for `schema_name == "Product"` regardless of remaining text length (previously required `< 100 chars`). Restores link-density-removed elements containing price patterns
- **Wikipedia domain mapping generalized** — `DOMAIN_SCHEMA_MAP` changed from `"ko.wikipedia.org"` to `"wikipedia.org"` (covers all language variants: en, ko, ja, fr, etc.)
- **Schema override dispatch** — `_SCHEMA_OVERRIDES` frozenset allows WikiArticle schema to use wiki compressor even when page_type is `article`
- **Video domain mappings** — `youtube.com`, `youtu.be`, `vimeo.com` → `VideoObject` in `DOMAIN_SCHEMA_MAP` and `_JSONLD_TYPE_TO_SCHEMA`
- **`pagemap serve` argument forwarding** — `parse_args()` → `parse_known_args()`, remaining args forwarded to server via `_server_argv`. `server.main(argv=)` accepts explicit argv parameter. `pagemap serve --transport http --port 8000` now works
- **`--help` epilog fix** — Removed duplicate `build` in epilog examples (`%(prog)s build --url` → `%(prog)s --url`, since `%(prog)s` already includes `build` in subparser context)
- **`retio-pagemap --help` restored** — Removed `add_help=False` from `_parse_server_args()` so MCP server entry point shows usage information
- **README version updated** — Deployment Status table: `v0.5.2` → `v0.7.0`
- 3983 → 4014 tests passing (+31 net, +48 new, -17 test updates)

### Fixed

- **session_manager pool.acquire resource leak** — Added try/except around `install_ssrf_route_guard()` after `pool.acquire()`. On exception, `pool.release()` is called to prevent semaphore slot permanent occupation during long-running HTTP deployments
- **Card detection entity leak** — Added `_html.unescape(part_text)` in `_detect_cards_from_chunks()` Strategy 1. Fixes `&amp;` entities leaking into agent output (exposed by Product content rescue expansion, e.g., "H&amp;M" → "H&M")

## [0.7.0] - 2026-02-24

### Added

- **Content extraction quality overhaul** — AOM grid whitelist (`_detect_repeating_grids()` for schema-agnostic sibling container detection, link-density penalty exemption), DOM card detection (price-anchor + parent walk algorithm), Minimum Content Guarantee safety net (OG → pruned_html → raw_html cascade when < 10 tokens), Extraction Quality Score (EQS) telemetry. 36 new tests
- **Captcha/WAF block page detection** — "blocked" page_type with URL, meta, and DOM signals for Cloudflare, reCAPTCHA, hCaptcha, Turnstile, DataDome, PerimeterX/HUMAN, Imperva. HTTP status capture, `CAPTCHA_DETECTED` telemetry, `blocked_info` metadata, `verify_ref` navigation hint. Short-circuit safety override
- **Interactable noise filtering** — `_is_table_noise` predicate (unnamed row/cell/gridcell, trivial ordinals). 5th bucket (`bucket_table_noise`) in budget filter for noise deprioritization. Chrome inputs (radio, checkbox, switch) in pruned regions demoted to `bucket_rest`
- **Enhanced image extraction pipeline** — 4-phase pipeline: `<picture>`/`<source>` support, size/semantic filtering (W3C decorative signals), canonical URL dedup (largest variant), fetchpriority boost. `<figure>` semantic boost, `loading="eager"` boost, srcset `w` descriptor validation, SVG filtering, Amazon/Korean CDN patterns, JSON-LD/OG image integration. `IMAGE_FILTER_APPLIED` telemetry event. Code review: 7 fixes for telemetry counters, SVG filter ordering, dedup, Amazon noise patterns
- **Unicode script-based language filtering** — `script_filter.py` with bisect-based O(log k) codepoint classification, page-dominant-script detection, line-level filtering (short UI noise → remove, long foreign content → `[lang]` tag). Passthrough exceptions: URLs, numbers/units, brand names, ≤5 chars. `LANG_FILTER_APPLIED` telemetry. Enabled by default in `build_pruned_context()`
- **CLI UX improvements** — `--output` supports file mode (`out.json`) and directory mode (`out/`). `build` without `--url` prints error with examples. `--help` epilog with usage examples (`RawDescriptionHelpFormatter`). `--format json|text|markdown` stdout output (mutually exclusive with `--output`). `_progress.py` with rich spinner (optional dep) + TTY-aware `print_step`
- **Structured error handling** — regex-based `net::ERR_*` classifier (`classify_network_error()`), CLI formatter (`to_cli_text()`), `cmd_build()` sync-level error catch, `main()` top-level handler. `--verbose` for traceback. `RuntimeError` → `BrowserError` fix for MCP path. 31 new tests
- **JSON-LD schema expansion** — NewsArticle, BreadcrumbList, FAQPage, Event, LocalBusiness parsers. 1-pass JSON-LD parsing + generic type finder + dispatch registry refactoring. Event 8 subtypes, LocalBusiness 10 subtypes. 470+ new tests
- **SaaS/Government/Wiki schema-aware compressors** — `_compress_saas_dispatch()`/`_compress_government_dispatch()`/`_compress_wiki_dispatch()` in `pruned_context_builder.py`. Per-schema OG field mapping, dedicated ChunkTypes, domain-specific metadata extraction. `_compress_default()` fallback resolved
- **robots.txt compliance** — `RobotsChecker` (RFC 9309, Protego-based): origin-level cache, Cache-Control max-age dynamic TTL, fail-open semantics, 401/403→full block, 4xx→full allow. `--ignore-robots` / `PAGEMAP_IGNORE_ROBOTS` flag. Integrated in `get_page_map`/`batch_get_page_map`. `ROBOTS_BLOCKED` telemetry event
- **Bot User-Agent** — `--bot-ua` / `PAGEMAP_BOT_UA` flag for transparent `PageMapBot/{version}` User-Agent. Default remains Chrome UA
- **Legal disclaimer** — README, PyPI page, and MCP server startup message: users responsible for target website ToS and applicable laws
- **RFC 9457 Problem Details** — `problem_details.py`: `ProblemType(StrEnum)` 15-type error taxonomy, `ProblemDetail` frozen dataclass, `sanitize_detail()` 8-pattern secret masking, 9 factory functions. `_safe_error()` → `ProblemDetail` pathway, MCP text output backward compatible. `to_response()` produces `application/problem+json`. 88 tests
- **i18n expansion** — default locale changed from ko to en. Chinese (zh) locale added (Taobao/JD.com/Pinduoduo, 24 keyword tuples, 8 domain + 2 TLD mappings). 4 European locales added (es/it/pt/nl) with full LocaleConfig, domain/TLD mapping, path segment detection, Layer 1 detection terms (RATING, REVIEW_COUNT, NEXT/PREV_BUTTON, LOAD_MORE, REPORTER, CONTACT, BRAND). Accept-Language headers auto-sent per URL locale
- **K8s/nginx/Cloudflare deployment configs** — `deploy/` directory with Kubernetes manifests, nginx reverse proxy, and Cloudflare tunnel configuration
- **MCG regression tests** — 12-case regression suite with snapshot markers (NB-E)
- **SessionManager + BrowserPool** — `StdioSessionManager` (single session) + `HttpSessionManager` (BrowserPool-based multi-session). `BrowserPool` with `max_contexts=5`, `asyncio.Semaphore` capacity control, idle timeout reaper, `PoolHealth` monitoring
- **HTTP transport** — `--transport stdio|http` flag, FastMCP HTTP mode, structlog JSON logging, `/health`+`/ready` + K8s probes (`/livez`/`/readyz`/`/startupz`), CORS origin control, API Gateway middleware (trusted proxy IP, X-Request-ID, Cloudflare CIDR), graceful drain (SIGTERM → drain → shutdown)
- **Phase δ: Auth + Rate Limiting + Security** — 4-stream parallel implementation:
  - **Auth middleware** (`auth_middleware.py`) — ASGI `AuthMiddleware` (Bearer `sk-pm-*` key verification, health endpoint bypass, audit event logging via repository)
  - **SQLite persistent storage** (`repository_sqlite.py`) — `SqliteRepository` implementing `RepositoryProtocol` (aiosqlite, `api_keys`/`audit_log`/`usage_records` tables, `PRAGMA user_version` schema migration). `--db-path` / `PAGEMAP_DB_PATH` flag
  - **Repository abstraction** (`repository.py`) — `RepositoryProtocol` (runtime_checkable), `AuditEvent`/`UsageRecord` frozen dataclasses, `InMemoryRepository` backward-compat wrapper
  - **Rate limit middleware** (`rate_limit_middleware.py`) — ASGI `RateLimitMiddleware` (token bucket, `X-RateLimit-*` IETF headers, 429 with RFC 9457 body, fire-and-forget usage metering)
  - **Security headers middleware** (`security_headers.py`) — ASGI `SecurityHeadersMiddleware` (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'`, `Cache-Control: no-store`). TLS 1.3 enforcement via `--require-tls` / `PAGEMAP_REQUIRE_TLS` (HSTS + 421 Misdirected Request)
  - **Token security** (`token_security.py`) — `scrub_from_text()`/`scrub_headers()`/`contains_token()` for API key scrubbing in logs/responses
  - **Session isolation hardening** — `context.close()` destroys all cookies/storage on session removal
  - **Browser context recycling** — auto-recycle after `PAGEMAP_MAX_NAVIGATIONS` (default 100) or `PAGEMAP_MAX_SESSION_AGE` (default 1800s)
  - **Per-session resource quotas** — max tabs (`PAGEMAP_MAX_TABS`, default 5), navigation limit, session TTL enforcement
  - **Security event taxonomy** — 4 new telemetry events: `SSRF_BLOCKED`, `DNS_REBINDING_BLOCKED`, `BROWSER_DEAD`, `PROMPT_INJECTION_SANITIZED`
- **Phase I: Middleware chain integration** — Gateway (outermost) → RateLimit → Auth → SecurityHeaders → App (innermost). Repository + RateLimiter initialization in `_run_http_server()`, cleanup in `finally`

### Changed

- **Amazon page_type accuracy** — DOM score cap (`_DOM_CAP=40`), Amazon `/dp/` URL signal (25pts), `dom_add_to_cart` signal (8-language cart button detection), error threshold 15→25
- **HTML entity decoding** — `_unescape_entities()` in `sanitize_text()`/`sanitize_content_block()`. `\xa0` → normal space normalization. Applied to `_extract_text_lines()`, interactable/title extraction. 42 new tests
- **`_extract_pruning_metadata` helper extraction** — DRY improvement for metadata handling in page_map_builder (NB-C)
- **Content rescue deepcopy removal** — unnecessary `copy.deepcopy(el)` eliminated in AOM content rescue (NB-A)
- **README Output Example** — replaced idealized Nike output with real Zara product page output. Example prompts updated to match realistic scenarios
- **Community stats unified** — "16 sites" → "11 e-commerce sites" across README and community posts
- **ruff lint/format** — resolved all ruff warnings (I001, SIM103/105/114/117, F401/541/841, B007) across 16 files
- 2940 → 3983 tests passing (+1043)
- New dependency: `aiosqlite>=0.22.0`

### Fixed

- **HTML entity leaks in agent prompt output** — `_unescape_entities()` coverage extended to `pruned_context_builder.py` section headers and sanitizer edge cases. Golden site regression tests added
- **Age-based browser recycling test determinism** — fixed flaky timing-dependent assertions in `test_browser_recycling.py`

### Tests

- **5 new test suites** — `test_cli_smoke.py` (CLI end-to-end), `test_fuzz.py` (fuzzing), `test_output_quality.py` (output quality validation), `test_pipeline_integration.py` (pipeline integration), `test_golden_sites.py` (golden site entity verification). +614 tests (3369→3983)

## [0.6.0] - 2026-02-23

### Added

- **Phase α: RequestContext extraction** — `RequestContext` frozen dataclass + `_create_stdio_context()` helper. All 9 tool `_impl` functions accept `ctx` keyword-only parameter, zero direct `_state` references achieved. Foundation for Phase β (HTTP transport)
- **DOM node guard** — `ResourceExhaustionError` when DOM exceeds 50K nodes. Single `getComputedStyle()`-based evaluate combines node counting + hidden element detection
- **HTML size limit** — 5MB cap on `page.content()`. Applied to all 3 code paths: `build_page_map_live`, `build_page_map_from_page`, `rebuild_content_only`
- **Hidden content 2-layer detection** — (1) JS `getComputedStyle()` DOM removal (`display:none`, `visibility:hidden`, `opacity:0`, `font-size:0`, off-screen), (2) AOM filter inline style patterns. 43 new tests
- **MCP response size guards** — text 1MB (`PAGEMAP_MAX_TEXT_BYTES`) + screenshot 5MB (`PAGEMAP_MAX_IMAGE_BYTES`). Configurable via env vars, truncation with recovery hint tail marker, telemetry event emission
- **Agent-friendly error messages** — `_safe_error()` + `_RECOVERY_HINTS` recovery hint system. Actionable hints on all error paths
- **TOOL_ERROR telemetry** — `pagemap.tool.error` event emitted on `_safe_error()` calls, enriched with `session_id`
- **Resource guard telemetry** — `pagemap.guard.resource_triggered`, `pagemap.guard.response_size_exceeded` event types with TypedDict payloads and builder functions
- **Docker infrastructure** — `Dockerfile` 2-stage build (uv + Playwright + non-root user), `docker-compose.yml`, `.github/workflows/docker.yml` CI/CD
- **GitHub Codespaces / Devcontainer** — `.devcontainer/devcontainer.json` (MS Playwright image + uv feature + 3 VS Code extensions)
- **Cursor Marketplace plugin** — `.cursor-plugin/plugin.json` + `rules/` + `mcp.json`
- **VS Code MCP Gallery support** — OCI package entry added to `server.json`

### Changed

- **CI/CD hardening** — GitHub Actions SHA pinning, `pip-audit --strict`, `bandit -r`, CodeQL SAST, Dependabot (pip + github-actions + docker), gitleaks pre-commit hook, weekly latest-deps CI job
- **Guard helper extraction** — `_check_html_size()`, `_check_resource_limits()` refactored into standalone functions
- **Telemetry async flush** — `flush_async` prevents event loop blocking
- 2198 → 2266 tests passing (+68)

### Fixed

- **font-size:0 regex false positive** — valid values like `0.5em`, `0.875rem` were incorrectly matched as `font-size:0`. 43 regression tests added
- release.sh pyproject.toml readme path patching
- ruff lint/format fixes for telemetry and test files

## [0.5.2] - 2026-02-22

### Added

- **Chromium auto-install on first use** — `_auto_install_chromium()` module-level function: subprocess PIPE to prevent STDIO contamination, once-per-process guard, 300s timeout. `_launch_browser()` try-except chain for "executable doesn't exist" detection → auto-install → retry
- **Claude Code Plugin bundle** — `.claude-plugin/plugin.json` + `skills/browse-page/SKILL.md`. Install via `claude plugin add pagemap`. MCP server auto-registered, workflow guide included
- **MCP Tool Annotations on all 9 tools** — `readOnlyHint: true` (get_page_map, get_page_state, take_screenshot, scroll_page, wait_for, batch_get_page_map), `destructiveHint: true` (execute_action, fill_form), `destructiveHint: false` (navigate_back)
- **6 client config snippets** — Claude Code, Cursor, Claude Desktop (macOS/Windows), VS Code Copilot, Windsurf. Copy-paste JSON blocks in README
- **10 example prompts** — Product Information, Form & Search, Multi-Site Comparison, Multi-Step Workflows, Content Extraction
- **Pricing section in README** — "Local (STDIO): Free forever" + "Cloud API: Coming soon"

### Fixed

- **C4: Wikipedia page type misclassification** — `url_wikipedia_domain` + `dom_mw_content` signals added to classifier. 6 regression tests
- **C5: product_detail pruned_context 61 chars** — payment promo filter + footer noise filter + original price/discount extraction. 17 tests
- PruningError hierarchy, runner_up index, hostname matching, content_hash sentinel fixes
- Pruning enrichment + `_safe_error()` URL path preservation
- `.env` and snapshot directory path resolution
- CI lint + test collection errors (hypothesis importorskip, ruff format)

### Changed

- Phase 6.2 xpath prefix O(n*d) optimization + Phase 6.3 regex precompilation (14 patterns)
- Phase 7 pruning test reinforcement — 255 new tests (total pruning tests: 268 → 768)
- `batch_get_page_map` refactored with Tool Annotations
- `server.json` updated to v0.5.2 with MCP Registry schema `2025-12-11`
- 2198 tests passing (+0 from v0.5.0, test count stable)

### Benchmarks

- **Phase 3+ results**: PageMap **84.7%** task success (was 63.6% in Phase 3), +21.1%p improvement
- PageMap avg 2,710 tokens/task, $1.06 for 94 tasks
- vs Playwright MCP/CLI: **+23.3%p** success, **5.1x** fewer tokens
- vs Firecrawl: **+20.2%p** success, **5.1x** fewer tokens
- vs Full Playwright HTML: **+22.9%p** success, **20.3x** fewer tokens
- Snapshot recovery for 5 sites (H&M, Zara, COS, W Concept, SSF Shop)
- Engine bug fixes: pruner price limit, TABLE measurement preservation, price filter ordering
- AOM filter improvements: role="main" detection, e-commerce patterns, product noise override

## [0.5.0] - 2026-02-21

### Added

- **Diff-based updates** — `to_agent_prompt_diff()` outputs changed sections only, unchanged sections marked `— unchanged`. 3-tier rebuild: cache hit (~100ms) / content refresh (~500ms) / full rebuild (~1.5s)
- **URL-based PageMap cache** — `PageMapCache` 2-layer architecture (active + URL LRU 20 entries), TTL 90s safety net, DOM fingerprint + content_hash freshness validation, `CacheStats` observability
- **Template cache** — same-domain/page-type pruning rules pre-loaded for instant reuse across product pages
- **Cache invalidation strategy** — `InvalidationReason` enum (10 variants), hard/soft auto-classification. `ServerState` class encapsulates session, cache, and lock (42 global state references unified)
- **CJK token penalty correction** — Korean 9.4x penalty (0.61 chars/token vs English 5.75) compensated via language-aware budget weights. `compute_token_budget()` pure function applied to all 5 builder paths
- **`batch_get_page_map` tool** — parallel multi-URL processing with semaphore concurrency (max 5), per-tab 60s + global 120s timeout, LRU cache storage, SSRF validation
- **Session concurrency guard** — `tool_lock` serializes all 9 MCP tool handlers. Lock ordering: tool_lock → _session_lock
- **Hybrid `networkidle` strategy** — load → 6s networkidle budget → DOM settle fallback
- **Pruning failure signals** — `_pruning_warnings` metadata propagated to agent prompt
- **Timeout diagnostics** — `PipelineTimer` stage-by-stage tracking with `timeout_report()` hints
- **Latency optimizations** — H5: single-pass pruning decompose; H6: `asyncio.to_thread()` + 30s timeout for pruning event loop unblock

#### Pruning improvements (v0.4.0 scope, included in this release)

- **FORM chunk pruning restoration** — FORM ChunkType added to pruner in-main/no-main rules
- **`<aside>` filter sidebar preservation** — AOM filter weight 0.7 for interactive descendants
- **in-main 50-char threshold relaxation** — `_is_high_value_short_text()` preserves price/stock/shipping/discount patterns regardless of length
- **MEDIA chunk pruning restoration** — MEDIA caption preservation (text>10/20) in pruner rules
- **Action result summary** — `execute_action` returns structured JSON response (page changes, URL changes, DOM mutation detection)
- **Navigation hints** — pagination info, filter sidebar references in PageMap metadata
- **Dynamic schema detection** — domain fast path → gov TLD → JSON-LD @type sniffing → Generic fallback cascade. `SchemaName(StrEnum)` 6 members
- **Page type expansion** — weighted voting classifier, 5→15 page types (login, form, checkout, dashboard, help_faq, settings, error, documentation, landing)

#### Latency improvements (v0.4.0 scope, included in this release)

- **H1. Orchestrator parallelization** — `detect_all()` + `get_page_html()` via `asyncio.gather()`
- **H2. Dead regex removal** — unused regex calls in `_extract_text_lines()` deleted
- **H3. CDP session reuse** — cached CDP session in `get_ax_tree()` eliminates IPC overhead
- **H4. Dynamic navigation wait** — `MutationObserver`-based DOM stability detection replaces hardcoded sleep

#### Benchmark expansion

- **94 static tasks** (was 62) across 11 e-commerce sites, 14 categories
- **24 live tasks** (was 10) — 6 new categories: complex multistep, filter/sort, pagination, multi-field form, tab interaction, file navigation
- **7-condition comparison** — page_map, full_playwright, truncated_pw, playwright_skill, firecrawl, jina_reader, readability
- **4 new validators** — `contains_discount_info`, `multi_field_complete`, `contains_measurement`, `count_in_range`
- **`PlaywrightSkillConverter`** — CDP AX tree extraction simulating Playwright MCP `browser_snapshot` format
- **URL health check pipeline** — `check_urls.py`, `refresh_urls.py`, 3 CLI commands (`check-urls`, `refresh-urls`, `refresh-and-collect`)

#### Stability

- **CDP call individual timeouts** — `asyncio.timeout()` on 5 CDP call sites: AX tree 15s, CSS resolution 10s, Tier 3 10s
- **CDP session leak prevention** — `_cdp_session()` async context manager + `asyncio.shield(cdp.detach())`

### Changed

- **License: MIT → AGPL-3.0-only** — all source files updated with SPDX headers, classifiers and README badges updated
- `ServerState` class encapsulates session, cache, and lock
- CLI benchmark commands use dynamic constants (`CONVERTERS`, `ALL_CONDITIONS`, `COMPETITOR_CONDITIONS`) instead of hardcoded strings
- MCP server tool count: 8 → 9 (`batch_get_page_map`)
- 1899 tests passing (+751 from v0.3.0)

### Benchmarks

- PageMap 63.6% success vs Playwright MCP/CLI 61.5% vs Firecrawl 64.5% — comparable accuracy, **5.7x fewer tokens**
- PageMap avg 2,403 tokens/task vs competitors 11-14K
- Cost: $0.97 for 94 tasks vs $4.09 (Playwright) / $3.97 (Firecrawl) / $2.26 (Jina)
- Structure value confirmed: +40.5%p over truncated Playwright at same token budget
- New task categories (discount, table, counting, comparison): PageMap 84.8% vs competitors 75.0%

## [0.3.0] - 2026-02-19

### Added

- **New MCP tools (P6)** — 5 new tools + 1 new action, expanding from 3 to 8 total tools
  - `scroll_page(direction, amount)`: up/down scrolling by page, half-page, or pixel amount. Viewport-based delta calculation, scroll position metadata with atTop/atBottom hints. Page map auto-invalidated
  - `take_screenshot(full_page)`: viewport or full-page PNG screenshot via FastMCP `Image` native return. Standalone diagnostic tool — does not require an active Page Map
  - `navigate_back()`: browser history back navigation with SSRF post-check. Blocked URLs redirected to `about:blank`
  - `fill_form(fields)`: Pydantic-typed batch form filling. Sequential execution with 300ms inter-field settle, stop-on-first-error, per-field navigation/popup/SSRF check, DOM change detection
  - `wait_for(text, text_gone, timeout)`: dual-mode text appear/disappear waiting. Parameterized `page.wait_for_function()` (JS injection safe), 500-char text limit, 30s timeout cap
  - `hover` action in `execute_action`: hover on any interactive element with 500ms settle time, click-equivalent retry policy, DOM change detection
- **Popup/new tab auto-handling** — `context.on("page")` detection, SSRF check before switching, blocked popups closed automatically
- **JS dialog auto-handling** — alert/beforeunload auto-accepted, confirm/prompt auto-dismissed. `DialogInfo` buffered via `drain_dialogs()` for agent visibility
- **Same role:name element disambiguation** — CSS selector fallback when multiple elements share identical role and name

### Changed

- MCP server tool count: 3 → 8
- 1148 tests passing (+200 from v0.2.0)

## [0.2.0] - 2026-02-19

### Added

- **execute_action reliability overhaul (P2)**
  - 3-strategy locator fallback chain: `get_by_role(exact)` → CSS selector → role(`.first`, degraded)
  - CSS selector field on `Interactable` (Tier 1-2 CDP-based + Tier 3 batch JS inline generation)
  - Action retry logic: up to 2 retries with 15s wall-clock budget and locator re-resolution; click retried only on pre-dispatch failures
  - DOM change detection: pre/post structural fingerprint comparison catches URL-stable DOM mutations (modals, SPA navigations)
  - Overall execute_action timeout (30s) via `asyncio.wait_for`
  - Browser death detection (`TargetClosedError`, connection lost) with automatic `_last_page_map` invalidation and recovery guidance
  - Affordance-action compatibility pre-check (e.g. `type` on a button blocked early with suggested action)
  - Tier 3 CDP N+1 elimination: per-element 4x sequential CDP calls → single batch `Runtime.evaluate`
- **SSRF 4-layer defense (S2)**
  - `_normalize_ip()` pure-arithmetic parsing (octal/hex/decimal bypass defense)
  - Pre-nav DNS resolve + IP validation (`_resolve_dns` + `_validate_resolved_ips`, dual `is_global` check)
  - Post-nav DNS revalidation (redirect chain TOCTOU mitigation)
  - Context route guard (`install_ssrf_route_guard`, document/subdocument JS navigation blocking)
  - Post-action navigation SSRF check with `about:blank` redirect on block
- **Browser hardening (S3)**
  - Chromium launch args hardening (WebRTC IP leak prevention, ServiceWorker disable, permission deny, telemetry blocking)
  - Context options hardening (`service_workers="block"`, `accept_downloads=False`)
  - Internal protocol blocking expanded (`view-source://`, `blob:`, `data:`, `about:` — page-level → context-level)
  - Markdown injection defense (`javascript:`/`vbscript:`/`data:`/`blob:` URI neutralization)
- **`--allow-local` flag for local development (P6)**
  - `--allow-local` CLI flag: opt-in access to loopback (127.x, ::1), RFC 1918 (10.x, 172.16-31.x, 192.168.x), IPv6 ULA (fc00::/7)
  - `PAGEMAP_ALLOW_LOCAL` env var: alternative for containerized deployments
  - Cloud metadata endpoints (169.254.x.x, `metadata.google.internal`) remain unconditionally blocked
- **AX tree failure isolation (P8)** — `detect_interactables_ax()` failure no longer crashes entire build; graceful degradation returns pruning results only

### Changed

- `_validate_url()`: cloud metadata hosts checked first (always blocked), `BLOCKED_HOSTS` now respects `--allow-local`
- `_validate_resolved_ips()`: cloud metadata IPs prioritized, `_is_local_ip()` exemption for `--allow-local`
- `main()`: extracted `_parse_server_args()`, SECURITY warning logged when `--allow-local` is active
- 948 tests passing (+342 from v0.1.0)

## [0.1.3] - 2026-02-17

### Added

- GitHub Actions CI pipeline (lint + test, Python 3.11/3.12/3.13 matrix)
- CD pipeline for automated PyPI publishing via GitHub Release (OIDC trusted publishers)
- CI badge in README

### Changed

- Applied ruff format to entire codebase
- Excluded development config from public release

## [0.1.2] - 2026-02-17

### Fixed

- **Credentials accidentally included in sdist** — MCP Registry auth token files removed from distribution
- sdist exclude list hardened: `todo/`, `.playwright-mcp/`, credential files, `.env`, internal README excluded
- `.gitignore` updated with credential file patterns

### Changed

- `pyproject.toml` readme target changed from `README.md` (private) → `README_PUBLIC.md` (public) — fixes PyPI page showing internal README in v0.1.0~v0.1.1

## [0.1.1] - 2026-02-17 (yanked — credentials included in sdist)

### Added

- `server.json` for Official MCP Registry publishing
- `glama.json` for Glama MCP directory ownership
- `mcp-name` marker in README for PyPI ownership verification

### Known Issues

- Credential files accidentally included in sdist (fixed in v0.1.2)
- `pyproject.toml` readme pointed to internal README, exposing it on PyPI page

## [0.1.0] - 2026-02-16 (yanked — credentials included in sdist)

### Added

- Initial PyPI release (`pip install retio-pagemap`)
- MCP server with 3 tools: `get_page_map`, `execute_action`, `get_page_state`
- 3-tier interactive element detection (ARIA roles, implicit HTML roles, CDP event listeners)
- 5-stage HTML pruning pipeline (HTMLRAG, script extraction, semantic filtering, schema-aware chunks, compression)
- 97% HTML token reduction (2-5K tokens per page)
- Structured metadata extraction (JSON-LD, itemprop, Open Graph, h1 cascade)
- Multilingual support (ko, en, ja, fr, de)
- Security hardening: prompt injection defense, SSRF protection, action sandboxing, browser crash recovery
- CLI: `pagemap build`, `pagemap serve`
- Python API: `build_page_map_live()`, `build_page_map_offline()`
- 606 tests passing

### Benchmarks

- 95.2% task success across 66 tasks on 9 e-commerce sites
- 5.6x fewer tokens than Firecrawl/Jina Reader
- $0.58 total cost for 66 tasks (vs $2.66 Firecrawl, $1.54 Jina Reader)
