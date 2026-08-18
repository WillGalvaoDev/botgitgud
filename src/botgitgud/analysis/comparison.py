"""T0.7 — replaces legacy/bot.py's compare_major_cds_clean.

Combines T0.5 (monotonic alignment) and T0.6 (cadence/classification) into
one comparison per spell, and fixes a second bug on top of achado 3.1:
legacy/bot.py:561 skips any spell the player never cast at all
(`if not user_times: continue`), hiding the worst possible finding — "you
never used this ability" — from the report entirely. Eligible spells with
zero player usage are now included; `align([], ref_times)` naturally
produces an all-MISSED alignment for them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from botgitgud.analysis.alignment import Alignment, align
from botgitgud.analysis.cadence import SpellCadence, classify_cd_type, compute_cadence
from botgitgud.domain.models import PlayerLog, SpellProfile
from botgitgud.domain.spells import SpellCatalog, SpellInfo


@dataclass(frozen=True, slots=True)
class SpellComparison:
    spell: SpellInfo
    cd_type: Literal["MAJOR", "MINOR"]
    cadence: SpellCadence
    presence: float
    alignment: Alignment
    reference_n: int


def compare_spell_usage(
    spell: SpellInfo,
    presence: float,
    user_times: Sequence[float],
    ref_times: Sequence[float],
    n_usages_median: float,
    reference_n: int,
    *,
    base_cooldown: float | None = None,
    gap_penalty: float = 25.0,
) -> SpellComparison:
    cadence = compute_cadence(
        ref_times, n_usages_median=n_usages_median, base_cooldown=base_cooldown
    )
    cd_type = classify_cd_type(cadence)
    alignment = align(sorted(user_times), sorted(ref_times), gap_penalty=gap_penalty)
    return SpellComparison(
        spell=spell,
        cd_type=cd_type,
        cadence=cadence,
        presence=presence,
        alignment=alignment,
        reference_n=reference_n,
    )


def compare_all_spells(
    player_log: PlayerLog,
    profile: Mapping[int, SpellProfile],
    eligible_spell_ids: Sequence[int],
    *,
    catalog: SpellCatalog,
    reference_n: int,
) -> list[SpellComparison]:
    """T1.6: replaces bot.py's compare_all_spells — one SpellComparison per
    eligible spell, in the given order (discover_eligible_spell_ids' own
    descending-presence order). Zero-usage abilities are never skipped
    (see this module's docstring).
    """
    comparisons: list[SpellComparison] = []
    for spell_id in eligible_spell_ids:
        sp = profile[spell_id]
        user_times = sorted(player_log.cast_timeline.get(spell_id, ()))
        comparisons.append(
            compare_spell_usage(
                spell=catalog.get(spell_id),
                presence=sp.presence,
                user_times=user_times,
                ref_times=sp.ref_times,
                n_usages_median=sp.n_usages_median,
                reference_n=reference_n,
            )
        )
    return comparisons
