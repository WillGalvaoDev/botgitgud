from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

import pytest
from real_corpus import require_real_corpus

from botgitgud.analysis import proc_analysis
from botgitgud.analysis.ability_classification import AbilityClassification, AbilitySignals
from botgitgud.analysis.proc_analysis import ProcAnalysis, ProcMetrics, analyze_procs
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.models import AuraBand, AuraDetail, FightRef, PlayerBuild, PlayerLog


def _log(
    *,
    duration_s: float = 60.0,
    casts: dict[int, tuple[float, ...]] | None = None,
    uptimes: dict[int, float] | None = None,
    details: dict[int, AuraDetail] | None = None,
) -> PlayerLog:
    return PlayerLog(
        fight=FightRef("report", 1, 1, "Boss", 5, duration_s, True, partition=3),
        build=PlayerBuild("Player", None, "Mage", "Fire", "dps", None, None, None),
        dps=None,
        percentile=None,
        cast_timeline={} if casts is None else casts,
        uptimes={} if uptimes is None else uptimes,
        aura_details={} if details is None else details,
    )


def _classification(spell_id: int, role: AbilityRole) -> AbilityClassification:
    return AbilityClassification(
        spell_id,
        role,
        AbilitySignals(False, 0.0, False, 0, True, None),
        "test",
        False,
    )


def _classify_as(
    roles: dict[int, AbilityRole],
) -> Callable[[PlayerLog], Mapping[int, AbilityClassification]]:
    def classify(_log: PlayerLog) -> Mapping[int, AbilityClassification]:
        return {spell_id: _classification(spell_id, role) for spell_id, role in roles.items()}

    return classify


def test_derives_every_metric_by_hand_and_converts_ms_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as({10: AbilityRole.SELF_OFFENSIVE_PROC, 20: AbilityRole.CORE_DAMAGE}),
    )
    log = _log(
        duration_s=120.0,
        casts={20: (1.5, 3.0, 6.0, 9.0)},
        uptimes={10: 0.25},
        details={
            10: AuraDetail(
                4,
                (AuraBand(1000, 4000), AuraBand(3000, 7000), AuraBand(8000, 8500)),
            )
        },
    )

    result = analyze_procs(log)

    assert result.status == "available"
    assert result.reasons == ()
    assert result.metrics == (
        ProcMetrics(
            spell_id=10,
            role=AbilityRole.SELF_OFFENSIVE_PROC,
            status="available",
            reasons=(),
            uptime_frac=0.25,
            procs=4,
            procs_per_min=2.0,
            time_to_consume_s=(0.5, 0.0),
            alignment_frac=0.75,
            overlap_s=1.0,
            possibly_wasted_bands=1,
        ),
    )


def test_pre_m6_log_explicitly_degrades_without_partial_metrics() -> None:
    result = analyze_procs(_log(uptimes={10: 0.5}))
    assert result.status == "unavailable"
    assert result.reasons == (proc_analysis.NO_AURA_DETAILS_REASON,)
    assert result.metrics == ()


def test_real_pre_m6_corpus_explicitly_degrades() -> None:
    logs = require_real_corpus()
    assert logs
    without_details = [log for log in logs if not log.aura_details]
    assert without_details
    for log in without_details:
        result = analyze_procs(log)
        assert result.status == "unavailable"
        assert result.reasons == (proc_analysis.NO_AURA_DETAILS_REASON,)
        assert result.metrics == ()


def test_external_and_unresolved_auras_are_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as(
            {
                10: AbilityRole.SELF_OFFENSIVE_BUFF,
                11: AbilityRole.EXTERNAL_OFFENSIVE,
                12: AbilityRole.SELF_AURA_UNRESOLVED,
            }
        ),
    )
    detail = AuraDetail(1, (AuraBand(0, 1000),))
    result = analyze_procs(
        _log(
            uptimes={10: 0.1, 11: 0.1, 12: 0.1},
            details={10: detail, 11: detail, 12: detail},
        )
    )
    assert tuple(metric.spell_id for metric in result.metrics) == (10,)


def test_zero_uses_empty_bands_and_no_offensive_casts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as({10: AbilityRole.SELF_OFFENSIVE_PROC}),
    )
    metric = analyze_procs(_log(details={10: AuraDetail(0, ())})).metrics[0]
    assert metric.procs == 0
    assert metric.procs_per_min == 0.0
    assert metric.time_to_consume_s == ()
    assert metric.alignment_frac is None
    assert metric.overlap_s == 0.0
    assert metric.possibly_wasted_bands == 0
    assert metric.uptime_frac is None


def test_clamps_bands_and_includes_boundary_casts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as({10: AbilityRole.SELF_OFFENSIVE_BUFF, 20: AbilityRole.CORE_DAMAGE}),
    )
    metric = analyze_procs(
        _log(
            duration_s=10.0,
            casts={20: (0.0, 10.0)},
            details={10: AuraDetail(2, (AuraBand(-1000, 0), AuraBand(10000, 11000)))},
        )
    ).metrics[0]
    assert metric.time_to_consume_s == (0.0, 0.0)
    assert metric.alignment_frac == 1.0
    assert metric.possibly_wasted_bands == 0


def test_overlapping_bands_count_overlap_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as({10: AbilityRole.SELF_OFFENSIVE_PROC}),
    )
    metric = analyze_procs(
        _log(
            duration_s=10.0,
            details={
                10: AuraDetail(
                    3,
                    (AuraBand(-1000, 8000), AuraBand(2000, 12000), AuraBand(4000, 6000)),
                )
            },
        )
    ).metrics[0]
    assert metric.overlap_s == 6.0
    assert metric.overlap_s is not None
    assert metric.overlap_s <= 10.0


def test_zero_duration_has_no_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as({10: AbilityRole.SELF_OFFENSIVE_PROC}),
    )
    metric = analyze_procs(_log(duration_s=0.0, details={10: AuraDetail(1, ())})).metrics[0]
    assert metric.procs_per_min is None


def test_band_permutation_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proc_analysis,
        "classify_abilities",
        _classify_as({10: AbilityRole.SELF_OFFENSIVE_PROC, 20: AbilityRole.CORE_DAMAGE}),
    )
    bands = (AuraBand(5000, 7000), AuraBand(1000, 3000), AuraBand(2000, 6000))
    log = _log(casts={20: (2.0, 5.5)}, details={10: AuraDetail(3, bands)})
    permuted = replace(log, aura_details={10: AuraDetail(3, tuple(reversed(bands)))})
    assert analyze_procs(log) == analyze_procs(permuted)


def test_status_reason_invariants() -> None:
    with pytest.raises(ValueError, match="reasons"):
        ProcAnalysis("available", ("reason",), ())
    with pytest.raises(ValueError, match="reasons"):
        ProcAnalysis("unavailable", (), ())


def test_metric_rejects_non_self_role() -> None:
    with pytest.raises(ValueError, match="role"):
        ProcMetrics(
            1,
            AbilityRole.EXTERNAL_OFFENSIVE,
            "available",
            (),
            None,
            None,
            None,
            (),
            None,
            None,
            None,
        )
