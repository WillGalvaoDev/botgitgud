from __future__ import annotations

import pytest

from botgitgud.report.discord_markdown import clamp_text, safe_field, sanitize_field


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("hello", 0, ""),
        ("hello", 1, "…"),
        ("hello", -5, ""),
        ("", 10, ""),
        ("", 0, ""),
        ("hello", 5, "hello"),
        ("hello world", 5, "hell…"),
        ("😀😀😀😀😀", 3, "😀😀…"),
    ],
)
def test_clamp_text_boundaries(text: str, limit: int, expected: str) -> None:
    assert clamp_text(text, limit) == expected


def test_clamp_text_never_exceeds_limit() -> None:
    for limit in range(20):
        assert len(clamp_text("x" * 50, limit)) <= limit


def test_markdown_is_escaped() -> None:
    value = sanitize_field("**bold** _i_ `code` ~~s~~ | [link](url) \\")
    assert r"\*\*bold\*\*" in value
    assert r"\[link\]\(url\)" in value
    assert value.endswith(r"\\")


@pytest.mark.parametrize(
    "mention",
    [
        "@everyone",
        "@here",
        "<@123456789012345678>",
        "<@!123456789012345678>",
        "<@&123456789012345678>",
        "<#123456789012345678>",
    ],
)
def test_mentions_are_neutralized(mention: str) -> None:
    assert mention not in sanitize_field(mention)


def test_safe_field_sanitizes_before_truncating_unicode() -> None:
    result = safe_field("😀**@everyone**終", 12)
    assert len(result) <= 12
    assert "@everyone" not in result
    assert result.endswith("…")
