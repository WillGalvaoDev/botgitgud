from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from botgitgud.analysis.dps_gap import (
    IMPACT_GATE_PCT,
    _diagnose,
    analyze_dps_gap,
    oaxaca_terms,
)
from botgitgud.domain.models import AbilityDamage, FightRef, PlayerBuild, PlayerLog
from botgitgud.domain.spells import SpellCatalog

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
    return PlayerLog(
        fight=_FIGHT,
        build=build,
        dps=dps,
        percentile=50.0,
        cast_timeline={},
        damage_by_ability=damage_by_ability or {},
        avg_targets_per_cast=avg_targets_per_cast or {},
    )


def _ability(*, total: float, casts: int, spell_id: int = 1) -> AbilityDamage:
    return AbilityDamage(spell_id=spell_id, total=total, hits=casts, casts=casts)


def _catalog(tmp_path: Path) -> SpellCatalog:
    return SpellCatalog(tmp_path / "spells.json", blizzard=None)


# -- identity: volume + efficiency + interaction == delta_d (Hypothesis) ------


@settings(max_examples=1000)
@given(
    c_u=st.floats(min_value=0, max_value=1_000, allow_nan=False, allow_infinity=False),
    p_u=st.floats(min_value=0, max_value=100_000, allow_nan=False, allow_infinity=False),
    c_r=st.floats(min_value=0, max_value=1_000, allow_nan=False, allow_infinity=False),
    p_r=st.floats(min_value=0, max_value=100_000, allow_nan=False, allow_infinity=False),
)
def test_oaxaca_terms_sum_to_delta_d(c_u: float, p_u: float, c_r: float, p_r: float) -> None:
    volume, efficiency, interaction = oaxaca_terms(c_u, p_u, c_r, p_r)
    delta_d = c_u * p_u - c_r * p_r
    assert volume + efficiency + interaction == pytest.approx(delta_d, abs=1e-6)


# -- documented synthetic acceptance criteria ----------------------------------


def test_player_identical_to_cohort_median_has_terms_near_zero() -> None:
    volume, efficiency, interaction = oaxaca_terms(c_u=10.0, p_u=100.0, c_r=10.0, p_r=100.0)
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
    assert gap.efficiency == pytest.approx(0.0)
    assert gap.volume == pytest.approx(gap.delta_d)
    assert gap.delta_d == pytest.approx(-500.0)


def test_same_casts_lower_dmg_per_cast_puts_entire_gap_in_efficiency(tmp_path: Path) -> None:
    ref_ability = _ability(total=1000.0, casts=10)  # p_r = 100
    player_ability = _ability(total=800.0, casts=10)  # p_u = 80 (80%), same casts
    matched = [_log(name=f"Ref{i}", damage_by_ability={1: ref_ability}) for i in range(15)]
    player = _log(dps=100.0, damage_by_ability={1: player_ability})

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    gap = next(a for a in report.abilities if a.spell.spell_id == 1)
    assert gap.volume == pytest.approx(0.0)
    assert gap.efficiency == pytest.approx(gap.delta_d)
    assert gap.delta_d == pytest.approx(-200.0)


def test_ability_below_impact_gate_does_not_appear_in_the_list(tmp_path: Path) -> None:
    # duration=300s, player_dps=1000 -> delta_dps_pct = delta_d / 300 / 1000 * 100
    # a delta_d of 900 gives exactly 0.3% (< IMPACT_GATE_PCT=0.5%).
    assert IMPACT_GATE_PCT == 0.5
    player_ability = _ability(total=900.0, casts=9)
    matched = [_log(name=f"Ref{i}") for i in range(15)]  # never cast it: c_r = p_r = 0
    player = _log(dps=1000.0, damage_by_ability={1: player_ability})

    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )

    assert 1 not in {a.spell.spell_id for a in report.abilities}
    assert report.n_other == 1
    assert report.other_pct == pytest.approx(0.3)


# -- diagnosis rules, in the exact documented order ----------------------------


def test_diagnosis_rule1_unpaired_buffs_when_efficiency_dominant_and_relaxed() -> None:
    diagnosis, confidence = _diagnose(
        volume=1.0,
        efficiency=-10.0,
        buffs_relaxed=True,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "buffs_nao_pareados"
    assert confidence == "baixa"


def test_diagnosis_rule2_few_targets_when_below_cohort_p25() -> None:
    diagnosis, _confidence = _diagnose(
        volume=1.0,
        efficiency=-10.0,
        buffs_relaxed=False,
        player_avg_targets=1.0,
        cohort_avg_targets=[5.0] * 20,  # p25 well above 1.0
    )
    assert diagnosis == "poucos_alvos"


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
    assert diagnosis == "usos_perdidos_excedentes"
    assert confidence == "alta"


def test_diagnosis_rule4_window_or_own_buffs_when_efficiency_dominates() -> None:
    diagnosis, confidence = _diagnose(
        volume=-1.0,
        efficiency=-10.0,
        buffs_relaxed=False,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "janela_ou_buffs_proprios"
    assert confidence == "alta"


def test_diagnosis_rule5_combined_fallback() -> None:
    diagnosis, confidence = _diagnose(
        volume=-3.0,
        efficiency=-2.0,
        buffs_relaxed=False,
        player_avg_targets=None,
        cohort_avg_targets=(),
    )
    assert diagnosis == "volume_e_eficiencia_combinados"
    assert confidence == "alta"


# -- header-level gap ------------------------------------------------------------


def test_gap_pct_relative_to_cohort_median(tmp_path: Path) -> None:
    matched = [_log(name=f"Ref{i}") for i in range(15)]
    player = _log(dps=880.0)
    report = analyze_dps_gap(
        player, matched, cohort_median_dps=1000.0, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.gap_pct == pytest.approx(-0.12)


def test_gap_pct_none_without_a_cohort_median(tmp_path: Path) -> None:
    player = _log(dps=880.0)
    report = analyze_dps_gap(
        player, [], cohort_median_dps=None, catalog=_catalog(tmp_path), buffs_relaxed=False
    )
    assert report.gap_pct is None
