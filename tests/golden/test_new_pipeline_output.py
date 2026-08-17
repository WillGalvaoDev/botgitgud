"""T0.7 — golden test for the NEW (bot.py) pipeline output.

Companion to tests/golden/test_legacy_output.py: same fixture log, but
replayed through the httpx-based ReplayTransport (bot.py's WclClient/
BlizzardClient use httpx, not requests — see httpx_cassette_transport.py)
and checked against the T0.7 acceptance criterion: the new report differs
from the frozen legacy snapshot, and that difference includes the
USOS PERDIDOS section.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from httpx_cassette_transport import ReplayTransport
from new_bot_runner import isolated_new_bot

FIXTURE_REPORT_CODE = "PtfBbQKRY9d6zAMC"
FIXTURE_FIGHT_ID = 1
FIXTURE_CHARACTER = "Zarad"


def _run_new_pipeline(new_bot: Any) -> str:
    new_bot._wcl_client = new_bot.WclClient(
        new_bot.WclClientConfig(client_id="test-id", client_secret="test-secret"),
        transport=ReplayTransport(),
    )
    new_bot._blizzard_client = new_bot.BlizzardClient(
        new_bot.BlizzardClientConfig(client_id="test-id", client_secret="test-secret"),
        transport=ReplayTransport(),
    )
    new_bot._spell_catalog = new_bot.SpellCatalog(
        Path("spells.json"), blizzard=new_bot._blizzard_client
    )

    user_data = new_bot.fetch_player_timeline_data(
        FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER
    )
    assert user_data is not None, "pipeline novo não encontrou o jogador nos dados gravados"

    references, matched, min_d, max_d, cohort_median_dps = new_bot.fetch_top_logs_for_cds(
        user_data["fight"]["encounter_id"],
        user_data["build"]["class"],
        user_data["build"]["spec"],
        user_data["fight"]["duration_sec"],
    )
    assert matched > 0, "nenhuma referência encontrada nos dados gravados"

    profile, num_positional = new_bot.build_cd_reference_profile(
        references, user_data["fight"]["duration_sec"]
    )
    eligible = new_bot.discover_eligible_spell_ids(profile)
    comparisons = new_bot.compare_all_spells(
        user_data, profile, eligible, reference_n=num_positional
    )

    warnings = []
    if new_bot.classify_cohort_size(matched) == "warn":
        warnings.append(
            f"Amostra pequena ({matched} logs). Trate os desvios como indicativos, não conclusivos."
        )
    if 0 < num_positional < new_bot.POSITIONAL_MIN_N:
        warnings.append(
            f"Apenas {num_positional} logs com duração próxima à sua (±12%) para comparar "
            "o timing dos cooldowns — os valores 'Ideal' têm confiança baixa."
        )

    percentile = new_bot.fetch_player_percentile(
        FIXTURE_REPORT_CODE,
        FIXTURE_FIGHT_ID,
        FIXTURE_CHARACTER,
        user_data["build"].get("server"),
        user_data["build"].get("region"),
        user_data["fight"]["encounter_id"],
        user_data["fight"].get("difficulty"),
    )

    header = new_bot.ReportHeader(
        char_name=FIXTURE_CHARACTER,
        boss_name=user_data["fight"]["boss_name"],
        class_name=user_data["build"]["class"],
        spec=user_data["build"]["spec"],
        reference_n=num_positional,
        duration_min_s=min_d,
        duration_max_s=max_d,
        player_dps=user_data.get("dps"),
        player_percentile=percentile,
        cohort_median_dps=cohort_median_dps,
        cohort_warnings=tuple(warnings),
    )
    return new_bot.render_report(header, comparisons)


def test_new_pipeline_report_matches_golden_snapshot(tmp_path: Path, snapshot: Any) -> None:
    isolated_cwd = tmp_path / "new_bot_scratch"
    with isolated_new_bot(isolated_cwd) as new_bot:
        report_text = _run_new_pipeline(new_bot)

    assert report_text == snapshot


def test_new_pipeline_differs_from_legacy_and_shows_missed_usage(tmp_path: Path) -> None:
    """Critério de aceite da T0.7: 'o novo snapshot difere do da T0.2 e a
    diferença contém a seção USOS PERDIDOS'.
    """
    legacy_snapshot_path = (
        Path(__file__).resolve().parent / "__snapshots__" / "test_legacy_output.ambr"
    )
    legacy_text = legacy_snapshot_path.read_text(encoding="utf-8")

    isolated_cwd = tmp_path / "new_bot_scratch"
    with isolated_new_bot(isolated_cwd) as new_bot:
        report_text = _run_new_pipeline(new_bot)

    assert report_text != legacy_text
    assert "USOS PERDIDOS" in report_text
    assert "USOS PERDIDOS" not in legacy_text


def test_new_pipeline_never_fabricates_parse_med(tmp_path: Path) -> None:
    """achado 3.10: o campo antigo 'Parse méd: 99' nunca deve reaparecer."""
    isolated_cwd = tmp_path / "new_bot_scratch"
    with isolated_new_bot(isolated_cwd) as new_bot:
        report_text = _run_new_pipeline(new_bot)
    assert "Parse méd" not in report_text
