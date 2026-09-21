"""T0.2 — golden test: freezes legacy/bot.py's current behavior.

Runs the exact same pipeline as tests/fixtures/record.py, but against the
recorded cassettes instead of the live API (via the `mock_http` fixture from
tests/conftest.py), and compares the final report string against a syrupy
snapshot.

This test exists so every later Phase-0 correction (T0.5 alignment, T0.6
cadence fix, T0.7 report changes, ...) has something concrete to diff
against: any change to the *new* pipeline's output should be a deliberate,
reviewed difference from what the legacy monolith actually produced for this
fixture — never a silent behavior change.

legacy/bot.py itself is never touched by this test (frozen reference).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from legacy_runner import isolated_legacy_bot

FIXTURE_REPORT_CODE = "PtfBbQKRY9d6zAMC"
FIXTURE_FIGHT_ID = 1
FIXTURE_CHARACTER = "Zarad"


def _run_legacy_pipeline(legacy_bot: Any, isolated_cwd: Path) -> str:
    token = legacy_bot.get_wcl_token()
    assert token, "get_wcl_token() deveria retornar um token a partir do cassete gravado"

    user_data = legacy_bot.fetch_player_timeline_data(
        token, FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER
    )
    assert user_data is not None, f"jogador '{FIXTURE_CHARACTER}' não encontrado nos dados gravados"

    references, matched, min_d, max_d, avg_p, _parses, min_p, max_p = (
        legacy_bot.fetch_top_logs_for_cds(
            token,
            user_data["fight"]["encounter_id"],
            user_data["build"]["class"],
            user_data["build"]["spec"],
            user_data["fight"]["duration_sec"],
        )
    )
    assert matched > 0, "nenhuma referência encontrada nos dados gravados"

    profile = legacy_bot.build_cd_reference_profile(references)
    major_cds = legacy_bot.discover_clean_major_cds(profile)
    comparison = legacy_bot.compare_major_cds_clean(user_data, profile, major_cds)

    return legacy_bot.generate_coach_report_string(
        FIXTURE_CHARACTER, user_data, matched, min_d, max_d, avg_p, min_p, max_p, comparison
    )


def test_legacy_report_matches_golden_snapshot(
    mock_http: None, tmp_path: Path, snapshot: Any
) -> None:
    isolated_cwd = tmp_path / "legacy_scratch"
    with isolated_legacy_bot(isolated_cwd) as legacy_bot:
        report_text = _run_legacy_pipeline(legacy_bot, isolated_cwd)

    assert report_text == snapshot


def test_legacy_report_contains_no_missed_usage_section(mock_http: None, tmp_path: Path) -> None:
    """Documents achado 3.1 (relario.md): the legacy nearest-neighbor matcher
    can never report a missed cooldown usage, by construction. This must stop
    being true once T0.5/T0.7 land on the NEW pipeline — it stays true here
    forever, because legacy/bot.py is frozen.
    """
    isolated_cwd = tmp_path / "legacy_scratch"
    with isolated_legacy_bot(isolated_cwd) as legacy_bot:
        report_text = _run_legacy_pipeline(legacy_bot, isolated_cwd)

    assert "excedente" in report_text or "Ideal:" in report_text, (
        "esperava ver o formato de comparação do legacy no relatório"
    )
    assert "USOS PERDIDOS" not in report_text.upper()
