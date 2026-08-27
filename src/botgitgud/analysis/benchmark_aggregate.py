"""EB.2 — agregação PURA/OFFLINE de uma população de observações de setup em
um Encounter Benchmark: prevalências por banda de percentile, sample sizes,
cobertura. Nenhuma persistência, nenhum job, nenhum cache, nenhuma query WCL,
nenhuma integração no pipeline de relatório — isso é EB.3+/SA.

Reutiliza `PlayerLog` (domain/models.py) como a observação de entrada: ele já
carrega report_code (`fight.report_code`), identidade do jogador
(`build.character_name`/`server`), rank_percent (`percentile`), duration
(`fight.duration_s`), item_level (`build.item_level`), `SetupProfile`
(`build.setup`) e as dimensões do target (`fight.encounter_id/difficulty/
partition`, `build.class_name/spec_name`) — exatamente os campos mínimos
pedidos, sem precisar de um tipo novo redundante.

Esta função NUNCA recebe um "jogador alvo": ela agrega a população inteira
observada em cada banda, sem filtrar por nenhuma escolha de setup de
ninguém — é exatamente o oposto do que `cohort_match.py`'s `talent_cluster`
faz hoje para a Execution Cohort, e é o motivo de o Encounter Benchmark
existir.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.domain.models import PlayerLog, SetupProfile

CANONICAL_SECONDARY_STATS: tuple[str, ...] = ("Crit", "Haste", "Mastery", "Versatility")

BandStatus = Literal["ok", "insufficient"]


class EncounterBenchmarkAggregationError(ValueError):
    """Uma observação não pertence ao target pedido — falha fechado em vez
    de silenciosamente misturar populações de contextos diferentes. Esta
    função assume que o CALLER já restringiu a entrada ao target; ela só
    confirma isso, não faz a restrição.
    """


# -- tipos de saída ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescriptiveStats:
    """n=0 é um resultado válido e explícito (nunca um KeyError/entrada
    ausente) — median/p25/p75 ficam `None` nesse caso, nunca 0.0, para que
    "sem dado" nunca seja lido como "valor zero".
    """

    n: int
    median: float | None
    p25: float | None
    p75: float | None


@dataclass(frozen=True, slots=True)
class PrevalenceEntry:
    key: str
    n_observed: int
    prevalence: float  # n_observed / n_available do PrevalenceDistribution pai


@dataclass(frozen=True, slots=True)
class PrevalenceDistribution:
    """`n_available` é o denominador EXPLÍCITO desta dimensão — nunca o
    tamanho total da banda. `entries` vazio quando `n_available == 0`
    (nenhuma observação tinha a dimensão disponível) — nunca inferido, é a
    única forma de `entries` vazio acontecer, já que qualquer observação
    disponível contribui com pelo menos uma entrada.
    """

    n_available: int
    entries: tuple[PrevalenceEntry, ...]  # ordenado: n_observed desc, key asc


@dataclass(frozen=True, slots=True)
class SetPieceEntry:
    set_id: str
    n_players: int  # jogadores com >=1 peça deste set (denominador: n_available do SetSummary)
    total_pieces: int  # contagem crua de peças — 2pc/4pc real fica para SA.4
    prevalence: float  # n_players / n_available


@dataclass(frozen=True, slots=True)
class SetSummary:
    n_available: int
    entries: tuple[SetPieceEntry, ...]


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    total_observations: int
    setup_available: int
    setup_missing: int
    coverage_ratio: float  # setup_available / total_observations; 0.0 se total==0


@dataclass(frozen=True, slots=True)
class BandBenchmark:
    band_name: str
    status: BandStatus  # "insufficient" quando sample_size < policy.min_sample_size
    sample_size: int  # pós-dedup
    raw_observation_count: int  # pré-dedup — quantas caíram nesta banda antes de deduplicar
    talent_build_prevalence: PrevalenceDistribution
    trinket_prevalence: PrevalenceDistribution
    trinket_pair_prevalence: PrevalenceDistribution
    set_summary: SetSummary
    secondary_stats: Mapping[
        str, DescriptiveStats
    ]  # sempre as 4 chaves de CANONICAL_SECONDARY_STATS
    duration_summary: DescriptiveStats
    item_level_summary: DescriptiveStats


@dataclass(frozen=True, slots=True)
class EncounterBenchmark:
    target: EncounterBenchmarkTarget
    policy_version: str
    total_input_observations: int
    eligible_observations: int  # rank_percent presente e em 0..100
    missing_setup_count: (
        int  # == coverage.setup_missing; exposto duas vezes de propósito (pedido explícito)
    )
    deduped_count: int  # observações REMOVIDAS pelo dedup (soma de todas as bandas)
    outside_policy_bands: int  # rank_percent elegível mas fora de toda banda aprovada (auditoria)
    coverage: CoverageSummary
    bands: Mapping[str, BandBenchmark]  # chave = nome da banda, na ordem canônica da policy


# -- identidade de talent build -----------------------------------------------


def talent_build_key(setup: SetupProfile) -> str | None:
    """Identidade canônica de build: só `(node_id, rank)`, ordenados — nunca
    `spell_id`. `spell_id` é metadado auxiliar (docs: `TalentNode`) que pode
    faltar de forma inconsistente mesmo para builds mecanicamente idênticas;
    deixá-lo entrar na chave faria a mesma build virar "duas" builds
    diferentes por um dado que não descreve a escolha em si.

    `None` quando não há talentos (não confundir com uma build vazia real —
    ver `SetupProfile.talents` vazio: hoje isso só acontece se `combatantInfo`
    tinha gear/stats mas nenhum talentTree válido).
    """
    if not setup.talents:
        return None
    pairs = sorted((t.node_id, t.rank) for t in setup.talents)
    return "|".join(f"{n}:{r}" for n, r in pairs)


def _trinket_pair_key(setup: SetupProfile) -> str | None:
    """Canonicalizado por `item_id`, nunca por slot — o mesmo par em 12/13
    ou 13/12 é o mesmo par. `None` a menos que EXATAMENTE 2 trinkets estejam
    presentes (um par precisa de dois lados; 0 ou 1 não formam par).
    """
    trinkets = setup.trinkets
    if len(trinkets) != 2:
        return None
    a, b = sorted(t.item_id for t in trinkets)
    return f"{a}+{b}"


# -- dedup ---------------------------------------------------------------------


def _player_identity(log: PlayerLog) -> tuple[str, str]:
    """`(nome, server)` normalizados por case — mesma convenção que
    `wcl_parsing.find_player_in_details` já usa para comparar nomes de
    personagem vindos da WCL.
    """
    return (log.build.character_name.strip().lower(), (log.build.server or "").strip().lower())


def _dedup_priority(log: PlayerLog) -> tuple[float, str, int]:
    """Regra CANÔNICA de desempate entre observações do MESMO jogador na
    MESMA banda — usada com `min()`, então "menor tupla" == "vence". Maior
    `rank_percent` primeiro (nega para inverter a ordem), depois
    `report_code` menor, depois `fight_id` menor. Nunca ordem de chegada,
    nunca `hash()`, nunca timing — só campos determinísticos e sempre
    presentes do próprio log.
    """
    rank = log.percentile if log.percentile is not None else -1.0
    return (-rank, log.fight.report_code, log.fight.fight_id)


def _dedup_band(members: Sequence[PlayerLog]) -> tuple[list[PlayerLog], int]:
    """Uma observação por jogador por banda (DedupPolicy.ONE_LOG_PER_PLAYER
    de EB.1, agora com semântica real): cinco personagens do mesmo jogador
    na mesma banda contam como UMA evidência, não cinco.
    """
    by_identity: dict[tuple[str, str], list[PlayerLog]] = {}
    for log in members:
        by_identity.setdefault(_player_identity(log), []).append(log)

    kept: list[PlayerLog] = []
    removed = 0
    for identity in sorted(by_identity):  # saída determinística, não ordem de inserção
        group = by_identity[identity]
        kept.append(min(group, key=_dedup_priority))
        removed += len(group) - 1
    return kept, removed


# -- prevalência genérica --------------------------------------------------------


def _prevalence_distribution(
    band_members: Sequence[PlayerLog],
    *,
    is_available: Callable[[PlayerLog], bool],
    keys_for: Callable[[PlayerLog], frozenset[str]],
) -> PrevalenceDistribution:
    available = [m for m in band_members if is_available(m)]
    n_available = len(available)
    counts: dict[str, int] = {}
    for member in available:
        for key in keys_for(member):
            counts[key] = counts.get(key, 0) + 1
    entries = tuple(
        PrevalenceEntry(key=k, n_observed=v, prevalence=(v / n_available if n_available else 0.0))
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return PrevalenceDistribution(n_available=n_available, entries=entries)


def _talent_keys(log: PlayerLog) -> frozenset[str]:
    if log.build.setup is None:
        return frozenset()
    key = talent_build_key(log.build.setup)
    return frozenset({key}) if key else frozenset()


def _trinket_keys(log: PlayerLog) -> frozenset[str]:
    if log.build.setup is None:
        return frozenset()
    # jogador distinto por item, mesmo que o item apareça nos dois slots —
    # numerador nunca passa de n_available (evita prevalence > 100%).
    return frozenset(str(g.item_id) for g in log.build.setup.trinkets)


def _trinket_pair_keys(log: PlayerLog) -> frozenset[str]:
    if log.build.setup is None:
        return frozenset()
    key = _trinket_pair_key(log.build.setup)
    return frozenset({key}) if key else frozenset()


def _set_summary(band_members: Sequence[PlayerLog]) -> SetSummary:
    """Denominador = observações com `setup` disponível (não "com peças de
    set"): 0 peças de set é um estado real do jogador, não um dado ausente
    — diferente de trinkets, onde slot vazio nos dados É um dado ausente.

    NÃO tenta concluir 2pc/4pc — só contagem crua de peças por setID
    (`total_pieces`) e quantos jogadores usam >=1 peça (`n_players`). Não
    reusa `count_tier_pieces` legado, que soma setIDs distintos juntos.
    """
    available = [m for m in band_members if m.build.setup is not None]
    n_available = len(available)

    total_pieces: dict[str, int] = {}
    players_by_set: dict[str, int] = {}
    for member in available:
        setup = member.build.setup
        assert setup is not None
        sets_this_player: set[str] = set()
        for piece in setup.set_pieces:
            set_key = str(piece.set_id)
            total_pieces[set_key] = total_pieces.get(set_key, 0) + 1
            sets_this_player.add(set_key)
        for set_key in sets_this_player:
            players_by_set[set_key] = players_by_set.get(set_key, 0) + 1

    entries = tuple(
        SetPieceEntry(
            set_id=set_key,
            n_players=players_by_set[set_key],
            total_pieces=total_pieces[set_key],
            prevalence=(players_by_set[set_key] / n_available if n_available else 0.0),
        )
        for set_key in sorted(total_pieces, key=lambda k: (-players_by_set[k], k))
    )
    return SetSummary(n_available=n_available, entries=entries)


# -- estatísticas descritivas ----------------------------------------------------


def _descriptive_stats(values: Sequence[float]) -> DescriptiveStats:
    n = len(values)
    if n == 0:
        return DescriptiveStats(n=0, median=None, p25=None, p75=None)
    ordered = sorted(values)
    median = statistics.median(ordered)
    if n < 2:
        return DescriptiveStats(n=n, median=median, p25=median, p75=median)
    # mesmo padrão de analysis/cadence.py: 3 pontos de corte com n=4, inclusive.
    q1, _q2, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    return DescriptiveStats(n=n, median=median, p25=q1, p75=q3)


def _secondary_stat_summary(band_members: Sequence[PlayerLog]) -> dict[str, DescriptiveStats]:
    result: dict[str, DescriptiveStats] = {}
    for stat_name in CANONICAL_SECONDARY_STATS:
        values = [
            m.build.setup.stats[stat_name]
            for m in band_members
            if m.build.setup is not None and stat_name in m.build.setup.stats
        ]
        result[stat_name] = _descriptive_stats(values)
    return result


# -- validação -------------------------------------------------------------------


def _matches_target(log: PlayerLog, target: EncounterBenchmarkTarget) -> bool:
    return (
        log.build.class_name == target.spec.class_name
        and log.build.spec_name == target.spec.spec_name
        and log.fight.encounter_id == target.encounter_id
        and log.fight.difficulty == target.difficulty
        and log.fight.partition == target.partition
    )


# -- banda individual --------------------------------------------------------------


def _build_band(
    name: str, members: list[PlayerLog], raw_count: int, policy: BenchmarkPolicy
) -> BandBenchmark:
    sample_size = len(members)
    status: BandStatus = "ok" if sample_size >= policy.min_sample_size else "insufficient"

    talent_prev = _prevalence_distribution(
        members,
        is_available=lambda log: log.build.setup is not None and bool(log.build.setup.talents),
        keys_for=_talent_keys,
    )
    trinket_prev = _prevalence_distribution(
        members,
        is_available=lambda log: log.build.setup is not None and bool(log.build.setup.trinkets),
        keys_for=_trinket_keys,
    )
    trinket_pair_prev = _prevalence_distribution(
        members,
        is_available=lambda log: log.build.setup is not None and len(log.build.setup.trinkets) == 2,
        keys_for=_trinket_pair_keys,
    )

    return BandBenchmark(
        band_name=name,
        status=status,
        sample_size=sample_size,
        raw_observation_count=raw_count,
        talent_build_prevalence=talent_prev,
        trinket_prevalence=trinket_prev,
        trinket_pair_prevalence=trinket_pair_prev,
        set_summary=_set_summary(members),
        secondary_stats=_secondary_stat_summary(members),
        duration_summary=_descriptive_stats([m.fight.duration_s for m in members]),
        item_level_summary=_descriptive_stats(
            [m.build.item_level for m in members if m.build.item_level is not None]
        ),
    )


# -- entrada pública ----------------------------------------------------------------


def build_encounter_benchmark(
    observations: Sequence[PlayerLog],
    *,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
) -> EncounterBenchmark:
    """Pura, determinística, offline. Nenhum parâmetro de "jogador alvo" —
    de propósito: esta é a agregação da POPULAÇÃO observada, nunca filtrada
    pelo setup de ninguém em particular.
    """
    for log in observations:
        if not _matches_target(log, target):
            raise EncounterBenchmarkAggregationError(
                f"observação {log.fight.report_code}/{log.fight.fight_id} "
                f"não pertence ao target {target.benchmark_id}"
            )

    total_input = len(observations)
    setup_available = sum(1 for o in observations if o.build.setup is not None)
    coverage = CoverageSummary(
        total_observations=total_input,
        setup_available=setup_available,
        setup_missing=total_input - setup_available,
        coverage_ratio=(setup_available / total_input) if total_input else 0.0,
    )

    eligible_count = 0
    outside_count = 0
    banded: dict[str, list[PlayerLog]] = {band.name: [] for band in policy.bands}
    for log in observations:
        pct = log.percentile
        if pct is None or not (0.0 <= pct <= 100.0):
            continue
        eligible_count += 1
        band = policy.band_for(pct)
        if band is None:
            outside_count += 1
        else:
            banded[band.name].append(log)

    deduped_total = 0
    band_results: dict[str, BandBenchmark] = {}
    for band in policy.bands:  # ordem canônica da própria policy (já ordenada por low)
        raw_members = banded[band.name]
        deduped_members, n_removed = _dedup_band(raw_members)
        deduped_total += n_removed
        band_results[band.name] = _build_band(band.name, deduped_members, len(raw_members), policy)

    return EncounterBenchmark(
        target=target,
        policy_version=policy.policy_version,
        total_input_observations=total_input,
        eligible_observations=eligible_count,
        missing_setup_count=coverage.setup_missing,
        deduped_count=deduped_total,
        outside_policy_bands=outside_count,
        coverage=coverage,
        bands=band_results,
    )
