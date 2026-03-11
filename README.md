<!-- mcp-name: io.github.Retio-ai/pagemap -->

# PageMap

**The browsing MCP server that fits in your context window.**

Compresses ~100K-token HTML into a 2-5K-token structured map while preserving every actionable element. AI agents can **read and interact** with any web page at 97% fewer tokens.

> *"Give your agent eyes and hands on the web."*

[![CI](https://github.com/Retio-ai/Retio-pagemap/actions/workflows/ci.yml/badge.svg)](https://github.com/Retio-ai/Retio-pagemap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/retio-pagemap)](https://pypi.org/project/retio-pagemap/)
[![Python](https://img.shields.io/pypi/pyversions/retio-pagemap)](https://pypi.org/project/retio-pagemap/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Docker](https://img.shields.io/docker/v/retio1001/pagemap?label=Docker)](https://hub.docker.com/r/retio1001/pagemap)
[![Awesome MCP Servers](https://img.shields.io/badge/Awesome-MCP%20Servers-fc60a8?logo=awesomelists&logoColor=white)](https://github.com/punkpeye/awesome-mcp-servers)

---

<!-- ============================================================ -->
<!--  HUMAN GUIDE                                                  -->
<!-- ============================================================ -->

## Why PageMap?

Playwright MCP dumps 50-540KB accessibility snapshots per page, overflowing context windows after 2-3 navigations. Firecrawl and Jina convert HTML to markdown — read-only, no interaction.

PageMap gives your agent a **compressed, actionable** view of any web page:

| | PageMap | Playwright MCP | Firecrawl | Jina Reader |
|--|:------:|:---------:|:-----------:|:--------:|
| **Tokens / page** | **2-5K** | 6-50K | 10-50K | 10-50K |
| **Interaction** | **click / type / select / hover** | Raw tree parsing | Read-only | Read-only |
| **Multi-page sessions** | **Unlimited** | Breaks at 2-3 pages | N/A | N/A |
| **Task success (94 tasks)** | **84.7%** | 61.5% | 64.5% | 57.8% |
| **Avg tokens / task** | **2,710** | 13,737 | 13,888 | 11,424 |
| **Cost / 94 tasks** | **$1.06** | $4.09 | $3.98 | $2.26 |

> Benchmarked across 11 e-commerce sites, 94 static tasks, 7 conditions. 8,100+ tests passing.

---

## Quick Start

Chromium is auto-installed on first use — no manual `playwright install` needed.

### Install

```bash
pip install retio-pagemap
```

### MCP Client Config

Add to Claude Code, Cursor, Windsurf, or Claude Desktop:

```json
{
  "mcpServers": {
    "pagemap": {
      "command": "uvx",
      "args": ["retio-pagemap"]
    }
  }
}
```

> **Claude Desktop (macOS)**: Use the absolute path to `uvx` — run `which uvx` (e.g. `/opt/homebrew/bin/uvx`).

> **VS Code (Copilot)**: Use `"servers"` instead of `"mcpServers"` in `.vscode/mcp.json`.

### Docker

```bash
docker run -p 8000:8000 retio1001/pagemap --transport http
```

---

## Features

### 13 MCP Tools — Read + Interact

Not just reading — your agent can click buttons, fill forms, select options, manage tabs, and navigate across pages. 13 tools cover the full browsing workflow:

`get_page_map` · `execute_action` · `fill_form` · `scroll_page` · `wait_for` · `take_screenshot` · `get_page_state` · `navigate_back` · `batch_get_page_map` · `open_tab` · `switch_tab` · `list_tabs` · `close_tab`

### 16 Page Types, Auto-Detected

PageMap automatically classifies pages and applies optimized extraction for each type:

`product_detail` · `listing` · `search_results` · `article` · `news` · `video` · `login` · `form` · `checkout` · `dashboard` · `help_faq` · `settings` · `error` · `documentation` · `landing` · `blocked`

### E-Commerce Deep Coverage

Built-in support for **30+ major e-commerce sites** across 4 tiers:

- **Global mega-platforms** — Amazon, eBay, AliExpress, SHEIN, Walmart, Rakuten
- **Global fashion** — Zara, H&M, Nike, Uniqlo, ASOS, Zalando, SSENSE, Farfetch, COS
- **Korea** — Coupang, Naver Shopping, Musinsa, 29CM, W Concept, SSG, 11st
- **Japan/China** — ZOZO, Tmall, JD.com, Taobao

Structured extraction of prices, options (size/color), ratings, availability — with automatic cookie consent handling and login barrier detection.

### Smart Recovery

PageMap detects problems and tells your agent what to do:

- **Barrier detection** — Login required? Bot blocked? Out of stock? Age verification? Popup overlay? PageMap adds a `barrier` field with the diagnosis and suggested next steps
- **Cookie consent auto-dismiss** — 7 CMP providers auto-detected (Cookiebot, OneTrust, TrustArc, Didomi, Quantcast, Usercentrics, generic fallback). 5-tier dismiss cascade: CMP JS API → Reject → Accept → Dismiss → Close symbol. GDPR reject-first default policy
- **Popup overlay detection** — AX tree `role="dialog"` + HTML regex 2-phase detection. Promotional popups (newsletter, exit-intent) auto-dismissed
- **Bot detection awareness** — Detects Cloudflare, Turnstile, reCAPTCHA, hCaptcha, and Akamai. Reports the provider and suggests wait/retry strategies
- **Stale ref recovery** — When DOM changes invalidate refs, PageMap returns clear guidance to re-fetch

### Content Intelligence

- **8 JSON-LD schemas** — Product, NewsArticle, VideoObject, FAQPage, Event, LocalBusiness, BreadcrumbList, and ItemList
- **Metadata extraction** — Prices, ratings, reviews, descriptions, images from structured data and DOM fallbacks
- **2-layer caching** — Cache hit (~10ms), content refresh (~500ms), full rebuild (~1.5s). Diff-based updates for unchanged sections

### 10 Languages

Locale auto-detected from URL. Token budgets adjusted for CJK scripts.

| Language | Locale | Language | Locale |
|----------|:------:|----------|:------:|
| English | `en` | Chinese | `zh` |
| Korean | `ko` | Spanish | `es` |
| Japanese | `ja` | Italian | `it` |
| French | `fr` | Portuguese | `pt` |
| German | `de` | Dutch | `nl` |

---

## Deployment

### Local (STDIO)

Default mode. Runs as a local MCP server — no server setup needed.

```bash
retio-pagemap
```

### Docker

```bash
docker run -p 8000:8000 retio1001/pagemap --transport http
```

Multi-architecture images (amd64/arm64) available on [Docker Hub](https://hub.docker.com/r/retio1001/pagemap) and GitHub Container Registry.

---

## Python API

```python
import asyncio
from pagemap.browser_session import BrowserSession
from pagemap.page_map_builder import build_page_map_live
from pagemap.serializer import to_agent_prompt, to_json

async def main():
    async with BrowserSession() as session:
        page_map = await build_page_map_live(session, "https://example.com/product/123")
        print(to_agent_prompt(page_map))   # Agent-optimized text format
        print(to_json(page_map))           # Structured JSON
        print(page_map.page_type)          # "product_detail"
        print(page_map.interactables)      # [Interactable(ref=1, role="button", ...)]
        print(page_map.metadata)           # {"name": "...", "price": "..."}

asyncio.run(main())
```

For offline processing (no browser):

```python
from pagemap.page_map_builder import build_page_map_offline

page_map = build_page_map_offline(open("page.html").read(), url="https://example.com/product/123")
```

---

## Security

PageMap treats all web content as untrusted input:

- **SSRF defense** — Multi-layer protection against server-side request forgery
- **Prompt injection defense** — Content boundaries, role-prefix stripping, suspicious content flagging
- **robots.txt compliance** — RFC 9309 compliant. `--ignore-robots` opt-out flag
- **Resource guards** — DOM node limit, HTML size limit, response size limit
- **Session isolation** — Each session has independent cookies and storage, automatically cleaned up

**Local development**: Private IPs are blocked by default. Use `--allow-local` or `PAGEMAP_ALLOW_LOCAL=1`.

### Disclaimer

Users are responsible for complying with the terms of service of target websites and all applicable laws when using PageMap.

---

## Troubleshooting

**"spawn uvx ENOENT" (Claude Desktop on macOS)** — Claude Desktop does not inherit your shell PATH. Run `which uvx` and use the absolute path in your config.

**First page takes a long time** — Chromium cold start takes ~10-30s on first navigation. Subsequent pages load in 1-3 seconds.

**Localhost blocked** — Use `--allow-local` flag or set `PAGEMAP_ALLOW_LOCAL=1`.

**Chromium not found** — Run `pip install retio-pagemap && playwright install chromium` to install manually.

---

## Requirements

- Python 3.11+
- Chromium (auto-installed on first use)

## Community

Have a question or idea? Join the conversation in [GitHub Discussions](https://github.com/Retio-ai/Retio-pagemap/discussions).

## Development

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Retio-ai/Retio-pagemap?quickstart=1)

```bash
git clone https://github.com/Retio-ai/Retio-pagemap.git
cd Retio-pagemap
uv sync --group dev
playwright install chromium
uv run pytest --tb=short -q
```

## Pricing

**Local (STDIO)** — Free forever. Self-hosted, open source under AGPL-3.0.

**Cloud API** — Hosted multi-tenant server with auth, rate limiting, and credit-based billing. Contact **retio1001@retio.ai** for access.

## License

AGPL-3.0-only — see [LICENSE](LICENSE) for the full text.

For commercial licensing options, contact **retio1001@retio.ai**.

---

<!-- ============================================================ -->
<!--  AGENT REFERENCE                                              -->
<!-- ============================================================ -->

## For Agents

*This section is written for AI agents using PageMap as an MCP tool.*

### Tools

| Tool | When to use |
|------|-------------|
| `get_page_map` | **Start here.** Navigate to a URL and get a full structured map with numbered refs. |
| `execute_action` | Click, type, select, or hover using a ref number from the last `get_page_map`. |
| `fill_form` | Fill multiple form fields in one call. More efficient than sequential `execute_action` calls. |
| `get_page_state` | Check current URL and title without a full rebuild. Use after actions that may navigate. |
| `scroll_page` | Scroll to reveal lazy-loaded content before calling `get_page_map` again. |
| `wait_for` | Wait for dynamic content to appear (e.g. after a search or form submit). |
| `take_screenshot` | Capture the visual state when the PageMap alone is ambiguous. |
| `navigate_back` | Go back one step in browser history. |
| `open_tab` | Open a new browser tab and navigate to a URL. |
| `switch_tab` | Switch to a different open tab by index. |
| `list_tabs` | List all open tabs with their URLs and titles. |
| `close_tab` | Close a tab by index. |
| `batch_get_page_map` | Fetch multiple URLs in parallel. Use for comparison tasks. |

### Output Format

```yaml
URL: https://example.com/product/123
Title: Product Name
Type: product_detail          # auto-detected page type

## Actions
[1] button: Add to cart (click)
[2] select: Size (select) — options: S, M, L, XL
[3] link: See all reviews (click)
...

## Info
Price: $49.99
Rating: 4.5 / 5 (128 reviews)
Description: ...

## Images
  [1] https://cdn.example.com/product.jpg

## Meta
Tokens: ~1,800 | Interactables: 24 | Generation: 380ms
```

- **`## Actions`** — Every interactive element on the page with a stable `ref` number.
- **`## Info`** — Key page content extracted from HTML: prices, titles, ratings, descriptions.
- **`## Images`** — Product/content image URLs.
- **`## Meta`** — Token count, interactable count, generation time.

### Barrier Detection

When PageMap encounters a page-level obstacle, it includes a `barrier` field in the response:

```yaml
State:
  barrier: login_required
  barrier_hint: "Login form detected with email + password fields. Use fill_form to authenticate."
```

Possible barriers: `cookie_consent`, `login_required`, `bot_blocked`, `out_of_stock`, `empty_results`, `error_page`, `age_verification`, `region_restricted`, `popup_overlay`.

**When you see a barrier:** follow the `barrier_hint` guidance. For `bot_blocked`, wait and retry. For `login_required`, use `fill_form` with credentials.

### Ref Lifecycle

Refs are assigned by `get_page_map` and remain valid until the page state changes.

**Refs are invalidated when:**
- The page navigates to a new URL
- A DOM mutation occurs (modal opens, SPA navigation, accordion toggles)
- `execute_action` causes a page-level change

**When you get a stale ref error:** call `get_page_map` again to get fresh refs before retrying.

### Token Budget Behavior

When a page exceeds the token budget, content is pruned in this order:
1. Navigation menus, footers, sidebars removed first
2. Secondary body content trimmed
3. `## Actions` and `## Info` are always preserved

If key content seems missing, try `scroll_page` to load lazy content, then `get_page_map` again.

### Recommended Workflow

```
1. get_page_map(url)          → read Actions + Info, pick refs
2. execute_action(ref, ...)   → interact
3. get_page_state()           → confirm navigation occurred
4. get_page_map(new_url)      → get fresh refs for next step
```

For pages with dynamic content (search results, filters):
```
1. get_page_map(url)
2. execute_action(ref, "click")    → trigger search/filter
3. wait_for(text="results")        → wait for content
4. get_page_map(url)               → get updated map
```

### Known Limitations

- **Login-gated pages** — PageMap does not manage sessions or cookies. Authentication must be handled externally.
- **Heavy bot detection** (Cloudflare, Akamai) — May block automated access. PageMap detects the provider and suggests strategies, but cannot bypass active bot mitigation.
- **Private network access** — Blocked by default. Requires `--allow-local` flag.
- **iframes** — Cross-origin iframes are not accessible due to browser security policies.

---

*PageMap — Structured Web Intelligence for the Agent Era.*
