from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from botgitgud.analysis.alignment import Alignment, AlignmentKind, AlignmentStep
from botgitgud.analysis.cadence import SpellCadence
from botgitgud.analysis.comparison import SpellComparison, StepGrade
from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.findings import Finding
from botgitgud.analysis.grading import QuantileStats
from botgitgud.analysis.performance_features import (
    PerformanceFindings,
    ScalarFinding,
    UptimeFinding,
    WasteFinding,
)
from botgitgud.domain.models import RunManifest
from botgitgud.domain.spells import SpellInfo
from botgitgud.report.html_report import render_html_report
from botgitgud.report.text import ReportHeader, render_header_and_top3

_STATS = QuantileStats(n=20, p10=8.0, p25=9.0, p50=10.0, p75=11.0, p90=12.0)


def _step_grade(grade: str = "green") -> StepGrade:
    return StepGrade(grade=grade, quantile=0.5, stats=_STATS, ci90=(9.0, 11.0))  # type: ignore[arg-type]


def _comparison(name: str = "Call Dreadstalkers") -> SpellComparison:
    steps = (
        AlignmentStep(
            kind=AlignmentKind.MATCH,
            user_index=0,
            ref_index=0,
            user_time=10.0,
            ref_time=10.0,
            delta=0.0,
        ),
        AlignmentStep(
            kind=AlignmentKind.MISSED,
            user_index=None,
            ref_index=1,
            user_time=None,
            ref_time=40.0,
            delta=None,
        ),
        AlignmentStep(
            kind=AlignmentKind.EXTRA,
            user_index=1,
            ref_index=None,
            user_time=70.0,
            ref_time=None,
            delta=None,
        ),
    )
    alignment = Alignment(steps=steps, total_cost=0.0, n_matched=1, n_missed=1, n_extra=1)
    return SpellComparison(
        spell=SpellInfo(spell_id=1, name=name, source="wcl"),
        cd_type="MAJOR",
        cadence=SpellCadence(
            observed_interval_median=30.0,
            observed_interval_iqr=5.0,
            n_usages_median=2.0,
            base_cooldown=None,
        ),
        presence=0.9,
        alignment=alignment,
        reference_n=20,
        step_grades=(_step_grade("green"), None, None),
    )


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "kind": "ABILITY_GAP",
        "title": "Chaos Strike",
        "detail": "dano por cast abaixo — janela ou buffs próprios",
        "estimated_gain_pct": 6.3,
        "confidence": "alta",
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def _ability_gap() -> AbilityGap:
    return AbilityGap(
        spell=SpellInfo(spell_id=2, name="Chaos Strike", source="wcl"),
        c_u=8.0,
        d_u=800.0,
        p_u=100.0,
        c_r=10.0,
        d_r=1000.0,
        p_r=100.0,
        delta_d=-200.0,
        volume=-200.0,
        efficiency=0.0,
        interaction=0.0,
        delta_dps_pct=-6.3,
        volume_dps_pct=-6.3,
        efficiency_dps_pct=0.0,
        diagnosis="usos_perdidos_excedentes",
        confidence="alta",
    )


def _dps_gap() -> DpsGapReport:
    return DpsGapReport(
        player_dps=1_090_000.0,
        cohort_median_dps=1_240_000.0,
        gap_pct=-0.121,
        duration_s=300.0,
        abilities=(_ability_gap(),),
        other_pct=-0.6,
        n_other=5,
    )


def _scalar_finding(grade: str = "red", user_value: float = 0.5) -> ScalarFinding:
    return ScalarFinding(
        grade=grade,  # type: ignore[arg-type]
        quantile=0.05,
        user_value=user_value,
        stats=_STATS,
        ci90=(9.0, 11.0),
        direction="higher_better",
    )


def _performance() -> PerformanceFindings:
    return PerformanceFindings(
        active_time=_scalar_finding("green", 0.95),
        deaths=_scalar_finding("red", 2.0),
        downtime=_scalar_finding("yellow", 10.0),
        uptimes=(
            UptimeFinding(
                spell=SpellInfo(spell_id=999, name="Bloodlust", source="wcl"),
                finding=_scalar_finding("green", 0.9),
            ),
        ),
        resource_waste=(WasteFinding(resource_type="Mana", finding=_scalar_finding("red", 500.0)),),
    )


def _manifest() -> RunManifest:
    from datetime import UTC, datetime

    return RunManifest(
        cohort_id="abc123",
        code_version="deadbee",
        generated_at=datetime(2026, 8, 18, tzinfo=UTC),
        n_members=20,
        wcl_partition=4,
        settings_hash="hash123",
    )


def _header(**overrides: object) -> ReportHeader:
    defaults: dict[str, object] = {
        "char_name": "Zarad",
        "boss_name": "Fallen-King Salhadaar & <Friends>",
        "class_name": "Warlock",
        "spec": "Demonology",
        "reference_n": 20,
        "duration_min_s": 300.0,
        "duration_max_s": 360.0,
        "player_dps": 108_297.0,
        "player_percentile": 57.0,
    }
    defaults.update(overrides)
    return ReportHeader(**defaults)  # type: ignore[arg-type]


def _full_html() -> str:
    return render_html_report(
        _header(),
        [_comparison()],
        manifest=_manifest(),
        performance=_performance(),
        dps_gap=_dps_gap(),
        top_actions=[_finding()],
        duration_s=345.0,
    )


# -- T3.4 acceptance criteria ----------------------------------------------------


def test_html_report_parses_under_a_strict_xml_parser() -> None:
    html = _full_html()
    root = ET.fromstring(html)  # raises ET.ParseError on any malformed markup
    assert root.tag == "{http://www.w3.org/1999/xhtml}html"


def test_html_report_has_no_external_resource_urls() -> None:
    html = _full_html()
    root = ET.fromstring(html)
    for el in root.iter():
        for attr in ("src", "href"):
            value = el.attrib.get(attr)
            if value is not None:
                assert not re.match(r"^https?://", value)
    # plain text mentioning a URL (never happens here) would still be fine —
    # only resource-loading attributes are checked above.


def test_html_report_special_characters_in_names_are_escaped() -> None:
    """The boss name contains `&` and `<`/`>` — a naive f-string embed
    would corrupt the XML; render_html_report must escape it.
    """
    html = render_html_report(_header(), [], duration_s=300.0)
    ET.fromstring(html)  # would raise if the ampersand/brackets weren't escaped
    assert "Fallen-King Salhadaar &amp; &lt;Friends&gt;" in html


def test_accessibility_every_grade_emoji_has_a_text_label() -> None:
    html = _full_html()
    for emoji, label in (
        ("🟢", "dentro do esperado"),
        ("🟡", "desvio moderado"),
        ("🔴", "desvio significativo"),
    ):
        if emoji in html:
            assert f"{emoji} {label}" in html


def test_all_three_required_sections_are_present() -> None:
    html = _full_html()
    assert "Timeline por Habilidade" in html
    assert "De Onde Veio o Gap de DPS" in html
    assert "Tabela de Features" in html


def test_report_with_no_optional_sections_still_produces_valid_xml() -> None:
    html = render_html_report(_header(), [], duration_s=0.0)
    ET.fromstring(html)


# -- Discord message length (T3.4's other acceptance criterion) ---------------


def test_header_and_top3_text_fits_in_a_single_discord_message() -> None:
    findings = [_finding(title=f"Ability {i}", detail="x" * 150) for i in range(3)]
    text = render_header_and_top3(_header(), findings)
    assert len(text) <= 2000
