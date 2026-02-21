<!-- mcp-name: io.github.Retio-ai/pagemap -->

# PageMap

**The browsing MCP server that fits in your context window.**

Compresses ~100K-token HTML into a 2-5K-token structured map while preserving every actionable element. AI agents can **read and interact** with any web page at 97% fewer tokens.

> *"Give your agent eyes and hands on the web."*

[![CI](https://github.com/Retio-ai/Retio-pagemap/actions/workflows/ci.yml/badge.svg)](https://github.com/Retio-ai/Retio-pagemap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/retio-pagemap)](https://pypi.org/project/retio-pagemap/)
[![Python](https://img.shields.io/pypi/pyversions/retio-pagemap)](https://pypi.org/project/retio-pagemap/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

---

## Why PageMap?

Playwright MCP dumps 50-540KB accessibility snapshots per page, overflowing context windows after 2-3 navigations. Firecrawl and Jina convert HTML to markdown — read-only, no interaction.

PageMap gives your agent a **compressed, actionable** view of any web page:

| | PageMap | Playwright MCP | Firecrawl | Jina Reader |
|--|:------:|:---------:|:-----------:|:--------:|
| **Tokens / page** | **2-5K** | 6-50K | 10-50K | 10-50K |
| **Interaction** | **click / type / select / hover** | Raw tree parsing | Read-only | Read-only |
| **Multi-page sessions** | **Unlimited** | Breaks at 2-3 pages | N/A | N/A |
| **Task success (94 tasks)** | **63.6%** | 61.5% | 64.5% | 57.8% |
| **Avg tokens / task** | **2,403** | 13,737 | 13,886 | 11,423 |
| **Cost / 94 tasks** | **$0.97** | $4.09 | $3.97 | $2.26 |

> Benchmarked across 11 e-commerce sites, 94 static tasks, 7 conditions. PageMap matches competitors in accuracy while using **5.7x fewer tokens** and is the only tool that supports **interaction**.

---

## Quick Start

### MCP Server (Claude Code / Cursor)

```bash
pip install retio-pagemap
playwright install chromium
```

Add to your project's `.mcp.json`:

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

Restart your IDE. Nine tools become available:

| Tool | Description |
|------|-------------|
| `get_page_map` | Navigate to URL, return structured PageMap with ref numbers |
| `execute_action` | Click, type, select, hover on elements by ref number |
| `get_page_state` | Lightweight page state check (URL, title) |
| `take_screenshot` | Capture viewport or full-page screenshot |
| `navigate_back` | Go back in browser history |
| `scroll_page` | Scroll up/down by page, half-page, or pixel amount |
| `fill_form` | Batch-fill multiple form fields in one call |
| `wait_for` | Wait for text to appear or disappear on the page |
| `batch_get_page_map` | Get Page Maps for multiple URLs in parallel |

### CLI

```bash
pagemap build --url "https://www.example.com/product/123"
```

---

## Output Example

```yaml
URL: https://www.example.com/product/air-max-90
Title: Nike Air Max 90
Type: product_detail

## Actions
[1] searchbox: Search (type)
[2] button: Add to Cart (click)
[3] combobox: Size (select) options=[250,255,260,265,270]
[4] button: Buy Now (click)

## Info
<h1>Nike Air Max 90</h1>
<span itemprop="price">139,000</span>
<span itemprop="ratingValue">4.7</span>
<span>2,341 reviews</span>

## Images
  [1] https://cdn.example.com/air-max-90-1.jpg
```

An agent reads the page and executes `execute_action(ref=3, action="select", value="260")` to select a size — all in one context window.

---

## How It Works

```
Raw HTML (~100K tokens)
  → PageMap (2-5K tokens)
     ├── Actions        Interactive elements with numbered refs
     ├── Info            Compressed HTML (prices, titles, key info)
     ├── Images          Product image URLs
     └── Metadata        Structured data (JSON-LD, Open Graph)
```

**Pipeline:**

```
URL → Playwright Browser
       ├─→ AX Tree ──→ 3-Tier Interactive Detector
       └─→ HTML ─────→ 5-Stage Pruning Pipeline
                         1. HTMLRAG preprocessing
                         2. Script extraction (JSON-LD, RSC payloads)
                         3. Semantic filtering (nav, footer, aside)
                         4. Schema-aware chunk selection
                         5. Attribute stripping & compression
                       → Budget-aware assembly → PageMap
```

### Interactive Detection (3-Tier)

| Tier | Source | Examples |
|:----:|--------|----------|
| 1 | ARIA roles with names | Buttons, links, menus |
| 2 | Implicit HTML roles | `<input>`, `<select>`, `<textarea>` |
| 3 | CDP event listeners | Divs/spans with click handlers |

---

## Reliability

`execute_action` is built for real-world web pages:

- **Locator fallback chain** — `get_by_role(exact)` → CSS selector → degraded match. Handles duplicate labels, dynamic IDs, and shadow DOM
- **Auto-retry** — up to 2 retries within 15s budget with locator re-resolution. Click retried only on pre-dispatch failures to prevent double-submission
- **DOM change detection** — structural fingerprint comparison catches URL-stable mutations (modals, SPA navigations, accordion toggles). Stale refs auto-invalidated
- **Popup & tab handling** — new tabs/popups auto-detected, SSRF-checked, and switched to. Blocked popups closed automatically
- **JS dialog handling** — alert/beforeunload auto-accepted, confirm/prompt auto-dismissed. Dialog content buffered and reported to the agent
- **Crash recovery** — 30s action timeout, browser death detection, automatic session invalidation with recovery guidance

---

## Security

PageMap treats all web content as **untrusted input**:

- **SSRF Defense** — 4-layer protection: scheme whitelist, DNS rebinding defense, private IP blocking, post-redirect DNS revalidation, context-level route guard
- **Browser Hardening** — WebRTC IP leak prevention, ServiceWorker blocking, internal protocol blocking (`view-source:`, `blob:`, `data:`), Markdown injection defense
- **Prompt Injection Defense** — nonce-based content boundaries, role-prefix stripping, Unicode control char removal
- **Action Sandboxing** — whitelisted actions only, dangerous key combos blocked, affordance-action compatibility pre-check
- **Input Validation** — value length limits, timeout enforcement, error sanitization

### Local Development

By default, PageMap blocks all private network access (localhost, 192.168.x.x, etc.)
as an SSRF defense. For local development workflows, enable `--allow-local`:

**Option A: CLI flag**
```json
{ "command": "uvx", "args": ["retio-pagemap", "--allow-local"] }
```

**Option B: Environment variable** (containerized deployments)
```json
{ "command": "uvx", "args": ["retio-pagemap"], "env": {"PAGEMAP_ALLOW_LOCAL": "1"} }
```

Cloud metadata endpoints (169.254.x.x, metadata.google.internal) remain blocked.

---

## Multilingual Support

Built-in i18n for price, review, rating, and pagination extraction:

| Language | Locale | Price formats | Keywords |
|----------|:------:|---------------|----------|
| Korean | `ko` | 원, ₩ | 리뷰, 평점, 다음, 더보기 |
| English | `en` | $, £, € | reviews, rating, next, load more |
| Japanese | `ja` | ¥, 円 | レビュー, 評価, 次へ |
| French | `fr` | €, CHF | avis, note, suivant |
| German | `de` | €, CHF | Bewertungen, Bewertung, weiter |

Locale is auto-detected from the URL domain.

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

        # Agent-optimized text format
        print(to_agent_prompt(page_map))

        # Structured JSON
        print(to_json(page_map))

        # Direct field access
        print(page_map.page_type)        # "product_detail"
        print(page_map.interactables)    # [Interactable(ref=1, role="button", ...)]
        print(page_map.pruned_context)   # compressed HTML
        print(page_map.images)           # ["https://cdn.example.com/img.jpg"]
        print(page_map.metadata)         # {"name": "...", "price": "..."}

asyncio.run(main())
```

For offline processing (no browser):

```python
from pagemap.page_map_builder import build_page_map_offline

html = open("page.html").read()
page_map = build_page_map_offline(html, url="https://example.com/product/123")
```

---

## Requirements

- Python 3.11+
- Chromium (`playwright install chromium`)

## Community

Have a question or idea? Join the conversation in [GitHub Discussions](https://github.com/Retio-ai/Retio-pagemap/discussions).

## License

AGPL-3.0-only — see [LICENSE](LICENSE) for the full text.

For commercial licensing options, contact **retio1001@retio.ai**.

---

*PageMap — Structured Web Intelligence for the Agent Era.*
