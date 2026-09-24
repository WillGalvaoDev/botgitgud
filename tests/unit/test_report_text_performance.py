"""T3.1 — report/text.py's section ordering + rendering for the
performance-beyond-casts findings (T3.1's own
normative order: 1. Mortes/downtime 2. Active time 3. Uptimes 4. Waste de
recurso 5. Usos perdidos de CD 6. Timing de CD — "Build" was item 1 under
T2.2/T3.1, removed by EC.4; see analysis/talent_cluster.py's docstring).
"""

from __future__ import annotations

from botgitgud.analysis.comparison import SpellComparison, compare_spell_usage
from botgitgud.analysis.grading import QuantileStats
from botgitgud.analysis.performance_features import (
    PerformanceFindings,
    ScalarFinding,
    UptimeFinding,
    WasteFinding,
)
from botgitgud.domain.spells import SpellInfo
from botgitgud.report.text import ReportHeader, render_report

_STATS = QuantileStats(n=20, p10=0.80, p25=0.85, p50=0.90, p75=0.95, p90=0.99)


def _finding(grade: str, user_value: float, direction: str = "higher_better") -> ScalarFinding:
    return ScalarFinding(
        grade=grade,  # type: ignore[arg-type]
        quantile=0.05 if grade == "red" else 0.5,
        user_value=user_value,
        stats=_STATS,
        ci90=(0.85, 0.95),
        direction=direction,  # type: ignore[arg-type]
    )


def _performance(
    *,
    active_time_grade: str = "red",
    uptimes: tuple[UptimeFinding, ...] = (),
    resource_waste: tuple[WasteFinding, ...] = (),
) -> PerformanceFindings:
    return PerformanceFindings(
        active_time=_finding(active_time_grade, 0.50),
        deaths=_finding("red", 5.0, "lower_better"),
        downtime=_finding("red", 120.0, "lower_better"),
        uptimes=uptimes,
        resource_waste=resource_waste,
    )


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


def _timing_comparison() -> SpellComparison:
    return compare_spell_usage(
        spell=SpellInfo(spell_id=1, name="Call Dreadstalkers", source="wcl"),
        presence=0.9,
        user_times=[10.0, 40.0],
        ref_times=[10.0, 40.0],
        n_usages_median=2.0,
        reference_n=20,
    )


def test_resource_efficiency_precedes_offensive_timeline() -> None:
    text = render_report(
        _header(),
        [_timing_comparison()],
        performance=_performance(
            uptimes=(
                UptimeFinding(
                    spell=SpellInfo(spell_id=999, name="Bloodlust", source="wcl"),
                    finding=_finding("green", 0.9),
                ),
            ),
            resource_waste=(
                WasteFinding(resource_type="Mana", finding=_finding("red", 500.0, "lower_better")),
            ),
        ),
    )

    order = ["5 EFICIENCIA DE RECURSO", "Mortes:", "Tempo ativo:", "Mana", "6 TIMELINE OFENSIVA"]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_performance_sections_render_even_with_no_cd_comparisons() -> None:
    text = render_report(_header(), [], performance=_performance())
    assert "5 EFICIENCIA DE RECURSO" in text
    assert "Mortes:" in text
    assert "Downtime:" in text
    assert "Tempo ativo:" in text
    assert "TIMELINE OFENSIVA" not in text


def test_active_time_finding_precedes_any_cd_timing_section() -> None:
    """Acceptance: a player at the cohort's p05 for active_time_pct must
    have that finding rendered above any CD timing finding, regardless of
    the timing findings' own severity.
    """
    text = render_report(
        _header(), [_timing_comparison()], performance=_performance(active_time_grade="red")
    )
    assert text.index("Tempo ativo:") < text.index("MINOR CDS / BURST UTILITIES")


def test_uptime_and_waste_sections_absent_when_empty() -> None:
    text = render_report(_header(), [], performance=_performance(uptimes=(), resource_waste=()))
    assert "UPTIMES" not in text
    assert "WASTE DE RECURSO" not in text


def test_grade_emoji_rendered_for_deaths_and_active_time() -> None:
    text = render_report(_header(), [], performance=_performance())
    assert "🔴" in text


def test_section_five_absorbs_the_old_performance_subheadings() -> None:
    text = render_report(_header(), [], performance=_performance())
    assert "MORTES E DOWNTIME" not in text
    assert "ACTIVE TIME" not in text
    assert text.index("5 EFICIENCIA DE RECURSO") < text.index("Mortes:")


def test_no_performance_sections_when_performance_is_none() -> None:
    text = render_report(_header(), [], performance=None)
    assert "MORTES E DOWNTIME" not in text
    assert "ACTIVE TIME" not in text
