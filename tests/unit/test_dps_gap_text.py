from __future__ import annotations

from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.grading import QuantileStats
from botgitgud.domain.spells import SpellInfo
from botgitgud.report.dps_gap_text import render_dps_gap_section
from botgitgud.report.text import ReportHeader, render_report

_STATS = QuantileStats(n=20, p10=0, p25=0, p50=0, p75=0, p90=0)


def _gap(**overrides: object) -> AbilityGap:
    defaults: dict[str, object] = {
        "spell": SpellInfo(spell_id=1, name="Chaos Strike", source="wcl"),
        "n_u": 8.0,
        "d_u": 800.0,
        "p_u": 100.0,
        "n_r": 10.0,
        "d_r": 1000.0,
        "p_r": 100.0,
        "delta_d": -200.0,
        "volume": -200.0,
        "efficiency": 0.0,
        "interaction": 0.0,
        "delta_dps_pct": -6.3,
        "volume_dps_pct": -6.3,
        "efficiency_dps_pct": 0.0,
        "diagnosis": "usos_perdidos_excedentes",
        "confidence": "alta",
        "unit_kind": "CAST",
    }
    defaults.update(overrides)
    return AbilityGap(**defaults)  # type: ignore[arg-type]


def _report(**overrides: object) -> DpsGapReport:
    defaults: dict[str, object] = {
        "player_dps": 1_090_000.0,
        "cohort_median_dps": 1_240_000.0,
        "gap_pct": -0.121,
        "duration_s": 300.0,
        "abilities": (_gap(),),
        "other_pct": -0.6,
        "n_other": 11,
        "measured_dps": 1_090_000.0,
    }
    defaults.update(overrides)
    return DpsGapReport(**defaults)  # type: ignore[arg-type]


def _header(**overrides: object) -> ReportHeader:
    defaults: dict[str, object] = {
        "char_name": "Zarad",
        "boss_name": "Fallen-King Salhadaar",
        "class_name": "Warlock",
        "spec": "Demonology",
        "reference_n": 20,
        "duration_min_s": 300.0,
        "duration_max_s": 360.0,
    }
    defaults.update(overrides)
    return ReportHeader(**defaults)  # type: ignore[arg-type]


def test_header_line_shows_player_cohort_and_gap() -> None:
    lines = render_dps_gap_section(_report())
    header_line = next(line for line in lines if line.startswith("Você:"))
    assert "1.09M DPS" in header_line
    assert "1.24M DPS" in header_line
    assert "-12.1%" in header_line


def test_ability_row_shows_diagnosis_label() -> None:
    lines = render_dps_gap_section(_report())
    row = next(line for line in lines if "Chaos Strike" in line)
    assert "usos perdidos/excedentes" in row
    assert "-6.3pp" in row


def test_low_confidence_ability_is_flagged_in_the_row() -> None:
    report = _report(abilities=(_gap(diagnosis="buffs_nao_pareados", confidence="baixa"),))
    lines = render_dps_gap_section(report)
    row = next(line for line in lines if "Chaos Strike" in line)
    assert "confiança baixa" in row


def test_other_abilities_aggregate_row() -> None:
    lines = render_dps_gap_section(_report())
    other_row = next(line for line in lines if line.startswith("(outras"))
    assert "(outras 11)" in other_row
    assert "-0.6pp" in other_row


def test_missing_cohort_median_degrades_gracefully() -> None:
    report = _report(cohort_median_dps=None, gap_pct=None)
    lines = render_dps_gap_section(report)
    header_line = next(line for line in lines if line.startswith("Você:"))
    assert "n/d" in header_line


def test_dps_gap_section_renders_right_after_header_in_full_report() -> None:
    text = render_report(_header(), [], dps_gap=_report())
    assert text.index("TOP 3 PRIORIDADES") < text.index("DE ONDE VEIO O GAP DE DPS")


def test_dps_gap_section_absent_when_not_provided() -> None:
    text = render_report(_header(), [], dps_gap=None)
    assert "DE ONDE VEIO O GAP DE DPS" not in text
