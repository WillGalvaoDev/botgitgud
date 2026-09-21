"""Discord Markdown and mention sanitization for user-controlled fields."""

from __future__ import annotations

import re

_ELLIPSIS = "…"
_ZWSP = "\u200b"
_MENTION_EVERYONE_HERE = re.compile(r"@(everyone|here)")
_MENTION_TAG = re.compile(r"<([@#][!&]?)")
_MARKDOWN_ESCAPE_CHARS = ("\\", "*", "_", "`", "~", "|", "[", "]", "(", ")")


def clamp_text(text: str, max_chars: int) -> str:
    """Truncate by code point while never exceeding ``max_chars``."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return _ELLIPSIS
    return text[: max_chars - 1] + _ELLIPSIS


def sanitize_field(text: str) -> str:
    """Neutralize Discord Markdown and mentions in untrusted text."""
    text = _MENTION_EVERYONE_HERE.sub(f"@{_ZWSP}\\1", text)
    text = _MENTION_TAG.sub(f"<{_ZWSP}\\1", text)
    for char in _MARKDOWN_ESCAPE_CHARS:
        text = text.replace(char, "\\" + char)
    return text


def safe_field(text: str, max_chars: int) -> str:
    """Sanitize first, then truncate to preserve the requested hard limit."""
    return clamp_text(sanitize_field(text), max_chars)
