"""PageMap serialization: JSON and agent prompt formats.

Two output formats:
- JSON: structured data for programmatic consumption
- Agent prompt: minimal-token format for LLM agent consumption
"""

from __future__ import annotations

import json
from typing import Any

from pagemap.preprocessing.preprocess import count_tokens
from pagemap.sanitizer import add_content_boundary, sanitize_content_block, sanitize_text

from . import PageMap


def to_json(page_map: PageMap, indent: int = 2) -> str:
    """Serialize PageMap to JSON string.

    Args:
        page_map: PageMap to serialize
        indent: JSON indentation level

    Returns:
        JSON string
    """
    data = {
        "url": page_map.url,
        "title": page_map.title,
        "page_type": page_map.page_type,
        "interactables": [
            {
                "ref": i.ref,
                "role": i.role,
                "name": i.name,
                "affordance": i.affordance,
                "region": i.region,
                "tier": i.tier,
                **({"value": i.value} if i.value else {}),
                **({"options": i.options} if i.options else {}),
            }
            for i in page_map.interactables
        ],
        "pruned_context": page_map.pruned_context,
        "images": page_map.images,
        **({"metadata": page_map.metadata} if page_map.metadata else {}),
        "meta": {
            "pruned_tokens": page_map.pruned_tokens,
            "interactable_count": page_map.total_interactables,
            "generation_ms": round(page_map.generation_ms, 1),
            "tier_counts": page_map.tier_counts,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=indent)


def to_dict(page_map: PageMap) -> dict[str, Any]:
    """Serialize PageMap to a dictionary."""
    return json.loads(to_json(page_map))


def to_agent_prompt(page_map: PageMap, include_meta: bool = False) -> str:
    """Serialize PageMap to minimal-token agent prompt format.

    Format:
        URL: coupang.com/vp/products/123
        Type: product_detail

        ## Actions
        [1] searchbox: 쿠팡 검색 (type)
        [2] button: 장바구니 담기 (click)
        [3] select: 사이즈 선택 (select) options=[S,M,L,XL]

        ## Info
        제목: 오버핏 레더 자켓
        가격: 189,000원 (원가 259,000원)
        평점: 4.6 (847개 리뷰)

    Args:
        page_map: PageMap to serialize
        include_meta: include token counts and generation time

    Returns:
        Formatted string for LLM consumption
    """
    lines: list[str] = []

    # Header
    lines.append(f"URL: {page_map.url}")
    if page_map.title:
        lines.append(f"Title: {sanitize_text(page_map.title)}")
    lines.append(f"Type: {page_map.page_type}")
    lines.append("")

    # Actions section
    if page_map.interactables:
        lines.append("## Actions")
        for item in page_map.interactables:
            name = sanitize_text(item.name)
            line = f"[{item.ref}] {item.role}: {name} ({item.affordance})"
            if item.value:
                line += f' value="{sanitize_text(item.value)}"'
            if item.options:
                opts = ",".join(sanitize_text(o, max_len=100) for o in item.options[:8])
                if len(item.options) > 8:
                    opts += f"...+{len(item.options) - 8}"
                line += f" options=[{opts}]"
            lines.append(line)
        lines.append("")

    # Info section — wrapped with content boundary
    if page_map.pruned_context:
        lines.append("## Info")
        sanitized_context = sanitize_content_block(page_map.pruned_context)
        lines.append(add_content_boundary(sanitized_context, page_map.url))
        lines.append("")

    # Images section
    if page_map.images:
        lines.append("## Images")
        for i, url in enumerate(page_map.images[:5], 1):
            lines.append(f"  [{i}] {url}")
        lines.append("")

    # Optional meta
    if include_meta:
        prompt_text = "\n".join(lines)
        total_tokens = count_tokens(prompt_text)
        lines.append("## Meta")
        lines.append(f"Tokens: ~{total_tokens}")
        lines.append(f"Interactables: {page_map.total_interactables}")
        lines.append(f"Generation: {page_map.generation_ms:.0f}ms")

    return "\n".join(lines)


def estimate_prompt_tokens(page_map: PageMap) -> int:
    """Estimate total token count of the agent prompt format."""
    prompt = to_agent_prompt(page_map)
    return count_tokens(prompt)
