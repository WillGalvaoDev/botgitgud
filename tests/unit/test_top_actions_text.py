from __future__ import annotations

from botgitgud.analysis.findings import Finding, RelevanceFinding, TopPriorities
from botgitgud.report.text import ReportHeader, render_report
from botgitgud.report.top_actions_text import render_top_actions_section


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "kind": "ABILITY_GAP",
        "title": "Chaos Strike",
        "detail": "O DPS observado ficou abaixo da referência; causa não identificada.",
        "observed_deficit_player_pp": 6.3,
        "confidence": "alta",
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


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


def test_empty_ranking_is_honest_about_unquantified_findings() -> None:
    lines = render_top_actions_section(TopPriorities())
    text = "\n".join(lines)
    assert "Não há dados suficientes para definir prioridades confiáveis nesta luta." in text
    assert "Nenhum problema material detectado" not in text


def test_renders_between_one_and_three_numbered_actions() -> None:
    findings = [
        _finding(title=f"Ability{i}", observed_deficit_player_pp=float(i)) for i in range(1, 3)
    ]
    lines = render_top_actions_section(TopPriorities(level1=tuple(findings)))
    text = "\n".join(lines)
    assert "1. **Ability1**" in text
    assert "2. **Ability2**" in text


def test_shows_estimated_gain_and_confidence() -> None:
    lines = render_top_actions_section(TopPriorities(level1=(_finding(),)))
    text = "\n".join(lines)
    assert "+6.3pp" in text
    assert "confiança: alta" in text


def test_top_actions_section_always_present_in_full_report() -> None:
    text_empty = render_report(_header(), [])
    assert "TOP 3 PRIORIDADES" in text_empty

    text_with = render_report(_header(), [], top_actions=TopPriorities(level1=(_finding(),)))
    assert "TOP 3 PRIORIDADES" in text_with
    assert "Chaos Strike" in text_with


def test_top_actions_section_precedes_dps_gap_and_category_sections() -> None:
    text = render_report(_header(), [], top_actions=TopPriorities(level1=(_finding(),)))
    assert "TOP 3 PRIORIDADES" in text
    assert "TIMELINE OFENSIVA" not in text


def test_level2_uses_label_and_never_formats_decision_numbers() -> None:
    relevance = RelevanceFinding("UPTIME", "Uptime", "Ajuste o uptime.", 0.123, 0.987, "alta")
    text = "\n".join(render_top_actions_section(TopPriorities(level2=(relevance,))))
    assert "impacto não quantificado" in text
    assert "0.123" not in text
    assert "0.987" not in text
