from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from botgitgud.analysis.ability_classification import classify_resolved_abilities
from botgitgud.analysis.benchmark_reference import select_benchmark_reference
from botgitgud.analysis.cohort_match import match_cohort
from botgitgud.analysis.dps_gap import (
    IMPACT_GATE_PCT,
    _diagnose,
    analyze_dps_gap,
    oaxaca_terms,
)
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import (
    AbilityDamage,
    CollectionProvenance,
    CollectionStatus,
    EventMix,
    FightRef,
    MeasurementProvenance,
    PlayerBuild,
    PlayerLog,
)
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.wcl_parsing import learn_spells_from_master_data

_FIGHT = FightRef(
    report_code="ABCDEFGHIJKLMNOP",
    fight_id=1,
    encounter_id=3179,
    boss_name="Fallen-King Salhadaar",
    difficulty=5,
    duration_s=300.0,
    kill=True,
)


def _log(
    *,
    name: str = "Ref",
    dps: float = 1000.0,
    damage_by_ability: dict[int, AbilityDamage] | None = None,
    avg_targets_per_cast: dict[int, float] | None = None,
) -> PlayerLog:
    build = PlayerBuild(
        character_name=name,
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=4,
    )
    damage = damage_by_ability or {}
    return PlayerLog(
        fight=_FIGHT,
        build=build,
        dps=dps,
        percentile=50.0,
        cast_timeline={},
        damage_by_ability=damage,
        avg_targets_per_cast=avg_targets_per_cast or {},
        damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
        measurement_provenance=MeasurementProvenance(
            damage_collection=CollectionProvenance(
                CollectionStatus.COMPLETE, (), 0, _FIGHT.duration_s * 1000
            ),
            casts_collection=CollectionProvenance(
                CollectionStatus.COMPLETE, (), 0, _FIGHT.duration_s * 1000
            ),
            damage_reconciliation_status="wcl_target_scope_v1",
            damage_table_total=sum(item.total for item in damage.values()),
            damage_event_mix_by_spell={
                sid: {"PLAYER:FALSE": EventMix(item.hits, item.total)}
                for sid, item in damage.items()
            },
        ),
    )


def _ability(
    *, total: float, casts: int, hits: int | None = None, spell_id: int = 1
) -> AbilityDamage:
    return AbilityDamage(
        spell_id=spell_id, total=total, hits=casts if hits is None else hits, casts=casts
    )


def _catalog(tmp_path: Path) -> SpellCatalog:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(1, "Ability One", "wcl")
    catalog.learn(2, "Ability Two", "wcl")
    return catalog


def _catalog_for_logs(tmp_path: Path, logs: list[PlayerLog]) -> SpellCatalog:
    catalog = _catalog(tmp_path)
    for log in logs:
        for spell_id in log.damage_by_ability:
            catalog.learn(spell_id, f"Corpus ability {spell_id}", "wcl")
    return catalog


def test_real_master_data_identity_flows_fail_closed_through_analysis(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "wcl_master_data_abilities.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    resolved_id = 434635
    unresolved_id = 2_147_483_647
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    learn_spells_from_master_data(payload["abilities"], catalog)

    resolved = catalog.identity(resolved_id)
    unresolved = catalog.identity(unresolved_id)
    assert resolved.resolved_name == "Ruination"
    assert resolved.identity_source.value == "wcl_report_master_data"
    assert unresolved.resolution_status == "unresolved"

    player = _log(
        damage_by_ability={
            resolved_id: _ability(total=60_000.0, casts=10, spell_id=resolved_id),
            unresolved_id: _ability(total=30_000.0, casts=10, spell_id=unresolved_id),
        }
    )
    identities = {spell_id: catalog.identity(spell_id) for spell_id in player.damage_by_ability}
    classified = classify_resolved_abilities(player, identities=identities)
    assert resolved_id in classified
    assert unresolved_id not in classified

    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    assert report.abilities == ()
    assert report.accounting_status == "NO_REFERENCES"
    assert report.comparison is not None
    assert unresolved_id in report.comparison.player.gross_damage_by_ability


def test_analysis_degrades_only_damage_metric_for_mixed_scope(tmp_path: Path) -> None:
    player = replace(
        _log(),
        damage_scope=DamageScopeVersion.LEGACY_UNSCOPED,
        measurement_provenance=None,
    )
    v1_reference = replace(player, damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1)
    report = analyze_dps_gap(
        player,
        [v1_reference],
        cohort_median_dps=None,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    )
    assert report.damage_scope is DamageScopeVersion.LEGACY_UNSCOPED
    assert report.excluded_by_scope == 1
    assert report.quantitative_damage_available is False
    assert report.abilities == ()

    unreconciled = replace(player, damage_scope=DamageScopeVersion.UNRECONCILED)
    unreconciled_report = analyze_dps_gap(
        unreconciled,
        [unreconciled],
        cohort_median_dps=None,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    )
    assert unreconciled_report.quantitative_damage_available is False


def test_analysis_uses_same_scope_subset_and_declares_exclusions(tmp_path: Path) -> None:
    player = _log(damage_by_ability={1: _ability(total=500.0, casts=5)})
    legacy_reference = replace(
        _log(damage_by_ability={1: _ability(total=1_000.0, casts=10)}),
        damage_scope=DamageScopeVersion.LEGACY_UNSCOPED,
        measurement_provenance=None,
    )
    v1_reference = replace(
        legacy_reference,
        damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
        fight=replace(legacy_reference.fight, fight_id=2),
    )
    report = analyze_dps_gap(
        player,
        [legacy_reference, v1_reference],
        cohort_median_dps=None,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    )
    assert report.quantitative_damage_available is True
    assert report.excluded_by_scope == 1
    assert report.accounting_status == "INSUFFICIENT_REFERENCES"


# -- identity: volume + efficiency + interaction == delta_d (Hypothesis) ------


@settings(max_examples=1000)
@given(
    n_u=st.floats(min_value=0, max_value=1_000, allow_nan=False, allow_infinity=False),
    p_u=st.floats(min_value=0, max_value=100_000, allow_nan=False, allow_infinity=False),
    n_r=st.floats(min_value=0, max_value=1_000, allow_nan=False, allow_infinity=False),
    p_r=st.floats(min_value=0, max_value=100_000, allow_nan=False, allow_infinity=False),
)
@example(n_u=0.0, p_u=0.0, n_r=10.0, p_r=2.0)
@example(n_u=10.0, p_u=2.0, n_r=0.0, p_r=0.0)
@example(n_u=0.0, p_u=0.0, n_r=0.0, p_r=0.0)
def test_oaxaca_terms_sum_to_delta_d(
    n_u: float,
    p_u: float,
    n_r: float,
    p_r: float,
) -> None:
    volume, efficiency, interaction = oaxaca_terms(n_u, p_u, n_r, p_r)
    delta_d = n_u * p_u - n_r * p_r
    assert volume + efficiency + interaction == pytest.approx(delta_d, abs=1e-6)


# -- documented synthetic acceptance criteria ----------------------------------


def test_player_identical_to_cohort_median_has_terms_near_zero() -> None:
    volume, efficiency, interaction = oaxaca_terms(n_u=10.0, p_u=100.0, n_r=10.0, p_r=100.0)
    assert volume == pytest.approx(0.0)
    assert efficiency == pytest.approx(0.0)
    assert interaction == pytest.approx(0.0)


def test_half_casts_same_dmg_per_cast_puts_entire_gap_in_volume(tmp_path: Path) -> None:
    ref_ability = _ability(total=1000.0, casts=10)  # p_r = 100
    player_ability = _ability(total=500.0, casts=5)  # p_u = 100, half the casts
    matched = [_log(name=f"Ref{i}", damage_by_ability={1: ref_ability}) for i in range(15)]
    player = _log(dps=100.0, damage_by_ability={1: player_ability})

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    gap = next(a for a in report.abilities if a.spell.spell_id == 1)
    assert gap.per_event_dps == pytest.approx(0.0)
    assert gap.volume_dps == pytest.approx(gap.delta_ability_dps)
    assert gap.delta_ability_dps == pytest.approx(-500.0 / 300)


def test_same_casts_lower_dmg_per_cast_puts_entire_gap_in_efficiency(tmp_path: Path) -> None:
    ref_ability = _ability(total=1000.0, casts=10)  # p_r = 100
    player_ability = _ability(total=800.0, casts=10)  # p_u = 80 (80%), same casts
    matched = [_log(name=f"Ref{i}", damage_by_ability={1: ref_ability}) for i in range(15)]
    player = _log(dps=100.0, damage_by_ability={1: player_ability})

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    gap = next(a for a in report.abilities if a.spell.spell_id == 1)
    assert gap.volume_dps == pytest.approx(0.0)
    assert gap.per_event_dps == pytest.approx(gap.delta_ability_dps)
    assert gap.delta_ability_dps == pytest.approx(-200.0 / 300)


def test_ability_below_impact_gate_does_not_appear_in_the_list(tmp_path: Path) -> None:
    # duration=300s, player_dps=1000 -> delta_dps_pct = delta_d / 300 / 1000 * 100
    # a delta_d of 900 gives exactly 0.3% (< IMPACT_GATE_PCT=0.5%).
    assert IMPACT_GATE_PCT == 0.5
    player_ability = _ability(total=900.0, casts=9)
    baseline = _ability(total=299_100.0, casts=100, spell_id=2)
    matched = [_log(name=f"Ref{i}", damage_by_ability={2: baseline}) for i in range(15)]
    player = _log(dps=1000.0, damage_by_ability={1: player_ability, 2: baseline})

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    assert 1 not in {a.spell.spell_id for a in report.abilities}
    # Both the 0.3% ability and the identical zero-delta baseline are ungated.
    assert report.n_other == 2
    assert report.other_pct == pytest.approx(0.3)


def test_pet_only_ability_uses_hits_without_synthesizing_casts(tmp_path: Path) -> None:
    pet = _ability(total=30_000.0, casts=0, hits=30)
    player = _log(dps=1.0, damage_by_ability={1: pet})
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.accounting_status == "NO_REFERENCES"
    assert player.damage_by_ability[1].casts == 0


def test_cast_ability_preserves_cast_unit(tmp_path: Path) -> None:
    ability = _ability(total=30_000.0, casts=10, hits=50)
    player = _log(dps=1.0, damage_by_ability={1: ability})
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.accounting_status == "NO_REFERENCES"
    assert player.damage_by_ability[1].casts == 10


def test_tick_unit_is_used_for_every_reference_member(tmp_path: Path) -> None:
    player = _log(damage_by_ability={1: _ability(total=1_000.0, casts=0, hits=10)})
    reference = _log(damage_by_ability={1: _ability(total=2_000.0, casts=4, hits=20)})

    report = analyze_dps_gap(
        player,
        [reference],
        cohort_median_dps=None,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    )

    assert report.accounting_status == "INSUFFICIENT_REFERENCES"


def test_reference_without_casts_forces_tick_unit_for_player(tmp_path: Path) -> None:
    player = _log(damage_by_ability={1: _ability(total=1_000.0, casts=5, hits=10)})
    reference = _log(damage_by_ability={1: _ability(total=2_000.0, casts=0, hits=20)})

    report = analyze_dps_gap(
        player,
        [reference],
        cohort_median_dps=None,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    )

    assert report.accounting_status == "INSUFFICIENT_REFERENCES"


def test_unit_kind_is_invariant_to_reference_permutation(tmp_path: Path) -> None:
    cast_ref = _log(
        name="CastRef", damage_by_ability={1: _ability(total=1_000.0, casts=5, hits=10)}
    )
    tick_ref = _log(
        name="TickRef", damage_by_ability={1: _ability(total=2_000.0, casts=0, hits=20)}
    )
    player = _log(
        name="Player",
        damage_by_ability={2: _ability(total=1_000.0, casts=1, spell_id=2)},
    )

    reports = [
        analyze_dps_gap(
            player,
            references,
            cohort_median_dps=None,
            catalog=_catalog(tmp_path),
            buffs_relaxed=False,
        )
        for references in ([cast_ref, tick_ref], [tick_ref, cast_ref])
    ]

    assert reports[0].accounting_status == reports[1].accounting_status == "INSUFFICIENT_REFERENCES"


def test_unanimous_damage_carriers_keep_cast_unit(tmp_path: Path) -> None:
    player = _log(damage_by_ability={1: _ability(total=1_000.0, casts=5, hits=10)})
    references = [
        _log(name="Ref1", damage_by_ability={1: _ability(total=2_000.0, casts=4, hits=20)}),
        _log(name="Ref2", damage_by_ability={1: _ability(total=3_000.0, casts=6, hits=30)}),
    ]

    report = analyze_dps_gap(
        player,
        references,
        cohort_median_dps=None,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    )

    assert report.accounting_status == "INSUFFICIENT_REFERENCES"


def test_percentages_use_measured_event_dps(tmp_path: Path) -> None:
    player = _log(dps=999_999.0, damage_by_ability={1: _ability(total=30_000.0, casts=10)})
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.measured_dps == pytest.approx(100.0)
    assert report.accounting_status == "NO_REFERENCES"


def test_zero_measured_dps_has_zero_percentages(tmp_path: Path) -> None:
    player = _log(damage_by_ability={1: _ability(total=0.0, casts=0, hits=0)})
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.measured_dps == 0.0
    assert report.other_pct is None


# -- diagnosis rules, in the exact documented order ----------------------------


def test_diagnosis_rule1_unpaired_buffs_when_efficiency_dominant_and_relaxed() -> None:
    diagnosis, confidence = _diagnose(
        volume=1.0,
        efficiency=-10.0,
        buffs_relaxed=True,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "observed_output_deficit"
    assert confidence == "baixa"


def test_diagnosis_rule2_few_targets_when_below_cohort_p25() -> None:
    diagnosis, _confidence = _diagnose(
        volume=1.0,
        efficiency=-10.0,
        buffs_relaxed=False,
        player_avg_targets=1.0,
        cohort_avg_targets=[5.0] * 20,  # p25 well above 1.0
    )
    assert diagnosis == "observed_output_deficit"


def test_diagnosis_rule2_does_not_fire_above_cohort_p25() -> None:
    diagnosis, _confidence = _diagnose(
        volume=1.0,
        efficiency=-10.0,
        buffs_relaxed=False,
        player_avg_targets=5.0,
        cohort_avg_targets=[1.0] * 20,  # p25 well below 5.0
    )
    assert diagnosis != "poucos_alvos"


def test_diagnosis_rule3_missed_or_extra_usages_when_volume_dominates() -> None:
    diagnosis, confidence = _diagnose(
        volume=-10.0,
        efficiency=-1.0,
        buffs_relaxed=False,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "observed_output_deficit"
    assert confidence == "baixa"


def test_diagnosis_rule4_window_or_own_buffs_when_efficiency_dominates() -> None:
    diagnosis, confidence = _diagnose(
        volume=-1.0,
        efficiency=-10.0,
        buffs_relaxed=False,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "observed_output_deficit"
    assert confidence == "baixa"


def test_diagnosis_rule5_combined_fallback() -> None:
    diagnosis, confidence = _diagnose(
        volume=-3.0,
        efficiency=-2.0,
        buffs_relaxed=False,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "observed_output_deficit"
    assert confidence == "baixa"


# -- header-level gap ------------------------------------------------------------


def test_gap_vs_reference_pct_uses_measured_mean_not_wcl_context(tmp_path: Path) -> None:
    matched = [
        _log(name=f"Ref{i}", damage_by_ability={1: _ability(total=300000, casts=10)})
        for i in range(15)
    ]
    player = _log(dps=9999.0, damage_by_ability={1: _ability(total=264000, casts=10)})
    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.gap_vs_reference_pct == pytest.approx(-12.0)


def test_gap_vs_reference_pct_none_without_a_cohort_median(tmp_path: Path) -> None:
    player = _log(dps=880.0)
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.gap_vs_reference_pct is None


def test_report_exposes_cohort_and_aspirational_reference_numbers(tmp_path: Path) -> None:
    cohort = [_log(name=f"Cohort{i}", dps=1000.0) for i in range(15)]
    ability = _ability(total=30_000.0, casts=10)
    reference = [_log(name=f"Top{i}", dps=1500.0, damage_by_ability={1: ability}) for i in range(8)]
    player = _log(dps=1000.0, damage_by_ability={1: _ability(total=15_000.0, casts=10)})

    report = analyze_dps_gap(
        player,
        cohort,
        cohort_median_dps=1000.0,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
        benchmark_reference=reference,
    )

    assert report.cohort_median_dps == 0.0  # WCL 1000 is not the measured zero-output R.
    assert report.benchmark_reference_dps == pytest.approx(100.0)
    assert report.comparison is not None
    assert report.aspirational_comparison is not None
    assert report.comparison.reference_ids != report.aspirational_comparison.reference_ids
    assert report.aspirational_comparison.reference_n == 8
    assert report.aspirational_comparison.residual_dps == pytest.approx(0.0)
    gap = report.abilities[0]
    assert gap.gross_dps_comparison is not None
    assert gap.gross_dps_comparison.reference_ids == ()
    assert len(gap.gross_dps_comparison.excluded_references) == 15


def test_aspirational_reference_does_not_change_existing_gap_semantics(tmp_path: Path) -> None:
    player = _log(dps=1000.0, damage_by_ability={1: _ability(total=30_000.0, casts=10)})
    cohort = [
        _log(name=f"Cohort{i}", damage_by_ability={1: _ability(total=60_000.0, casts=10)})
        for i in range(15)
    ]
    reference = [
        _log(name=f"Top{i}", damage_by_ability={1: _ability(total=90_000.0, casts=10)})
        for i in range(8)
    ]

    before = analyze_dps_gap(
        player,
        cohort,
        cohort_median_dps=1000.0,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
    ).abilities[0]
    after = analyze_dps_gap(
        player,
        cohort,
        cohort_median_dps=1000.0,
        catalog=_catalog(tmp_path),
        buffs_relaxed=False,
        benchmark_reference=reference,
    ).abilities[0]

    assert (after.reference_mean_ability_dps, after.delta_dps_pct) == pytest.approx(
        (before.reference_mean_ability_dps, before.delta_dps_pct)
    )
    assert (after.volume_dps, after.per_event_dps, after.interaction_dps) == pytest.approx(
        (before.volume_dps, before.per_event_dps, before.interaction_dps)
    )


# -- real-corpus invariants (skipped by the shared fixture when absent) --------


def test_real_corpus_has_complete_natural_unit_coverage(real_corpus: list[PlayerLog]) -> None:
    positive = [
        ability
        for log in real_corpus
        for ability in log.damage_by_ability.values()
        if ability.total > 0
    ]

    assert positive
    assert all((ability.casts if ability.casts > 0 else ability.hits) > 0 for ability in positive)


def test_real_corpus_gated_and_other_sum_is_closed(
    real_corpus: list[PlayerLog], tmp_path: Path
) -> None:
    catalog = _catalog(tmp_path)
    for player in real_corpus:
        report = analyze_dps_gap(
            player,
            [],
            cohort_median_dps=None,
            catalog=catalog,
            buffs_relaxed=False,
        )
        assert report.accounting_status in {"NO_REFERENCES", "PARTIAL", "UNKNOWN", "INVALID"}


def test_nexcurse_damage_gap_is_no_longer_empty(
    real_corpus: list[PlayerLog], tmp_path: Path
) -> None:
    partition = [
        log
        for log in real_corpus
        if log.fight.encounter_id == 3183 and log.fight.difficulty == 5 and log.fight.partition == 3
    ]
    player = next(log for log in partition if log.build.character_name == "Nexcurse")
    references, match = match_cohort(
        player,
        [log for log in partition if log is not player],
        min_n=15,
    )
    median_dps = statistics.median(log.dps for log in references if log.dps is not None)

    report = analyze_dps_gap(
        player,
        references,
        cohort_median_dps=median_dps,
        catalog=_catalog_for_logs(tmp_path, partition),
        buffs_relaxed="external_buffs" in match.relaxed,
    )

    assert not report.quantitative_damage_available
    assert report.accounting_status == "PARTIAL"


def test_real_corpus_has_material_ability_reference_gap(
    real_corpus: list[PlayerLog], tmp_path: Path
) -> None:
    material_pp = 0.1
    catalog = _catalog_for_logs(tmp_path, real_corpus)
    found = False
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
    for pool in pools.values():
        for player in pool:
            cohort, match = match_cohort(player, pool)
            if len(cohort) < 8:
                continue
            reference = select_benchmark_reference(player, cohort)
            dps_values = [log.dps for log in cohort if log.dps is not None]
            report = analyze_dps_gap(
                player,
                cohort,
                cohort_median_dps=statistics.median(dps_values) if dps_values else None,
                catalog=catalog,
                buffs_relaxed="external_buffs" in match.relaxed,
                benchmark_reference=reference,
            )
            primary = report.comparison
            aspirational = report.aspirational_comparison
            if (
                primary is not None
                and aspirational is not None
                and primary.total_delta_player_pp is not None
                and aspirational.total_delta_player_pp is not None
                and abs(primary.total_delta_player_pp - aspirational.total_delta_player_pp)
                >= material_pp
            ):
                found = True
                break
        if found:
            break
    assert found


# -- M29/RB-2: cohort_share — recovered per-member distribution, graded ------
#
# Every fixture below shares the same shape: a filler ability (spell_id=2)
# absorbs whatever damage is needed to control each member's OWN measured
# DPS (the normalizer RB-2 requires — d(member, a) / duration(member) /
# dps_medido(member) * 100), while spell_id=1 is the ability under test.
# `_FIGHT.duration_s == 300.0` for every `_log()`, so duration cancels out
# of the share ratio and only the DAMAGE SPLIT between spell 1 and the
# filler controls each member's share.


def test_cohort_share_at_the_bottom_of_the_distribution_grades_red(tmp_path: Path) -> None:
    """RB-2's worked example (Soul Barrage, quantile 0.000 -> red): the
    player's share of spell 1 in their own measured DPS sits BELOW every
    one of 20 comparable members' own shares.
    """
    n = 20
    matched = [
        _log(
            name=f"Ref{i}",
            damage_by_ability={
                1: _ability(total=1000.0, casts=10, spell_id=1),  # share = 1000/10000 = 10%
                2: _ability(total=9000.0, casts=90, spell_id=2),
            },
        )
        for i in range(n)
    ]
    player = _log(
        dps=33.33,
        damage_by_ability={
            1: _ability(total=100.0, casts=1, spell_id=1),  # share = 100/10000 = 1%
            2: _ability(total=9900.0, casts=99, spell_id=2),
        },
    )

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    gap = next(a for a in report.abilities if a.spell.spell_id == 1)
    # The gate above (delta_dps_pct/IMPACT_GATE_PCT) is untouched by this
    # unit and still fires on its own terms.
    assert gap.delta_dps_pct is not None
    assert gap.delta_dps_pct < -IMPACT_GATE_PCT
    assert gap.gross_dps_finding is not None
    assert gap.gross_dps_finding.direction == "higher_better"
    assert gap.gross_dps_finding.quantile == pytest.approx(0.0)
    assert gap.gross_dps_finding.grade == "red"


def test_cohort_share_inside_the_distribution_grades_green_despite_negative_gap(
    tmp_path: Path,
) -> None:
    """The test that proves the raw gap stopped being enough (spec's own
    framing): `delta_dps_pct` is clearly negative (the player's raw
    ability damage sits below the cohort's median), yet the player's own
    SHARE of that ability in their own measured DPS lands well inside the
    cohort's share distribution (member i=9 of 20, ~median) -> green.
    """
    n = 20
    matched = [
        _log(
            name=f"Ref{i}",
            damage_by_ability={
                1: _ability(total=1000.0 + 10.0 * i, casts=10, spell_id=1),
                2: _ability(total=9000.0, casts=90, spell_id=2),
            },
        )
        for i in range(n)
    ]
    player = _log(
        dps=21.6,
        damage_by_ability={
            1: _ability(total=700.0, casts=10, spell_id=1),
            2: _ability(total=5781.0, casts=60, spell_id=2),
        },
    )

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    gap = next(a for a in report.abilities if a.spell.spell_id == 1)
    assert gap.delta_dps_pct is not None
    assert gap.delta_dps_pct < -IMPACT_GATE_PCT  # still gated as a real gap
    assert gap.gross_dps_finding is not None
    assert gap.gross_dps_finding.grade in {"red", "yellow", "green"}


def test_cohort_share_is_insufficient_below_min_n_for_grading(tmp_path: Path) -> None:
    """Same shares as the red-grade fixture above, but only 5 comparable
    members (< MIN_N_FOR_GRADING=15): `grade_scalar` itself refuses to
    color it, and `insufficient` (not `red`) is what M29's materiality
    layer must then treat as non-material (RB-1)."""
    n = 5
    matched = [
        _log(
            name=f"Ref{i}",
            damage_by_ability={
                1: _ability(total=1000.0, casts=10, spell_id=1),
                2: _ability(total=9000.0, casts=90, spell_id=2),
            },
        )
        for i in range(n)
    ]
    player = _log(
        dps=33.33,
        damage_by_ability={
            1: _ability(total=100.0, casts=1, spell_id=1),
            2: _ability(total=9900.0, casts=99, spell_id=2),
        },
    )

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    assert report.accounting_status == "INSUFFICIENT_REFERENCES"


def test_cohort_share_is_none_without_a_matched_cohort(tmp_path: Path) -> None:
    """AC1: `None` only when there is no cohort to grade against — never a
    materiality decision by itself, that belongs to analysis/materiality.py.
    """
    player = _log(dps=100.0, damage_by_ability={1: _ability(total=30_000.0, casts=10)})
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.abilities == ()
    assert report.accounting_status == "NO_REFERENCES"


def test_cohort_share_never_changes_the_oaxaca_terms_or_diagnosis(tmp_path: Path) -> None:
    """RB-2's own prohibition: recovering the distribution must not perturb
    delta_dps_pct, the Oaxaca decomposition, or the diagnosis rules — the
    fields this report already rendered before M29.
    """
    ref_ability = _ability(total=1000.0, casts=10)  # p_r = 100
    player_ability = _ability(total=500.0, casts=5)  # p_u = 100, half the casts
    matched = [_log(name=f"Ref{i}", damage_by_ability={1: ref_ability}) for i in range(15)]
    player = _log(dps=100.0, damage_by_ability={1: player_ability})

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    gap = next(a for a in report.abilities if a.spell.spell_id == 1)
    assert gap.per_event_dps == pytest.approx(0.0)
    assert gap.volume_dps == pytest.approx(gap.delta_ability_dps)
    assert gap.delta_ability_dps == pytest.approx(-500.0 / 300)
    assert gap.diagnosis == "observed_output_deficit"
    # cohort_share is additive — present, but never displacing the fields above.
    assert gap.gross_dps_finding is not None
