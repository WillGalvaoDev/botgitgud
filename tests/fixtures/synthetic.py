"""tests/fixtures/synthetic.py — hand-written fixtures independent of any
live API call or recorded cassette. These are the base for Fase 0-3 unit
tests that need known, exact input properties (T0.5 alignment, T0.6
cadence classification, T2.x cohort statistics, ...).

Shapes mirror legacy/bot.py's own data structures (a spell_id -> sorted
cast-times dict for a single player; a list of such structures — each also
carrying "fight"/"build" metadata — for a reference cohort) since that is
what the Fase 0 corrections (T0.5-T0.8) operate on directly. Later phases
that introduce typed dataclasses (T1.2) can build their models on top of
these same numbers without changing the fixtures.
"""

from __future__ import annotations

from typing import Any

# Spell IDs are arbitrary but stable across this module and any test that
# imports it, so assertions can hardcode them.
FIREBALL_ID = 90001  # short-CD rotational ability
BIG_COOLDOWN_ID = 90002  # long major CD
RARE_PROC_ID = 90003  # single-usage edge case (achado 3.3 / T0.6)


def build_synthetic_user_timeline() -> dict[int, list[float]]:
    """3 abilities with hand-picked cast times.

    - FIREBALL_ID: 5 casts, roughly every ~20s — rotational ability.
    - BIG_COOLDOWN_ID: 2 casts, ~150s apart — a long major CD.
    - RARE_PROC_ID: 1 cast — the "single usage" edge case that
      legacy/bot.py mishandles by treating the cast instant itself as a
      cooldown duration (achado 3.3, fixed in T0.6).
    """
    return {
        FIREBALL_ID: [5.0, 24.0, 46.0, 65.0, 88.0],
        BIG_COOLDOWN_ID: [12.0, 162.0],
        RARE_PROC_ID: [200.0],
    }


def build_synthetic_cohort(n: int = 10) -> list[dict[str, Any]]:
    """`n` reference players shaped like fetch_player_timeline_data()'s
    return value, with two deliberately known statistical properties:

    - FIREBALL_ID: every one of the `n` players casts it at the exact same
      4 instants — presence = 100%, a clean, low-variance rotational CD.
    - BIG_COOLDOWN_ID: only the first `n // 2` players cast it once, each at
      a slightly different instant — presence = 50%, deliberately BELOW the
      0.70 eligibility threshold (T0.6/legacy), to exercise that filter.
    """
    players: list[dict[str, Any]] = []
    fireball_times = [10.0, 130.0, 250.0, 370.0]
    half = n // 2
    for i in range(n):
        timeline: dict[int, list[float]] = {FIREBALL_ID: list(fireball_times)}
        if i < half:
            timeline[BIG_COOLDOWN_ID] = [15.0 + i]
        players.append(
            {
                "fight": {
                    "fight_id": 1,
                    "encounter_id": 9999,
                    "boss_name": "Synthetic Boss",
                    "duration_sec": 400.0,
                },
                "build": {"class": "Mage", "spec": "Fire"},
                "timeline": timeline,
            }
        )
    return players
