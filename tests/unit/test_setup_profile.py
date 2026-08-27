"""EB.0 — captura do `SetupProfile` a partir do `combatantInfo` que o
`QUERY_PLAYER_META` já baixa hoje.

A revisão arquitetural provou que a coorte de execução é filtrada pelas
escolhas do próprio jogador (`cohort_match.py`, covariável `talent_cluster`),
o que valida uma build ruim contra outros que fizeram a mesma escolha. O
Encounter Benchmark existe para responder "o que quem vai bem está usando?"
de forma independente — e precisa destes dados.

EB.0 muda o que é **armazenado**, nunca o que é **analisado**: os testes
abaixo fixam as duas metades disso.

Zero rede: cassete gravado + Parquet em tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from botgitgud.domain.models import (
    TRINKET_SLOTS,
    FightRef,
    GearPiece,
    PlayerBuild,
    PlayerLog,
    SetupProfile,
    TalentNode,
)
from botgitgud.ingest.parquet_codec import read_parquet_log, write_parquet_log
from botgitgud.ingest.wcl_parsing import (
    count_tier_pieces,
    extract_setup_profile,
    extract_talent_pairs,
    find_player_in_details,
)

CASSETTE = Path(__file__).resolve().parents[1] / "fixtures" / "cassettes"


def _real_combatant_info() -> dict[str, Any]:
    """O primeiro cassete gravado que carrega combatantInfo com gear — dado
    real da API, não um fixture inventado.
    """

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            if "combatantInfo" in o:
                return o
            for v in o.values():
                if (r := walk(v)) is not None:
                    return r
        if isinstance(o, list):
            for v in o:
                if (r := walk(v)) is not None:
                    return r
        return None

    for path in sorted(CASSETTE.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        blob = json.dumps(payload)
        if '"combatantInfo"' in blob and '"gear"' in blob:
            found = walk(payload)
            if found and isinstance(found.get("combatantInfo"), dict):
                info = found["combatantInfo"]
                if info.get("gear"):
                    return info
    pytest.skip("nenhum cassete com combatantInfo.gear disponível")


# -- parsing do payload real ----------------------------------------------------------


def test_setup_profile_is_parsed_from_a_real_recorded_payload() -> None:
    setup = extract_setup_profile(_real_combatant_info())

    assert setup is not None
    assert setup.talents, "talentTree real deve produzir nós"
    assert setup.gear, "gear real deve produzir peças"
    assert setup.stats, "stats real deve produzir ratings"


def test_talent_nodes_carry_node_rank_and_spell_id() -> None:
    setup = extract_setup_profile(_real_combatant_info())
    assert setup is not None
    node = setup.talents[0]
    assert node.node_id > 0
    assert node.rank >= 1
    # D-26: `spell_id` e identidade, nunca nome — nao existe catalogo de
    # nomes de talento neste projeto.
    assert node.spell_id is None or node.spell_id > 0


def test_trinkets_are_exactly_the_two_trinket_slots() -> None:
    setup = extract_setup_profile(_real_combatant_info())
    assert setup is not None
    assert TRINKET_SLOTS == (12, 13)
    assert [g.slot for g in setup.trinkets] == [12, 13]
    assert all(g.item_id > 0 for g in setup.trinkets)


def test_empty_gear_slots_are_dropped_instead_of_stored_as_id_zero() -> None:
    """WCL reporta slot vazio como `id: 0`; guardar isso viraria um "item"
    fantasma em qualquer agregação de benchmark.
    """
    setup = extract_setup_profile(_real_combatant_info())
    assert setup is not None
    assert all(g.item_id != 0 for g in setup.gear)


def test_set_pieces_are_identified_per_slot_not_just_counted() -> None:
    setup = extract_setup_profile(_real_combatant_info())
    assert setup is not None
    assert setup.set_pieces
    assert all(g.set_id is not None for g in setup.set_pieces)


def test_stats_use_the_lowest_observed_value_as_the_baseline() -> None:
    setup = extract_setup_profile(
        {"stats": {"Haste": {"min": 1000, "max": 1500}, "Crit": {"min": 600, "max": 600}}}
    )
    assert setup is not None
    assert setup.stats == {"Haste": 1000.0, "Crit": 600.0}


# -- ausência honesta -------------------------------------------------------------------


@pytest.mark.parametrize("payload", [{}, {"gear": []}, {"talentTree": [], "stats": {}}, "nao-dict"])
def test_absent_setup_is_none_never_an_empty_shell(payload: Any) -> None:
    """`None` significa "não há dado"; um `SetupProfile` vazio significaria
    "o jogador não escolheu nada", que é falso. O benchmark precisa
    distinguir os dois para degradar honestamente.
    """
    assert extract_setup_profile(payload) is None


def test_malformed_entries_are_skipped_without_losing_the_valid_ones() -> None:
    setup = extract_setup_profile(
        {
            "talentTree": [{"nodeID": 1, "rank": 1}, {"lixo": True}, {"nodeID": 2}],
            "gear": [{"slot": 12, "id": 500}, {"slot": 13}, {"id": 900}],
        }
    )
    assert setup is not None
    assert [t.node_id for t in setup.talents] == [1]
    assert [(g.slot, g.item_id) for g in setup.gear] == [(12, 500)]


def test_player_build_defaults_to_no_setup() -> None:
    build = PlayerBuild(
        character_name="X",
        server=None,
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=4,
    )
    assert build.setup is None


# -- EB.0 muda o que é ARMAZENADO, nunca o que é ANALISADO ------------------------------


def test_existing_talent_and_tier_extraction_is_bit_for_bit_unchanged() -> None:
    """A garantia central de EB.0: as duas entradas que o pipeline REALMENTE
    consome hoje (`talent_pairs` no matching, `tier_pieces` na covariável)
    continuam idênticas. Se este teste quebrar, EB.0 deixou de ser inerte.
    """
    info = _real_combatant_info()
    tree = info.get("talentTree") or []

    pairs = extract_talent_pairs(tree)
    tier = count_tier_pieces(info.get("gear") or [])
    setup = extract_setup_profile(info)
    assert setup is not None

    # O mesmo conjunto (nodeID, rank), derivado por dois caminhos independentes.
    assert {(t.node_id, t.rank) for t in setup.talents} == set(pairs)
    # `tier_pieces` continua sendo a CONTAGEM crua de setIDs nao-nulos —
    # inclusive quando ha mais de um setID no gear (o cassete real tem dois).
    assert tier == len(setup.set_pieces)


def test_find_player_in_details_populates_setup_without_touching_other_fields() -> None:
    info = _real_combatant_info()
    details = {
        "dps": [
            {
                "id": 7,
                "name": "Zarad",
                "type": "Warlock",
                "specs": [{"spec": "Demonology"}],
                "maxItemLevel": 283.0,
                "combatantInfo": info,
            }
        ]
    }
    match = find_player_in_details(details, "zarad")

    assert match is not None
    assert match.setup is not None
    assert match.talent_pairs == extract_talent_pairs(info.get("talentTree") or [])
    assert match.tier_pieces == count_tier_pieces(info.get("gear") or [])


def test_a_player_without_combatant_info_still_matches_with_no_setup() -> None:
    match = find_player_in_details(
        {"dps": [{"id": 1, "name": "Zarad", "type": "Warlock", "specs": ["Demonology"]}]}, "Zarad"
    )
    assert match is not None
    assert match.setup is None
    assert match.talent_pairs == frozenset()


# -- persistência ------------------------------------------------------------------------


def _log(setup: SetupProfile | None) -> PlayerLog:
    return PlayerLog(
        fight=FightRef("ABCDEFGHIJKLMNOP", 1, 3179, "Boss", 5, 300.0, True, partition=3),
        build=PlayerBuild(
            character_name="Zarad",
            server="Azralon",
            class_name="Warlock",
            spec_name="Demonology",
            role="dps",
            item_level=283.0,
            talent_hash=None,
            tier_pieces=4,
            talent_pairs=frozenset({(1, 1)}),
            setup=setup,
        ),
        dps=100_000.0,
        percentile=71.0,
        cast_timeline={104316: (1.3, 60.0)},
    )


def test_setup_survives_a_parquet_round_trip(tmp_path: Path) -> None:
    setup = SetupProfile(
        talents=(TalentNode(62121, 1, 80180), TalentNode(94647, 2, None)),
        gear=(GearPiece(12, 249343, 298.0, None), GearPiece(0, 250060, 289.0, 1983)),
        stats={"Haste": 1066.0, "Crit": 608.0},
    )
    path = tmp_path / "log.parquet"
    write_parquet_log(_log(setup), path)

    restored = read_parquet_log(path).build.setup

    assert restored == setup
    assert restored is not None
    assert [g.slot for g in restored.trinkets] == [12]
    assert [g.set_id for g in restored.set_pieces] == [1983]


def test_a_log_without_setup_round_trips_as_none_not_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "log.parquet"
    write_parquet_log(_log(None), path)
    assert read_parquet_log(path).build.setup is None


def test_parquet_written_before_eb0_stays_readable(tmp_path: Path) -> None:
    """Regressão de migração: os ~977 Parquet já em disco não têm a coluna
    `setup_json`. Eles precisam continuar legíveis — o timeline/damage neles
    é o dado caro que esta mudança não pode custar.

    Simula o schema antigo removendo a coluna do arquivo escrito.
    """
    import pyarrow.parquet as pq

    path = tmp_path / "log.parquet"
    write_parquet_log(_log(SetupProfile(stats={"Haste": 1.0})), path)

    table = pq.read_table(path)
    assert "setup_json" in table.column_names
    legacy = tmp_path / "legacy.parquet"
    pq.write_table(table.drop_columns(["setup_json"]), legacy)

    restored = read_parquet_log(legacy)

    assert restored.build.setup is None  # ausente != vazio
    assert restored.build.talent_pairs == frozenset({(1, 1)})
    assert restored.cast_timeline == {104316: (1.3, 60.0)}  # dado caro intacto


def test_real_pre_eb0_parquet_on_disk_still_reads(tmp_path: Path) -> None:
    """O teste acima simula o schema antigo; este lê um arquivo REAL gravado
    antes de EB.0, se houver um no warehouse local.
    """
    raw = Path(__file__).resolve().parents[2] / "data" / "raw"
    files = sorted(raw.rglob("*.parquet")) if raw.is_dir() else []
    if not files:
        pytest.skip("nenhum Parquet pré-EB.0 disponível neste ambiente")

    log = read_parquet_log(files[0])

    assert log.build.setup is None
    assert log.fight.report_code
