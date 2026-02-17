"""Content sanitization for prompt injection defense.

PageMap output enters the user's LLM context directly. Malicious web content
can manipulate the LLM via role-prefix injection, hidden Unicode, or ANSI
escape sequences. This module provides multi-layer defense:

1. sanitize_text() — short fields (element names, titles, metadata values)
2. sanitize_content_block() — large blocks (pruned_context)
3. add_content_boundary() — wraps output with source-tagged markers
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

# Unicode control characters that can be used for prompt injection
# Zero-width chars, bidi overrides, interlinear annotations
_CONTROL_CHAR_RE = re.compile(
    r"[\u200B-\u200F\u202A-\u202E\u2060-\u2069\uFEFF\uFFF9-\uFFFB"
    r"\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]"
)

# ANSI escape sequences
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Role prefix patterns that could trick an LLM
# Matches both line-start and mid-text patterns like "[SYSTEM: ...]"
_ROLE_PREFIX_RE = re.compile(
    r"\[?\s*(?:SYSTEM|ASSISTANT|USER|HUMAN|AI|ADMIN|INSTRUCTION|OVERRIDE"
    r"|IMPORTANT|IGNORE|HACK|COMMAND)\s*[:\]]\s*",
    re.IGNORECASE,
)

# Boundary tag patterns — prevents content from escaping <web_content> markers
_BOUNDARY_TAG_RE = re.compile(
    r"<\s*/?\s*web_content[\w]*[^>]*>",
    re.IGNORECASE,
)


def sanitize_text(text: str, max_len: int = 256) -> str:
    """Sanitize a short text field (element names, titles, metadata values).

    - Strips Unicode control characters (zero-width, bidi overrides)
    - Removes ANSI escape sequences
    - Escapes role-prefix patterns that could inject instructions
    - Collapses newlines into spaces
    - Truncates to max_len
    """
    if not text:
        return text

    # Remove ANSI escapes
    text = _ANSI_ESCAPE_RE.sub("", text)

    # Remove Unicode control characters
    text = _CONTROL_CHAR_RE.sub("", text)

    # Collapse newlines to prevent multi-line injection
    text = text.replace("\n", " ").replace("\r", " ")

    # Escape role-prefix patterns
    text = _ROLE_PREFIX_RE.sub("", text)

    # Strip boundary tags to prevent content escaping
    text = _BOUNDARY_TAG_RE.sub("", text)

    # Collapse whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()

    # Truncate
    if len(text) > max_len:
        text = text[:max_len]

    return text


def sanitize_content_block(text: str, max_len: int = 50_000) -> str:
    """Sanitize a large content block (pruned_context).

    Same sanitization as sanitize_text but with a higher length limit
    and newlines are preserved (content structure matters).
    """
    if not text:
        return text

    # Remove ANSI escapes
    text = _ANSI_ESCAPE_RE.sub("", text)

    # Remove Unicode control characters (preserve \n and \t)
    text = _CONTROL_CHAR_RE.sub("", text)

    # Escape role-prefix patterns on each line
    text = _ROLE_PREFIX_RE.sub("", text)

    # Strip boundary tags to prevent content escaping
    text = _BOUNDARY_TAG_RE.sub("", text)

    # Truncate
    if len(text) > max_len:
        text = text[:max_len]

    return text


def add_content_boundary(text: str, source_url: str) -> str:
    """Wrap content with nonce-tagged boundary markers identifying the source.

    Uses a random nonce in the tag name (e.g. <web_content_a8f3b2c1...>) so
    that malicious content cannot predict and forge closing tags.
    """
    nonce = secrets.token_hex(8)  # 16 hex chars, 2^64 possibilities
    tag = f"web_content_{nonce}"
    text = _BOUNDARY_TAG_RE.sub("", text)  # defense-in-depth
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'<{tag} source="{_escape_attr(source_url)}" timestamp="{ts}">\n{text}\n</{tag}>'


def _escape_attr(value: str) -> str:
    """Escape a string for use in an XML-like attribute."""
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
