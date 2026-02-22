# Browse Page with PageMap

Use the PageMap MCP server to browse, read, and interact with web pages. PageMap compresses ~100K-token HTML into a 2-5K-token structured map while preserving every actionable element.

## Core Workflow

### 1. Read a page

Call `get_page_map` with a URL. The response contains:

- **Actions** — Interactive elements with numbered `[ref]` identifiers and affordances (click, type, select, hover)
- **Info** — Compressed HTML with prices, titles, ratings, and key content
- **Images** — Product/content image URLs
- **Metadata** — Structured data from JSON-LD and Open Graph

### 2. Interact with elements

Use `execute_action` with a ref number from the Actions section:

- `execute_action(ref=3, action="click")` — Click a button or link
- `execute_action(ref=5, action="type", value="search query")` — Type into an input
- `execute_action(ref=7, action="select", value="Large")` — Select a dropdown option
- `execute_action(ref=2, action="hover")` — Hover to reveal menus

### 3. Fill forms

Use `fill_form` to batch-fill multiple fields in one call:

```
fill_form(fields=[
  {"ref": 3, "value": "John Doe"},
  {"ref": 4, "value": "john@example.com"},
  {"ref": 5, "value": "Large"}
])
```

## Tips

- **Refs expire** after page changes. If you get a "refs expired" error, call `get_page_map` again to refresh.
- **Scroll** with `scroll_page(direction="down")` to reveal more content, then call `get_page_map` to see newly loaded elements.
- **Wait** for dynamic content with `wait_for(text="Add to Cart")` before interacting.
- **Compare pages** with `batch_get_page_map(urls=[...])` to read multiple URLs in parallel.
- **Check state** with `get_page_state` for a lightweight URL/title check without rebuilding the full map.
- **Screenshot** with `take_screenshot` when you need visual confirmation.
- **Go back** with `navigate_back` to return to the previous page.

## Available Tools

| Tool | Purpose |
|------|---------|
| `get_page_map` | Navigate to URL, return structured map with ref numbers |
| `execute_action` | Click, type, select, hover by ref number |
| `fill_form` | Batch-fill multiple form fields |
| `get_page_state` | Lightweight state check (URL, title) |
| `scroll_page` | Scroll up/down/to position |
| `wait_for` | Wait for text to appear/disappear |
| `take_screenshot` | Capture viewport or full page |
| `navigate_back` | Go back in browser history |
| `batch_get_page_map` | Read multiple URLs in parallel |
