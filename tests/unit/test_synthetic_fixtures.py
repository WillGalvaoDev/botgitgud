from __future__ import annotations

from typing import Any


def test_synthetic_user_timeline_shape(synthetic_user_timeline: dict[int, list[float]]) -> None:
    assert len(synthetic_user_timeline) == 3
    for times in synthetic_user_timeline.values():
        assert times == sorted(times)


def test_synthetic_cohort_fireball_presence_is_100pct(
    synthetic_cohort: list[dict[str, Any]],
) -> None:
    from synthetic import FIREBALL_ID

    assert len(synthetic_cohort) == 10
    assert all(FIREBALL_ID in p["timeline"] for p in synthetic_cohort)
    assert all(len(p["timeline"][FIREBALL_ID]) == 4 for p in synthetic_cohort)


def test_synthetic_cohort_big_cooldown_presence_is_50pct(
    synthetic_cohort: list[dict[str, Any]],
) -> None:
    from synthetic import BIG_COOLDOWN_ID

    present = sum(1 for p in synthetic_cohort if BIG_COOLDOWN_ID in p["timeline"])
    assert present == 5  # exactly half of 10 — below the 0.70 eligibility threshold
