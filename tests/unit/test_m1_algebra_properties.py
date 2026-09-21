"""Independent M1 ledger oracle: durations, support, zero and missing IDs."""

import math
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_m1_acceptance import _log

from botgitgud.analysis.dps_gap import analyze_dps_gap
from botgitgud.analysis.measurement import compare_damage
from botgitgud.domain.models import AbilityDamage, EventMix, PlayerLog
from botgitgud.domain.spells import SpellCatalog


def _case(values: tuple[int, int, int, int], identity: int) -> PlayerLog:
    duration, first, second, support_seed = values
    support = (first + second) * support_seed / 100
    log = _log(first + second, duration=duration, support=support, fight_id=identity)
    return replace(
        log,
        damage_by_ability={
            sid: AbilityDamage(sid, damage, 0, 0)
            for sid, damage in ((1, first), (999999, second))
            if damage
        },
        cast_timeline={},
    )


_VALUES = st.tuples(
    st.integers(1, 900), st.integers(0, 10**8), st.integers(0, 10**8), st.integers(0, 100)
)


@settings(max_examples=100, deadline=None, derandomize=True)
@given(st.lists(_VALUES, min_size=9, max_size=18))
def test_a05_a06_a16_a17_independent_ledger_before_and_after_gate(
    cases: list[tuple[int, int, int, int]],
) -> None:
    player, *refs = [_case(values, i + 1) for i, values in enumerate(cases)]

    def net(log: PlayerLog) -> float:
        return (
            math.fsum(a.total for a in log.damage_by_ability.values())
            - log.support_subtracted_damage
        ) / log.fight.duration_s

    expected = net(player) - math.fsum(net(r) for r in refs) / len(refs)
    comparison = compare_damage(player, tuple(refs))
    tolerance = max(
        1e-9,
        1e-12
        * math.fsum(abs(v) for v in (net(player), *(net(r) / len(refs) for r in refs), expected)),
    )
    assert comparison.total_delta_dps == pytest.approx(expected, rel=0, abs=tolerance)
    assert comparison.residual_dps == pytest.approx(0, rel=0, abs=tolerance)
    assert comparison == compare_damage(player, tuple(reversed(refs)))
    with TemporaryDirectory() as directory:
        catalog = SpellCatalog(Path(directory) / "spells.json", blizzard=None)
        catalog.learn(1, "Example", "wcl")  # second ID deliberately unresolved
        report = analyze_dps_gap(
            player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
        )
    assert report.support_delta_dps is not None
    displayed = math.fsum(a.delta_ability_dps for a in report.abilities)
    assert displayed + report.other_delta_dps + report.support_delta_dps == pytest.approx(
        expected, rel=0, abs=tolerance
    )
    for ability in report.abilities:
        assert (
            report.metric_comparisons[f"damage_per_event:{ability.spell.spell_id}"].player.value
            is None
        )
        assert ability.split_pair_count == 0
        assert ability.unclassified_dps == pytest.approx(ability.delta_ability_dps)
        assert not ability.review_eligible
    if net(player) == 0:
        assert comparison.total_delta_player_pp is None


def test_a10_other_damage_cannot_change_own_dps_casts_or_uptime(tmp_path: Path) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(1, "Example", "wcl")
    catalog.learn(2, "Other", "wcl")

    def augment(log: PlayerLog, other: int) -> PlayerLog:
        assert log.measurement_provenance is not None
        return replace(
            log,
            uptimes={1: 0.5},
            damage_by_ability={**log.damage_by_ability, 2: AbilityDamage(2, other, 0, 0)},
            measurement_provenance=replace(
                log.measurement_provenance, damage_table_total=100 + other
            ),
        )

    refs = [augment(_log(100, fight_id=i + 2), 100) for i in range(15)]
    first = analyze_dps_gap(
        augment(_log(100), 100), refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    second = analyze_dps_gap(
        augment(_log(100), 200), refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    for metric in ("gross_ability_dps", "player_casts_per_minute", "aura_uptime_fraction"):
        assert first.metric_comparisons[f"{metric}:1"] == second.metric_comparisons[f"{metric}:1"]
    assert first.metric_comparisons["gross_damage_share_pct:1"].player.value == 50
    assert second.metric_comparisons["gross_damage_share_pct:1"].player.value == pytest.approx(
        100 / 3
    )


def _assert_equation(actual: float, terms: list[Fraction]) -> None:
    """Independent rational oracle, with exactly §5's tolerance (no pytest default)."""
    expected = sum(terms, Fraction())
    residual = abs(Fraction(actual) - expected)
    scale = max(Fraction(1), abs(Fraction(actual)) + sum(map(abs, terms), Fraction()))
    assert residual <= max(Fraction(1, 10**9), scale / 10**12)


def assert_split_ledger(player: PlayerLog, refs: list[PlayerLog], catalog: SpellCatalog) -> dict:
    """Oracle never calls accounting, production split, or production closure helpers."""
    report = analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    assert report.quantitative_damage_available and report.comparison is not None
    assert report.total_delta_dps is not None and report.support_delta_dps is not None
    n = len(refs)

    def damage(item: PlayerLog, sid: int) -> Fraction:
        row = item.damage_by_ability.get(sid)
        return Fraction(row.total if row else 0) / Fraction(item.fight.duration_s)

    def support(item: PlayerLog) -> Fraction:
        return Fraction(item.support_subtracted_damage) / Fraction(item.fight.duration_s)

    ids = set(player.damage_by_ability).union(*(set(r.damage_by_ability) for r in refs))
    deltas = {sid: [(damage(player, sid) - damage(r, sid)) / n for r in refs] for sid in ids}
    support_terms = [(support(r) - support(player)) / n for r in refs]
    net_terms = [
        sum((damage(player, sid) - damage(r, sid) for sid in ids), Fraction()) / n
        + (support(r) - support(player)) / n
        for r in refs
    ]
    _assert_equation(report.total_delta_dps, net_terms)
    _assert_equation(report.support_delta_dps, support_terms)
    for sid in ids:
        _assert_equation(report.comparison.ability_delta_dps[sid], deltas[sid])
    shown = {a.spell.spell_id for a in report.abilities}
    _assert_equation(report.other_delta_dps, [v for sid in ids - shown for v in deltas[sid]])
    _assert_equation(
        math.fsum(
            [a.delta_ability_dps for a in report.abilities]
            + [report.other_delta_dps, report.support_delta_dps]
        ),
        net_terms,
    )
    split_count = 0
    for ability in report.abilities:
        sid = ability.spell.spell_id
        expected: list[list[Fraction]] = [[], [], [], []]
        valid_pairs = 0
        for ref in refs:
            pu, pr = player.measurement_provenance, ref.measurement_provenance
            assert pu is not None and pr is not None
            au, ar = player.damage_by_ability.get(sid), ref.damage_by_ability.get(sid)
            bu, br = (
                pu.damage_event_mix_by_spell.get(sid, {}),
                pr.damage_event_mix_by_spell.get(sid, {}),
            )
            keys_u = {key for key, value in bu.items() if value.count > 0}
            keys_r = {key for key, value in br.items() if value.count > 0}
            compatible = (
                au is not None
                and ar is not None
                and au.hits > 0
                and ar.hits > 0
                and len(keys_u) == 1
                and keys_u == keys_r
                and keys_u <= {"PLAYER:FALSE", "PLAYER:TRUE", "PET:FALSE", "PET:TRUE"}
            )
            if not compatible:
                expected[3].append((damage(player, sid) - damage(ref, sid)) / n)
                continue
            assert au is not None and ar is not None
            qu, qr = (
                Fraction(au.hits) / Fraction(player.fight.duration_s),
                Fraction(ar.hits) / Fraction(ref.fight.duration_s),
            )
            puv, prv = Fraction(au.total) / au.hits, Fraction(ar.total) / ar.hits
            expected[0].append((qu - qr) * prv / n)
            expected[1].append((puv - prv) * qr / n)
            expected[2].append((qu - qr) * (puv - prv) / n)
            valid_pairs += 1
        assert ability.split_pair_count == valid_pairs
        split_count += valid_pairs
        for actual, terms in zip(
            (
                ability.volume_dps,
                ability.per_event_dps,
                ability.interaction_dps,
                ability.unclassified_dps,
            ),
            expected,
            strict=True,
        ):
            _assert_equation(actual, terms)
        _assert_equation(
            math.fsum(
                (
                    ability.volume_dps,
                    ability.per_event_dps,
                    ability.interaction_dps,
                    ability.unclassified_dps,
                )
            ),
            deltas[sid],
        )
    return {"split_pairs": split_count, "shown": len(shown), "omitted": len(ids - shown)}


def _split_case(values: tuple[int, int, int, int], identity: int) -> PlayerLog:
    item = _case(values, identity)
    assert item.measurement_provenance is not None
    rows = {
        sid: replace(row, hits=1 + (identity * 7 + sid) % 31)
        for sid, row in item.damage_by_ability.items()
    }
    bucket = "PLAYER:FALSE" if identity % 3 != 0 else "PLAYER:UNKNOWN"
    return replace(
        item,
        damage_by_ability=rows,
        measurement_provenance=replace(
            item.measurement_provenance,
            damage_event_mix_by_spell={
                sid: {bucket: EventMix(row.hits, row.total)} for sid, row in rows.items()
            },
        ),
    )


@settings(max_examples=70, deadline=None, derandomize=True)
@given(st.lists(_VALUES, min_size=9, max_size=18))
def test_u11_independent_split_weights_and_normative_tolerance(
    cases: list[tuple[int, int, int, int]],
) -> None:
    player, *refs = [_split_case(values, i + 1) for i, values in enumerate(cases)]
    with TemporaryDirectory() as directory:
        catalog = SpellCatalog(Path(directory) / "spells.json", blizzard=None)
        catalog.learn(1, "Example", "wcl")
        assert_split_ledger(player, refs, catalog)


def test_u11_oracle_exercises_split_unclassified_support_and_gate(tmp_path: Path) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(1, "Example", "wcl")
    player = _split_case((300, 25000, 200, 10), 1)
    refs = [_split_case((200 + i, 100000, 500, i), i + 2) for i in range(15)]
    assert assert_split_ledger(player, refs, catalog) == {
        "split_pairs": 10,
        "shown": 1,
        "omitted": 1,
    }
