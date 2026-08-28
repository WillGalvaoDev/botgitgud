from __future__ import annotations

from pathlib import Path

from botgitgud.analysis.cadence import classify_cd_type, compute_cadence, is_eligible
from botgitgud.domain.blacklist import MAJOR_CD_BLACKLIST

# -- documented acceptance criteria ------------------------------------------


def test_single_usage_never_becomes_an_instant_cooldown() -> None:
    cadence = compute_cadence([200.0], n_usages_median=1.0)
    assert cadence.observed_interval_median is None
    assert cadence.observed_interval_median != 200.0


def test_three_usages_median_interval() -> None:
    cadence = compute_cadence([10.0, 130.0, 250.0], n_usages_median=1.0)
    assert cadence.observed_interval_median == 120.0


def test_single_usage_unknown_base_cooldown_classified_major_and_eligible() -> None:
    """legacy/bot.py descartava justamente essas — cooldowns longos usados uma vez."""
    cadence = compute_cadence([200.0], n_usages_median=1.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MAJOR"
    assert is_eligible(999, presence=0.85, cadence=cadence, blacklist=MAJOR_CD_BLACKLIST) is True


def test_ring_of_peace_is_not_filtered_by_name() -> None:
    """achado 3.12: 'ring' era um substring banido; agora a blacklist é só por ID."""
    ring_of_peace_spell_id = (
        207018  # a ID em si não importa — o ponto é que nome nunca entra na decisão
    )
    cadence = compute_cadence([10.0, 40.0, 70.0], n_usages_median=3.0)
    assert ring_of_peace_spell_id not in MAJOR_CD_BLACKLIST
    assert (
        is_eligible(
            ring_of_peace_spell_id, presence=0.9, cadence=cadence, blacklist=MAJOR_CD_BLACKLIST
        )
        is True
    )


def test_no_lexical_filters_anywhere_in_src() -> None:
    """achado 3.12: elegibilidade de spell nunca pode depender do NOME.

    `legacy/bot.py:523` decidia com
    `any(x in name_lower for x in ["potion", "healthstone", ..., "trinket"])`,
    e por isso este guard bane essas palavras em `src/`.

    EB.0 introduziu a única exceção auditada até então: `domain/models.py`
    usa "trinket" como nome de SLOT de equipamento (`TRINKET_SLOTS =
    (12, 13)`), selecionado por índice numérico do `combatantInfo.gear[]`
    — nunca comparando o nome de coisa alguma. EB.2 introduziu a segunda:
    `analysis/benchmark_aggregate.py` agrega prevalência de trinkets, mas
    por `item_id` (um inteiro), nunca por nome. EB.3 introduziu a terceira:
    `analysis/benchmark_store_models.py` apenas serializa o CAMPO
    `trinket_prevalence` (nome de atributo Python, não uma decisão sobre
    spell/item) de/para uma chave de dicionário JSON, ida e volta. EB.4
    introduziu a quarta: `ingest/benchmark_fetch.py`'s docstring de módulo
    lista "trinkets" em prosa como um dos dados que `SetupProfile` precisa
    — o arquivo não contém nenhuma linha de código que compare nome
    algum, só a query/fetch fight-wide de `combatantInfo`. SA.1 introduziu
    a quinta: `analysis/setup_finding.py` define `FindingCategory.TRINKET`/
    `TRINKET_PAIR` (nomes de categoria de domínio) e um `FindingSubject`
    identificado por `item_id` (inteiro), nunca por nome — mesmo padrão da
    segunda exceção. SA.3 introduziu a sexta: `analysis/setup_trinkets.py`
    compara trinkets equipados contra o benchmark, também só por `item_id`
    (via `FindingSubject.trinket`/`trinket_pair` e a chave crua
    `str(item_id)`/`"{a}+{b}"`, o mesmo formato de
    `benchmark_aggregate.trinket_pair_key`). Não existe sequer um campo de
    nome de item em `GearPiece`/`TalentNode` (domain/models.py) para
    filtrar por ele. O intento do guard (nada de decisão lexical sobre
    spells) continua valendo em todo o resto de `src/`, incluindo
    `analysis/` fora dessas seis exceções, onde elegibilidade é decidida.
    """
    src_root = Path(__file__).resolve().parents[2] / "src"
    banned_words = ("potion", "healthstone", "trinket")
    reviewed_exceptions = {
        src_root / "botgitgud" / "domain" / "models.py",
        src_root / "botgitgud" / "analysis" / "benchmark_aggregate.py",
        src_root / "botgitgud" / "analysis" / "benchmark_store_models.py",
        src_root / "botgitgud" / "ingest" / "benchmark_fetch.py",
        src_root / "botgitgud" / "analysis" / "setup_finding.py",
        src_root / "botgitgud" / "analysis" / "setup_trinkets.py",
    }

    offenders = []
    for path in src_root.rglob("*.py"):
        if path in reviewed_exceptions:
            continue
        text = path.read_text(encoding="utf-8").lower()
        if any(word in text for word in banned_words):
            offenders.append(path)
    assert offenders == []


def test_the_reviewed_trinket_exception_selects_by_slot_never_by_name() -> None:
    """Guarda a própria exceção acima: se `domain/models.py` algum dia
    passar a comparar nomes, este teste quebra em vez de deixar a exceção
    virar um buraco silencioso no achado 3.12.
    """
    models = Path(__file__).resolve().parents[2] / "src" / "botgitgud" / "domain" / "models.py"
    text = models.read_text(encoding="utf-8")

    assert "TRINKET_SLOTS: tuple[int, int] = (12, 13)" in text
    assert "g.slot in TRINKET_SLOTS" in text
    # nenhuma comparação de nome, em nenhuma forma
    assert ".lower()" not in text
    assert "name_lower" not in text


def test_the_reviewed_benchmark_aggregate_exception_keys_trinkets_by_item_id() -> None:
    """Segunda exceção (EB.2): identidade de trinket é `item_id` (inteiro),
    nunca um nome. `GearPiece`/`TalentNode` (domain/models.py) nem têm um
    campo de nome — não há nada para comparar por string aqui.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "botgitgud"
        / "analysis"
        / "benchmark_aggregate.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "str(g.item_id)" in text  # chave de trinket = item_id, não nome
    assert "sorted(t.item_id for t in trinkets)" in text  # par canônico por item_id
    # `.lower()` aqui só normaliza identidade de JOGADOR (achado 3.12 é sobre
    # nome de SPELL/item) — mas nenhuma decisão de elegibilidade de spell
    # pode depender de string em lugar nenhum deste arquivo.
    assert "spell.lower()" not in text
    assert "ability.lower()" not in text


def test_the_reviewed_benchmark_store_models_exception_only_passes_field_names_through() -> None:
    """Terceira exceção (EB.3): `trinket_prevalence`/`trinket_pair_prevalence`
    são nomes de CAMPO de `BandBenchmark` (EB.2), só copiados entre atributo
    Python e chave JSON — nenhuma comparação de string decide elegibilidade
    aqui.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "botgitgud"
        / "analysis"
        / "benchmark_store_models.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "b.trinket_prevalence" in text  # leitura de atributo, não string
    assert '"trinket_prevalence"' in text  # só a chave JSON correspondente
    # `.lower()` aparece aqui só para normalizar IDENTIDADE DE JOGADOR
    # (`population_fingerprint`), mesma convenção de
    # benchmark_aggregate.py's `_player_identity` — não é elegibilidade de
    # spell, então não é banida nesta exceção (diferente da 1a exceção,
    # domain/models.py, onde nenhum `.lower()` deveria existir de jeito
    # nenhum). O que este teste garante é mais específico: nenhuma decisão
    # de SPELL/item por nome.
    assert "spell" not in text.lower()
    assert ".name.lower()" not in text


def test_the_reviewed_benchmark_fetch_exception_only_mentions_trinkets_in_prose() -> None:
    """Quarta exceção (EB.4): `ingest/benchmark_fetch.py` só cita "trinkets"
    no docstring de módulo, ao listar os dados que `SetupProfile` precisa —
    nenhuma linha de CÓDIGO deste arquivo compara nome de spell/item.
    """
    path = (
        Path(__file__).resolve().parents[2] / "src" / "botgitgud" / "ingest" / "benchmark_fetch.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "spell" not in text.lower()
    assert ".lower()" not in text  # nenhuma normalização de nome sequer existe aqui
    # a única query deste arquivo é fight-wide por combatantInfo — nenhuma
    # comparação de nome de player/item decide o que é buscado.
    assert "QUERY_PLAYER_SETUP_ONLY" in text


def test_the_reviewed_setup_finding_exception_keys_trinkets_by_item_id() -> None:
    """Quinta exceção (SA.1): `FindingSubject.trinket`/`trinket_pair` são
    identificados por `item_id` (inteiro) — mesmo padrão da segunda
    exceção (`benchmark_aggregate.py`), nunca um nome de item comparado.
    """
    path = (
        Path(__file__).resolve().parents[2] / "src" / "botgitgud" / "analysis" / "setup_finding.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "item_id: int | None" in text
    assert "spell" not in text.lower()
    # `.lower()` aparece aqui só para normalizar TEXTO LIVRE contra o
    # vocabulário causal proibido (`validate_setup_language`) — não é
    # elegibilidade de spell/item por nome, então não é banida nesta
    # exceção (mesma distinção da terceira exceção, benchmark_store_models.py).
    assert ".item_id.lower()" not in text
    assert "item_name" not in text.lower()


def test_the_reviewed_setup_trinkets_exception_keys_trinkets_by_item_id() -> None:
    """Sexta exceção (SA.3): `setup_trinkets.py` compara trinkets equipados
    contra o benchmark só por `item_id` (inteiro), nunca por nome — mesmo
    padrão da segunda e quinta exceções.
    """
    path = (
        Path(__file__).resolve().parents[2] / "src" / "botgitgud" / "analysis" / "setup_trinkets.py"
    )
    text = path.read_text(encoding="utf-8")

    assert "g.item_id" in text
    assert "spell" not in text.lower()
    assert "item_name" not in text.lower()


# -- classification branches --------------------------------------------------


def test_classify_uses_base_cooldown_first_when_known() -> None:
    cadence = compute_cadence([0.0, 10.0], n_usages_median=2.0, base_cooldown=120.0)
    assert classify_cd_type(cadence) == "MAJOR"  # base_cooldown wins even though interval is 10s


def test_classify_minor_base_cooldown() -> None:
    cadence = compute_cadence([], n_usages_median=1.0, base_cooldown=30.0)
    assert classify_cd_type(cadence) == "MINOR"


def test_classify_falls_back_to_observed_interval_when_base_unknown() -> None:
    cadence = compute_cadence([0.0, 100.0], n_usages_median=2.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MAJOR"


def test_classify_multi_use_short_interval_is_minor() -> None:
    cadence = compute_cadence([0.0, 20.0, 40.0], n_usages_median=3.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MINOR"


def test_classify_single_usage_high_n_median_is_minor() -> None:
    # n_usages_median > 1.5 with no other evidence: not treated as a rare major CD.
    cadence = compute_cadence([200.0], n_usages_median=4.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MINOR"


# -- eligibility branches ------------------------------------------------------


def test_low_presence_is_ineligible() -> None:
    cadence = compute_cadence([0.0, 100.0], n_usages_median=2.0)
    assert is_eligible(1, presence=0.5, cadence=cadence, blacklist=frozenset()) is False


def test_short_observed_interval_is_ineligible() -> None:
    cadence = compute_cadence([0.0, 10.0], n_usages_median=2.0)  # interval = 10s < 15s floor
    assert is_eligible(1, presence=0.9, cadence=cadence, blacklist=frozenset()) is False


def test_short_base_cooldown_is_ineligible() -> None:
    cadence = compute_cadence([], n_usages_median=1.0, base_cooldown=10.0)
    assert is_eligible(1, presence=0.9, cadence=cadence, blacklist=frozenset()) is False


def test_blacklisted_id_is_ineligible_regardless_of_stats() -> None:
    cadence = compute_cadence([0.0, 100.0], n_usages_median=2.0)
    assert is_eligible(22812, presence=0.99, cadence=cadence, blacklist=MAJOR_CD_BLACKLIST) is False


def test_unknown_interval_never_excludes_on_its_own() -> None:
    cadence = compute_cadence([200.0], n_usages_median=1.0)  # single usage: interval unknown
    assert is_eligible(1, presence=0.9, cadence=cadence, blacklist=frozenset()) is True


def test_iqr_of_single_interval_is_zero() -> None:
    cadence = compute_cadence([10.0, 40.0], n_usages_median=2.0)
    assert cadence.observed_interval_iqr == 0.0


def test_iqr_with_multiple_intervals() -> None:
    cadence = compute_cadence([0.0, 10.0, 20.0, 40.0], n_usages_median=4.0)
    assert cadence.observed_interval_iqr is not None
    assert cadence.observed_interval_iqr >= 0.0
