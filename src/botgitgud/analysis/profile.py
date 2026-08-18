"""T1.6 — reference-cohort statistical profile, replacing bot.py's
build_cd_reference_profile/discover_eligible_spell_ids. Same math as
Fase 0 (T0.7/T0.8), adapted to the T1.2 domain models: input is
Sequence[PlayerLog] instead of a list of raw dicts, output is
Mapping[int, SpellProfile] (T1.2/T1.3's own profile type) instead of a
bare dict.

T0.8's normalization rule (unchanged): `presence` is quasi-invariant to
duration -> uses the whole sanity-band pool; `ref_times`/`n_usages_median`
scale with duration -> restricted to the positional-band subset, with
usage counts normalized to a rate/minute and rescaled back to
target_duration_s before being handed to cadence.py (calibrated for
absolute counts, not rates).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence

from botgitgud.analysis.cadence import compute_cadence, is_eligible
from botgitgud.analysis.cohort import (
    usage_count_at_duration,
    usage_rate_per_minute,
    within_positional_band,
)
from botgitgud.domain.blacklist import MAJOR_CD_BLACKLIST
from botgitgud.domain.models import PlayerLog, SpellProfile


def build_cd_reference_profile(
    reference_logs: Sequence[PlayerLog], target_duration_s: float
) -> tuple[Mapping[int, SpellProfile], int]:
    """Returns (profile, n_positional) — n_positional is reported to the
    user when small (< POSITIONAL_MIN_N, see cohort.py).
    """
    num_logs = len(reference_logs)
    if num_logs == 0:
        return {}, 0

    positional_logs = [
        ref
        for ref in reference_logs
        if within_positional_band(ref.fight.duration_s, target_duration_s)
    ]
    num_positional = len(positional_logs)

    presence_count: dict[int, int] = defaultdict(int)
    slot_timings: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    for ref in reference_logs:
        for spell_id in ref.cast_timeline:
            presence_count[spell_id] += 1

    for ref in positional_logs:
        for spell_id, times in ref.cast_timeline.items():
            for idx, t in enumerate(times):
                slot_timings[spell_id][idx].append(t)

    rates_by_spell: dict[int, list[float]] = defaultdict(list)
    for ref in positional_logs:
        for spell_id, times in ref.cast_timeline.items():
            rates_by_spell[spell_id].append(usage_rate_per_minute(len(times), ref.fight.duration_s))

    profile: dict[int, SpellProfile] = {}
    for spell_id in presence_count:
        presence = presence_count[spell_id] / num_logs

        raw_slots = [times for _slot, times in sorted(slot_timings.get(spell_id, {}).items())]
        # ref_times must be ascending for alignment.align() (§0.5's own
        # _require_sorted) — slot_ref_times is re-paired through the same
        # sort so slot_ref_times[i] stays the raw distribution behind
        # ref_times[i] (T2.3: grading/bootstrap CI need that raw data).
        by_median = sorted(
            ((statistics.median(times), tuple(times)) for times in raw_slots if times),
            key=lambda pair: pair[0],
        )
        slot_medians = [median for median, _times in by_median]
        slot_ref_times = tuple(times for _median, times in by_median)

        if positional_logs:
            # Spells the positional subset never cast still contribute an
            # explicit rate of 0.0 — otherwise the median would silently
            # skip them instead of reflecting "basically never used here".
            rates = rates_by_spell.get(spell_id, []) + [0.0] * (
                num_positional - len(rates_by_spell.get(spell_id, []))
            )
            n_usages_median = usage_count_at_duration(statistics.median(rates), target_duration_s)
        else:
            n_usages_median = 0.0

        profile[spell_id] = SpellProfile(
            spell_id=spell_id,
            presence=presence,
            ref_times=tuple(slot_medians),
            n_usages_median=n_usages_median,
            slot_ref_times=slot_ref_times,
        )

    return profile, num_positional


def discover_eligible_spell_ids(profile: Mapping[int, SpellProfile]) -> list[int]:
    """Deterministic order by descending presence — profile itself must
    already be built from a deterministically-ordered source (see
    ingest/log_fetcher.py's fetch_many, which returns results in `refs`
    order rather than thread-completion order) for ties to be stable.
    """
    eligible: list[tuple[int, float]] = []
    for spell_id, sp in sorted(profile.items()):
        cadence = compute_cadence(sp.ref_times, n_usages_median=sp.n_usages_median)
        if is_eligible(spell_id, sp.presence, cadence, blacklist=MAJOR_CD_BLACKLIST):
            eligible.append((spell_id, sp.presence))

    eligible.sort(key=lambda x: x[1], reverse=True)
    return [spell_id for spell_id, _ in eligible]
