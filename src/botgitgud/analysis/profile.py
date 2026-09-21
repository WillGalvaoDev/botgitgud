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

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace

from botgitgud.analysis.cadence import compute_cadence, is_eligible
from botgitgud.analysis.cohort import (
    usage_count_at_duration,
    usage_rate_per_minute,
    within_positional_band,
)
from botgitgud.analysis.measurement import damage_reference_id
from botgitgud.domain.blacklist import MAJOR_CD_BLACKLIST
from botgitgud.domain.cooldowns import get_base_cooldown
from botgitgud.domain.measurement_validation import collection_interval_problem, valid_player_casts
from botgitgud.domain.models import CollectionStatus, PhaseKey, PlayerLog, SpellProfile


def _sorted_slot_distributions(
    raw_slots: Sequence[Sequence[float]],
) -> tuple[list[float], tuple[tuple[float, ...], ...]]:
    """ref_times must be ascending for alignment.align() (§0.5's own
    _require_sorted) — slot_ref_times is re-paired through the same sort
    so slot_ref_times[i] stays the raw distribution behind ref_times[i]
    (T2.3: grading/bootstrap CI need that raw data).
    """
    by_median = sorted(
        ((statistics.median(times), tuple(times)) for times in raw_slots if times),
        key=lambda pair: pair[0],
    )
    medians = [median for median, _times in by_median]
    distributions = tuple(times for _median, times in by_median)
    return medians, distributions


def build_cd_reference_profile(
    reference_logs: Sequence[PlayerLog], target_duration_s: float
) -> tuple[Mapping[int, SpellProfile], int]:
    """Returns (profile, n_positional) — n_positional is reported to the
    user when small (< POSITIONAL_MIN_N, see cohort.py).
    """
    by_identity: dict[str, PlayerLog] = {}
    conflicts: set[str] = set()
    for log in reference_logs:
        identity = damage_reference_id(log)
        if identity in by_identity and by_identity[identity] != log:
            conflicts.add(identity)
        by_identity[identity] = log
    reference_logs = tuple(
        log
        for log in reference_logs
        if log.measurement_provenance is not None
        and log.measurement_provenance.casts_collection.status is CollectionStatus.COMPLETE
        and collection_interval_problem(
            log.measurement_provenance.casts_collection, log.fight.duration_s
        )
        is None
        and math.isfinite(log.fight.duration_s)
        and log.fight.duration_s > 0
        and damage_reference_id(log) not in conflicts
    )
    cleaned = []
    for log in reference_logs:
        valid = {
            sid: times for sid, times in log.cast_timeline.items() if valid_player_casts(log, sid)
        }
        cleaned.append(
            replace(
                log,
                cast_timeline=valid,
                phase_cast_timeline={
                    sid: times for sid, times in log.phase_cast_timeline.items() if sid in valid
                },
            )
        )
    reference_logs = tuple(cleaned)
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
    # T2.4: spell_id -> phase_key -> slot_idx (within THAT key's own
    # sequence) -> times — never merged across phase_id/occurrence.
    phase_slot_timings: dict[int, dict[PhaseKey, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for ref in reference_logs:
        for spell_id in ref.cast_timeline:
            presence_count[spell_id] += 1

    for ref in positional_logs:
        for spell_id, times in ref.cast_timeline.items():
            for idx, t in enumerate(times):
                slot_timings[spell_id][idx].append(t)
        for spell_id, by_key in ref.phase_cast_timeline.items():
            for key, times in by_key.items():
                for idx, t in enumerate(times):
                    phase_slot_timings[spell_id][key][idx].append(t)

    rates_by_spell: dict[int, list[float]] = defaultdict(list)
    for ref in positional_logs:
        for spell_id, times in ref.cast_timeline.items():
            rates_by_spell[spell_id].append(usage_rate_per_minute(len(times), ref.fight.duration_s))

    profile: dict[int, SpellProfile] = {}
    for spell_id in presence_count:
        presence = presence_count[spell_id] / num_logs

        raw_slots = [times for _slot, times in sorted(slot_timings.get(spell_id, {}).items())]
        slot_medians, slot_ref_times = _sorted_slot_distributions(raw_slots)

        phase_ref_times: dict[PhaseKey, tuple[float, ...]] = {}
        phase_slot_ref_times: dict[PhaseKey, tuple[tuple[float, ...], ...]] = {}
        for key, by_slot in phase_slot_timings.get(spell_id, {}).items():
            key_raw_slots = [times for _slot, times in sorted(by_slot.items())]
            key_medians, key_distributions = _sorted_slot_distributions(key_raw_slots)
            phase_ref_times[key] = tuple(key_medians)
            phase_slot_ref_times[key] = key_distributions

        if positional_logs:
            rates = rates_by_spell.get(spell_id, [])
            if not rates:
                continue
            n_usages_median = usage_count_at_duration(statistics.median(rates), target_duration_s)
        else:
            n_usages_median = 0.0

        profile[spell_id] = SpellProfile(
            spell_id=spell_id,
            presence=presence,
            ref_times=tuple(slot_medians),
            n_usages_median=n_usages_median,
            slot_ref_times=slot_ref_times,
            phase_ref_times=phase_ref_times,
            phase_slot_ref_times=phase_slot_ref_times,
            # EC.1: subgrupo ABSOLUTO que realmente lançou este spell —
            # `presence_count[spell_id]` já É essa contagem (o numerador
            # de `presence`), só exposta separadamente para `is_eligible`.
            n_with_spell=presence_count[spell_id],
            n_positional_with_spell=len(rates_by_spell.get(spell_id, [])),
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
        cadence = compute_cadence(
            sp.ref_times,
            n_usages_median=sp.n_usages_median,
            base_cooldown=get_base_cooldown(spell_id),
        )
        if is_eligible(
            spell_id,
            sp.presence,
            cadence,
            blacklist=MAJOR_CD_BLACKLIST,
            n_with_spell=sp.n_with_spell,
        ):
            eligible.append((spell_id, sp.presence))

    eligible.sort(key=lambda x: x[1], reverse=True)
    return [spell_id for spell_id, _ in eligible]
