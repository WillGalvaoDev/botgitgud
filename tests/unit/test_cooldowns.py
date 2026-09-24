from __future__ import annotations

import pytest

from botgitgud.domain.cooldowns import BASE_COOLDOWNS_S, get_base_cooldown


def test_starts_empty_no_unverified_values_fabricated() -> None:
    """docs/architecture.md: neither Blizzard's nor WCL's spell API exposes a
    cooldown field (verified live) — this table is deliberately empty
    rather than seeded with unverified guesses.
    """
    assert BASE_COOLDOWNS_S == {}


def test_get_base_cooldown_none_for_an_unknown_spell() -> None:
    assert get_base_cooldown(999_999_999) is None


def test_get_base_cooldown_returns_a_curated_value_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(BASE_COOLDOWNS_S, 12345, 120.0)
    assert get_base_cooldown(12345) == 120.0
