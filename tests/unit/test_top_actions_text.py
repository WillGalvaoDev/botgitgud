from __future__ import annotations

from botgitgud.analysis.findings import Finding
from botgitgud.report.text import ReportHeader, render_report
from botgitgud.report.top_actions_text import render_top_actions_section


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


def test_shows_nothing_material_message_when_no_actions() -> None:
    lines = render_top_actions_section([])
    text = "\n".join(lines)
    assert "Nenhum problema material detectado" in text


def test_renders_between_one_and_three_numbered_actions() -> None:
    findings = [_finding(title=f"Ability{i}", estimated_gain_pct=float(i)) for i in range(1, 3)]
    lines = render_top_actions_section(findings)
    text = "\n".join(lines)
    assert "1. **Ability1**" in text
    assert "2. **Ability2**" in text


def test_shows_estimated_gain_and_confidence() -> None:
    lines = render_top_actions_section([_finding()])
    text = "\n".join(lines)
    assert "+6.3pp" in text
    assert "confiança: alta" in text


def test_top_actions_section_always_present_in_full_report() -> None:
    text_empty = render_report(_header(), [])
    assert "TOP 3 AÇÕES" in text_empty

    text_with = render_report(_header(), [], top_actions=[_finding()])
    assert "TOP 3 AÇÕES" in text_with
    assert "Chaos Strike" in text_with


def test_top_actions_section_precedes_dps_gap_and_category_sections() -> None:
    text = render_report(_header(), [], top_actions=[_finding()])
    assert text.index("TOP 3 AÇÕES") < text.index("Nenhum Major/Minor CD")
