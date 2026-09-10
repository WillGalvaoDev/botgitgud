from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import pytest

from botgitgud.analysis.benchmark_reference import select_benchmark_reference
from botgitgud.analysis.cohort_match import match_cohort
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog


def _log(name: str, dps: float) -> PlayerLog:
    return PlayerLog(
        fight=FightRef(
            report_code=f"REPORT-{name}",
            fight_id=1,
            encounter_id=1,
            boss_name="Boss",
            difficulty=5,
            duration_s=300.0,
            kill=True,
        ),
        build=PlayerBuild(
            character_name=name,
            server="Realm",
            class_name="Warlock",
            spec_name="Demonology",
            role="dps",
            item_level=280.0,
            talent_hash=None,
            tier_pieces=4,
        ),
        dps=dps,
        percentile=50.0,
        cast_timeline={},
    )


def test_reference_is_non_empty_subset_and_fallback_selects_top_third() -> None:
    cohort = [_log(f"Ref{i:02d}", float(i)) for i in range(30)]
    player = _log("Player", 29.0)

    reference = select_benchmark_reference(player, cohort)

    assert len(reference) == 10
    assert {id(member) for member in reference} <= {id(member) for member in cohort}
    assert [member.dps for member in reference] == list(map(float, range(20, 30)))


def test_reference_is_deterministic_under_permutation_and_dps_ties() -> None:
    cohort = [_log(f"Ref{i:02d}", 100.0 if i >= 8 else float(i)) for i in range(12)]
    # Exercise the canonical log tie-break as well as input-order independence.
    cohort[9] = replace(cohort[9], percentile=99.0)
    player = _log("Player", 5.0)

    forward = select_benchmark_reference(player, cohort)
    reverse = select_benchmark_reference(player, list(reversed(cohort)))

    assert forward == reverse
    assert forward


def test_empty_cohort_is_outside_the_eligible_domain() -> None:
    with pytest.raises(ValueError):
        select_benchmark_reference(_log("Player", 1.0), [])


def test_reference_invariant_for_every_eligible_real_cohort(
    real_corpus: list[PlayerLog],
) -> None:
    pools: dict[tuple[int, int, int | None, str, str], list[PlayerLog]] = defaultdict(list)
    for log in real_corpus:
        pools[
            (
                log.fight.encounter_id,
                log.fight.difficulty,
                log.fight.partition,
                log.build.class_name,
                log.build.spec_name,
            )
        ].append(log)

    checked = 0
    for pool in pools.values():
        for player in pool:
            cohort, _report = match_cohort(player, pool)
            if len(cohort) < 8:
                continue
            reference = select_benchmark_reference(player, cohort)
            assert reference
            assert {id(member) for member in reference} <= {id(member) for member in cohort}
            checked += 1
    assert checked > 0
