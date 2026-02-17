# PageMap

**The browsing MCP server that fits in your context window.**

Compresses ~100K-token HTML into a 2-5K-token structured map while preserving every actionable element. AI agents can **read and interact** with any web page at 97% fewer tokens.

> *"Give your agent eyes and hands on the web."*

[![PyPI](https://img.shields.io/pypi/v/retio-pagemap)](https://pypi.org/project/retio-pagemap/)
[![Python](https://img.shields.io/pypi/pyversions/retio-pagemap)](https://pypi.org/project/retio-pagemap/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Why PageMap?

Playwright MCP dumps 50-540KB accessibility snapshots per page, overflowing context windows after 2-3 navigations. Firecrawl and Jina convert HTML to markdown — read-only, no interaction.

PageMap gives your agent a **compressed, actionable** view of any web page:

| | PageMap | Playwright MCP | Firecrawl | Jina Reader |
|--|:------:|:---------:|:-----------:|:--------:|
| **Tokens / page** | **2-5K** | 50-540K | 10-50K | 10-50K |
| **Interaction** | **click / type / select** | Raw tree parsing | Read-only | Read-only |
| **Multi-page sessions** | **Unlimited** | Breaks at 2-3 pages | N/A | N/A |
| **Task success (66 tasks)** | **95.2%** | 39.7% \* | 60.9% | 61.2% |
| **Cost / 66 tasks** | **$0.58** | $6.71 \* | $2.66 | $1.54 |

> Benchmarked across 9 e-commerce sites, 66 tasks. PageMap uses **5.6x fewer tokens** while being the only tool that supports **interaction**.
>
> \* Playwright MCP figures are from 62-task static benchmark using pre-collected snapshots. SPA sites with empty snapshots lower all snapshot-based scores.

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

Restart your IDE. Three tools become available:

| Tool | Description |
|------|-------------|
| `get_page_map` | Navigate to URL, return structured PageMap with ref numbers |
| `execute_action` | Click, type, select on elements by ref number |
| `get_page_state` | Lightweight page state check (URL, title) |

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

## Security

PageMap treats all web content as **untrusted input**:

- **SSRF Defense** — scheme whitelist, private IP blocking, post-redirect revalidation
- **Prompt Injection Defense** — nonce-based content boundaries, role-prefix stripping, Unicode control char removal
- **Action Sandboxing** — whitelisted actions only, dangerous key combos blocked
- **Input Validation** — value length limits, timeout enforcement, error sanitization

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

## License

MIT

---

*PageMap — Structured Web Intelligence for the Agent Era.*
