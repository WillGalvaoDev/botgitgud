from __future__ import annotations

from botgitgud.analysis.comparison import compare_spell_usage
from botgitgud.domain.spells import SpellInfo
from botgitgud.report.text import ReportHeader, chunk_report_for_discord, render_report


def _spell(spell_id: int, name: str) -> SpellInfo:
    return SpellInfo(spell_id=spell_id, name=name, source="wcl")


def _header(**overrides: object) -> ReportHeader:
    defaults: dict[str, object] = {
        "char_name": "Zarad",
        "boss_name": "Fallen-King Salhadaar",
        "class_name": "Warlock",
        "spec": "Demonology",
        "reference_n": 2,
        "duration_min_s": 300.0,
        "duration_max_s": 360.0,
    }
    defaults.update(overrides)
    return ReportHeader(**defaults)  # type: ignore[arg-type]


def test_missed_usage_section_appears_when_there_are_missed_usages() -> None:
    comparison = compare_spell_usage(
        spell=_spell(1, "Eye Beam"),
        presence=0.9,
        user_times=[10.0, 130.0],
        ref_times=[10.0, 130.0, 250.0, 370.0],
        n_usages_median=4.0,
        reference_n=10,
    )
    text = render_report(_header(), [comparison])
    assert "USOS PERDIDOS" in text
    assert "Eye Beam" in text.split("USOS PERDIDOS")[1].split("MAJOR")[0]


def test_missed_usage_section_absent_when_nothing_missed() -> None:
    comparison = compare_spell_usage(
        spell=_spell(1, "Fireball"),
        presence=0.9,
        user_times=[10.0, 130.0],
        ref_times=[10.0, 130.0],
        n_usages_median=2.0,
        reference_n=10,
    )
    text = render_report(_header(), [comparison])
    assert "USOS PERDIDOS" not in text


def test_ability_never_used_by_player_still_appears_in_report() -> None:
    """O pior erro possível (achado 3.1, segunda metade): 0 usos e presence alta."""
    comparison = compare_spell_usage(
        spell=_spell(1, "Summon Demonic Tyrant"),
        presence=0.9,
        user_times=[],
        ref_times=[60.0, 180.0],
        n_usages_median=2.0,
        reference_n=10,
    )
    text = render_report(_header(), [comparison])
    assert "Summon Demonic Tyrant" in text
    assert "Usos: 0 (coorte: 2.0)" in text
    assert "USOS PERDIDOS" in text


def test_extra_usage_rendered_without_masked_delta() -> None:
    comparison = compare_spell_usage(
        spell=_spell(1, "Call Dreadstalkers"),
        presence=0.9,
        user_times=[10.0, 40.0, 70.0],
        ref_times=[10.0, 40.0],
        n_usages_median=2.0,
        reference_n=10,
    )
    text = render_report(_header(), [comparison])
    assert "Uso extra" in text
    assert "Delta: +0.0s" not in text  # legacy's masked-zero behavior must not reappear


def test_usage_count_line_shows_user_and_cohort_median() -> None:
    comparison = compare_spell_usage(
        spell=_spell(1, "Dark Pact"),
        presence=0.9,
        user_times=[10.0, 40.0],
        ref_times=[10.0, 40.0, 70.0],
        n_usages_median=2.5,
        reference_n=10,
    )
    text = render_report(_header(), [comparison])
    assert "Usos: 2 (coorte: 2.5)" in text


def test_header_shows_dps_and_percentile_when_available() -> None:
    header = _header(player_dps=108342.5, player_percentile=71.0, cohort_median_dps=117576.0)
    text = render_report(header, [])
    assert "108,342" in text or "108,343" in text  # formatted with thousands separator
    assert "71" in text
    assert "117,576" in text


def test_header_falls_back_to_nd_never_fabricates() -> None:
    header = _header(player_dps=None, player_percentile=None, cohort_median_dps=None)
    text = render_report(header, [])
    assert "n/d" in text
    assert "99" not in text  # the legacy fabricated-99.0 value must never appear


def test_no_parse_med_field_anywhere() -> None:
    """achado 3.10: characterRankings não tem `percentile`; o campo antigo some de vez."""
    header = _header(player_dps=1000.0, player_percentile=50.0, cohort_median_dps=2000.0)
    text = render_report(header, [])
    assert "Parse méd" not in text


def test_reference_count_shown_explicitly() -> None:
    header = _header(reference_n=47)
    text = render_report(header, [])
    assert "47 logs" in text


def test_empty_comparisons_still_renders_header_and_notice() -> None:
    text = render_report(_header(), [])
    assert "Nenhum Major/Minor CD elegível encontrado" in text


# -- chunking ------------------------------------------------------------------


def test_chunking_never_splits_a_line() -> None:
    lines = [f"line-{i}" * 5 for i in range(500)]
    text = "\n".join(lines)
    chunks = chunk_report_for_discord(text, max_len=200)
    reconstructed_lines = "\n".join(chunks).split("\n")
    assert reconstructed_lines == lines


def test_chunking_respects_max_len() -> None:
    lines = [f"line-{i}" * 5 for i in range(500)]
    text = "\n".join(lines)
    chunks = chunk_report_for_discord(text, max_len=200)
    assert all(len(c) <= 200 for c in chunks)


def test_chunking_single_short_text_is_one_chunk() -> None:
    chunks = chunk_report_for_discord("hello\nworld", max_len=1900)
    assert chunks == ["hello\nworld"]


def test_chunking_handles_line_longer_than_max_len() -> None:
    """A single line longer than max_len still comes back as its own chunk
    (never silently truncated or dropped) — it's just not further split.
    """
    long_line = "x" * 300
    chunks = chunk_report_for_discord(f"short\n{long_line}\nshort2", max_len=200)
    assert long_line in "\n".join(chunks)
    assert "short" in chunks[0]
    assert "short2" in chunks[-1]
