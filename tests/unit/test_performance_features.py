from __future__ import annotations

from pathlib import Path

from botgitgud.analysis.performance_features import (
    UPTIME_PRESENCE_THRESHOLD,
    analyze_performance_features,
    grade_scalar,
)
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
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
    active_time_pct: float | None = 0.95,
    deaths: int = 0,
    downtime_s: float = 0.0,
    uptimes: dict[int, float] | None = None,
    resource_waste: dict[str, float] | None = None,
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
        dps=100_000.0,
        percentile=50.0,
        cast_timeline={},
        active_time_pct=active_time_pct,
        deaths=deaths,
        downtime_s=downtime_s,
        uptimes=uptimes or {},
        resource_waste=resource_waste or {},
    )


def _catalog(tmp_path: Path) -> SpellCatalog:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(999, "Known Aura", "wcl")
    return catalog


# -- grade_scalar: directional (one-tailed) grading --------------------------


def test_higher_better_flags_low_values_red_never_high_ones() -> None:
    ref = [0.80] * 20  # n=20 >= MIN_N_FOR_GRADING, all identical
    low = grade_scalar(0.10, ref, "higher_better")
    high = grade_scalar(0.99, ref, "higher_better")
    assert low.grade == "red"
    assert high.grade == "green"  # better than the cohort is never penalized


def test_lower_better_flags_high_values_red_never_low_ones() -> None:
    ref = [5.0] * 20
    high = grade_scalar(50.0, ref, "lower_better")
    low = grade_scalar(0.0, ref, "lower_better")
    assert high.grade == "red"
    assert low.grade == "green"


def test_insufficient_sample_never_colors() -> None:
    ref = [1.0] * 5  # below MIN_N_FOR_GRADING (15)
    finding = grade_scalar(999.0, ref, "lower_better")
    assert finding.grade == "insufficient"


def test_typical_value_grades_green() -> None:
    ref = list(range(1, 21))  # 1..20, median ~10.5
    finding = grade_scalar(10.0, ref, "higher_better")
    assert finding.grade == "green"


# -- analyze_performance_features: deaths/downtime/active_time ----------------


def test_deaths_and_downtime_findings_use_lower_better_direction(tmp_path: Path) -> None:
    player = _log(deaths=5, downtime_s=100.0)
    matched = [_log(name=f"Ref{i}", deaths=0, downtime_s=0.0) for i in range(20)]
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    assert result.deaths.grade == "red"
    assert result.downtime.grade == "red"


def test_active_time_finding_is_none_when_player_value_missing(tmp_path: Path) -> None:
    player = _log(active_time_pct=None)
    matched = [_log(name=f"Ref{i}") for i in range(20)]
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    assert result.active_time is None


def test_active_time_finding_grades_against_cohort(tmp_path: Path) -> None:
    player = _log(active_time_pct=0.50)
    matched = [_log(name=f"Ref{i}", active_time_pct=0.95) for i in range(20)]
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    assert result.active_time is not None
    assert result.active_time.grade == "red"


# -- uptimes: >=70% cohort presence gate ---------------------------------------


def test_uptime_reported_when_cohort_presence_meets_threshold(tmp_path: Path) -> None:
    n = 20
    n_present = int(n * UPTIME_PRESENCE_THRESHOLD) + 1
    matched = [_log(name=f"Ref{i}", uptimes={999: 0.80} if i < n_present else {}) for i in range(n)]
    player = _log(uptimes={999: 0.10})
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    spell_ids = {uf.spell.spell_id for uf in result.uptimes}
    assert 999 in spell_ids


def test_uptime_omitted_when_cohort_presence_below_threshold(tmp_path: Path) -> None:
    n = 20
    n_present = int(n * UPTIME_PRESENCE_THRESHOLD) - 1  # below 70%
    matched = [_log(name=f"Ref{i}", uptimes={999: 0.80} if i < n_present else {}) for i in range(n)]
    player = _log(uptimes={999: 0.10})
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    spell_ids = {uf.spell.spell_id for uf in result.uptimes}
    assert 999 not in spell_ids


def test_ec1_uptime_reference_distribution_excludes_refs_without_the_buff(tmp_path: Path) -> None:
    """EC.1 regression: once the cohort mixes builds, refs that structurally
    never have access to a buff must never be padded into its reference
    distribution as 0.0 uptime — that would drag the comparison basis
    toward players who couldn't have the buff at all, contaminating the
    grade of players who reliably maintain it.
    """
    n = 20
    n_present = int(n * UPTIME_PRESENCE_THRESHOLD) + 1  # clears the reporting-relevance gate
    matched = [_log(name=f"Ref{i}", uptimes={999: 0.90} if i < n_present else {}) for i in range(n)]
    player = _log(uptimes={999: 0.85})  # genuinely close to the real (uncontaminated) median
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    finding = next(uf for uf in result.uptimes if uf.spell.spell_id == 999).finding
    # com a distribuição correta (só quem tem o buff), 0.85 fica logo abaixo
    # de um grupo uniforme em 0.90 — nunca um outlier "green" fabricado por
    # uma mediana artificialmente puxada para baixo pelos zeros de quem não
    # tem o mecanismo.
    assert finding.stats.p50 == 0.90
    assert finding.grade != "green"


def test_uptime_absence_is_not_imputed_as_zero(tmp_path: Path) -> None:
    matched = [_log(name=f"Ref{i}", uptimes={999: 0.90}) for i in range(20)]
    player = _log(uptimes={})  # never had the buff
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    assert all(uf.spell.spell_id != 999 for uf in result.uptimes)


def test_uptime_findings_omit_unresolved_abilities(tmp_path: Path) -> None:
    resolved_id = 111
    unresolved_id = 222
    uptimes = {resolved_id: 0.90, unresolved_id: 0.90}
    matched = [_log(name=f"Ref{i}", uptimes=uptimes) for i in range(20)]
    player = _log(uptimes={resolved_id: 0.10, unresolved_id: 0.10})
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    catalog.learn(resolved_id, "Known Aura", "wcl")

    result = analyze_performance_features(player, matched, catalog)

    assert {finding.spell.spell_id for finding in result.uptimes} == {resolved_id}


# -- resource waste -------------------------------------------------------------


def test_resource_waste_finding_grades_lower_better(tmp_path: Path) -> None:
    matched = [_log(name=f"Ref{i}", resource_waste={"Mana": 10.0}) for i in range(20)]
    player = _log(resource_waste={"Mana": 500.0})
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    waste_finding = next(wf for wf in result.resource_waste if wf.resource_type == "Mana")
    assert waste_finding.finding.grade == "red"


def test_resource_waste_only_reports_types_the_player_actually_has(tmp_path: Path) -> None:
    matched = [
        _log(name=f"Ref{i}", resource_waste={"Mana": 10.0, "Fúria (Rage)": 5.0}) for i in range(20)
    ]
    player = _log(resource_waste={"Mana": 1.0})
    result = analyze_performance_features(player, matched, _catalog(tmp_path))
    types = {wf.resource_type for wf in result.resource_waste}
    assert types == {"Mana"}
