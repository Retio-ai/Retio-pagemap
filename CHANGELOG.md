# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
- Excluded internal config.yaml from public release

## [0.1.2] - 2026-02-17

### Fixed

- **sdist에 MCP Registry 인증 토큰 포함 문제 수정** (`.mcpregistry_github_token`, `.mcpregistry_registry_token`)
- sdist exclude 목록 대폭 강화: `todo/`, `.playwright-mcp/`, `.mcpregistry_*`, `.env`, `README.md`(내부용) 등 제외
- `.gitignore`에 `.mcpregistry_*`, `.playwright-mcp/` 추가

### Changed

- `pyproject.toml` readme를 `README.md`(내부용) → `README_PUBLIC.md`(공개용)로 수정 — v0.1.0~v0.1.1에서 내부용 README가 PyPI 패키지 설명에 노출되던 문제 해결

## [0.1.1] - 2026-02-17 (yanked — sdist에 인증 토큰 포함)

### Added

- `server.json` for Official MCP Registry publishing
- `glama.json` for Glama MCP directory ownership
- `mcp-name` marker in README for PyPI ownership verification

### Known Issues

- sdist에 `.mcpregistry_github_token`, `.mcpregistry_registry_token` 포함됨 (v0.1.2에서 수정)
- `pyproject.toml` readme가 내부용 `README.md`를 가리키고 있어 PyPI 페이지에 내부 README 노출됨

## [0.1.0] - 2026-02-16

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
