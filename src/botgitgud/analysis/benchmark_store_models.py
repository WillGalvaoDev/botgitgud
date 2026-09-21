"""EB.3 split of analysis/benchmark_store.py's schema/serialization/freshness
data model out of its persistence logic, mirroring bot/job_models.py's split
from bot/jobs.py — kept under the 300-line limit and independently testable
without a live Store.

Three concerns live here, all pure/offline:
  1. the `encounter_benchmarks` table DDL + migration hook;
  2. canonical JSON (de)serialization of a full `EncounterBenchmark` tree;
  3. `BenchmarkFreshness`/`BenchmarkFreshnessPolicy` — staleness as a
     structured result, never a bare bool.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import (
    BandBenchmark,
    CoverageSummary,
    DescriptiveStats,
    EncounterBenchmark,
    PrevalenceDistribution,
    PrevalenceEntry,
    SetPieceEntry,
    SetSummary,
)
from botgitgud.domain.models import PlayerLog

# -- schema --------------------------------------------------------------------

CREATE_ENCOUNTER_BENCHMARKS_TABLE = """
CREATE TABLE IF NOT EXISTS encounter_benchmarks (
    benchmark_id VARCHAR PRIMARY KEY,
    class_name VARCHAR, spec_name VARCHAR,
    encounter_id INTEGER, difficulty INTEGER, partition INTEGER,
    benchmark_policy_version VARCHAR,
    created_at TIMESTAMP, updated_at TIMESTAMP,
    source_observation_count INTEGER, eligible_observation_count INTEGER,
    setup_available_count INTEGER, setup_missing_count INTEGER, deduped_count INTEGER,
    population_fingerprint VARCHAR, policy_fingerprint VARCHAR,
    benchmark_payload VARCHAR
)
"""
# `benchmark_id` já É a identidade completa (spec/encounter/difficulty/
# partition/policy_version — EB.1) como PRIMARY KEY: um upsert na MESMA
# identidade atualiza a linha; identidade diferente (partition ou
# policy_version diferentes) é, por construção, uma LINHA DIFERENTE — a
# coexistência exigida pela revisão arquitetural cai direto do desenho de
# EB.1, sem lógica extra aqui.
#
# Nenhuma migração ainda — hook point para a próxima, mesmo padrão de
# bot/job_models.py's MIGRATE_JOBS_TABLE (ALTER TABLE ... ADD COLUMN IF NOT
# EXISTS), para esta tabela nunca precisar inventar um segundo mecanismo.
MIGRATE_ENCOUNTER_BENCHMARKS_TABLE: tuple[str, ...] = ()


def now_utc_naive() -> datetime:
    """Mesma convenção de bot/job_models.py: TIMESTAMP (não TIMESTAMPTZ,
    que precisaria de `pytz`, fora da lista de dependências do projeto) —
    todo datetime que este módulo grava/compara é UTC-mas-naive.
    """
    return datetime.now(UTC).replace(tzinfo=None)


# -- fingerprints ----------------------------------------------------------------


def population_fingerprint(observations: Sequence[PlayerLog]) -> str:
    """Identidade determinística da POPULAÇÃO usada para construir um
    benchmark — nunca de `hash()` do Python (não estável entre processos),
    nunca de ordem de input (ordenado antes de hashear). Só identidade
    estável e sempre presente de cada observação: report_code, fight_id, e
    a identidade de jogador (mesma normalização de dedup em
    benchmark_aggregate.py's `_player_identity`).

    Distingue "mesma target/policy, população diferente" de "mesma target/
    policy, mesma população" — dois builds da mesma população, em ordens de
    input diferentes, produzem o MESMO fingerprint.
    """
    identities = sorted(
        f"{o.fight.report_code}|{o.fight.fight_id}|"
        f"{o.build.character_name.strip().lower()}|{(o.build.server or '').strip().lower()}"
        for o in observations
    )
    payload = "\n".join(identities)
    return hashlib.sha256(payload.encode()).hexdigest()


def policy_fingerprint(policy: BenchmarkPolicy) -> str:
    """Mais fino que `benchmark_policy_version` (uma string escolhida à
    mão): um hash do corpo INTEIRO da policy (bandas, min_sample_size,
    dedup_policy, max_data_age_days). Existe para pegar o caso em que a
    versão declarada não mudou mas a definição real mudou — exatamente o
    que a condição obrigatória "matching_policy_version antes de mudar
    coorte" quer evitar que aconteça silenciosamente.
    """
    payload = json.dumps(policy.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# -- payload: encode/decode ----------------------------------------------------


class EncounterBenchmarkCorruptPayloadError(ValueError):
    """Um `benchmark_payload` persistido não pôde ser decodificado — falha
    fechada, nunca um objeto parcial. Mesma convenção de EB.1/EB.2:
    `ValueError`, não `BotGitGudError` (erro de forma dos dados de
    persistência, não um erro de domínio de análise).
    """


def _encode_descriptive_stats(s: DescriptiveStats) -> dict[str, object]:
    return {"n": s.n, "median": s.median, "p25": s.p25, "p75": s.p75}


def _decode_descriptive_stats(d: dict[str, object]) -> DescriptiveStats:
    return DescriptiveStats(n=d["n"], median=d["median"], p25=d["p25"], p75=d["p75"])  # type: ignore[arg-type]


def _encode_prevalence_entry(e: PrevalenceEntry) -> dict[str, object]:
    return {"key": e.key, "n_observed": e.n_observed, "prevalence": e.prevalence}


def _decode_prevalence_entry(d: dict[str, object]) -> PrevalenceEntry:
    return PrevalenceEntry(**d)  # type: ignore[arg-type]


def _encode_prevalence_distribution(pd: PrevalenceDistribution) -> dict[str, object]:
    return {
        "n_available": pd.n_available,
        "entries": [_encode_prevalence_entry(e) for e in pd.entries],
    }


def _decode_prevalence_distribution(d: dict[str, object]) -> PrevalenceDistribution:
    entries_raw = d["entries"]
    assert isinstance(entries_raw, list)
    return PrevalenceDistribution(
        n_available=d["n_available"],  # type: ignore[arg-type]
        entries=tuple(_decode_prevalence_entry(e) for e in entries_raw),
    )


def _encode_set_piece_entry(e: SetPieceEntry) -> dict[str, object]:
    return {
        "set_id": e.set_id,
        "n_players": e.n_players,
        "total_pieces": e.total_pieces,
        "prevalence": e.prevalence,
    }


def _decode_set_piece_entry(d: dict[str, object]) -> SetPieceEntry:
    return SetPieceEntry(**d)  # type: ignore[arg-type]


def _encode_set_summary(s: SetSummary) -> dict[str, object]:
    return {
        "n_available": s.n_available,
        "entries": [_encode_set_piece_entry(e) for e in s.entries],
    }


def _decode_set_summary(d: dict[str, object]) -> SetSummary:
    entries_raw = d["entries"]
    assert isinstance(entries_raw, list)
    return SetSummary(
        n_available=d["n_available"],  # type: ignore[arg-type]
        entries=tuple(_decode_set_piece_entry(e) for e in entries_raw),
    )


def _encode_coverage(c: CoverageSummary) -> dict[str, object]:
    return {
        "total_observations": c.total_observations,
        "setup_available": c.setup_available,
        "setup_missing": c.setup_missing,
        "coverage_ratio": c.coverage_ratio,
    }


def _decode_coverage(d: dict[str, object]) -> CoverageSummary:
    return CoverageSummary(**d)  # type: ignore[arg-type]


def _encode_band(b: BandBenchmark) -> dict[str, object]:
    return {
        "band_name": b.band_name,
        "status": b.status,
        "sample_size": b.sample_size,
        "raw_observation_count": b.raw_observation_count,
        "talent_build_prevalence": _encode_prevalence_distribution(b.talent_build_prevalence),
        "trinket_prevalence": _encode_prevalence_distribution(b.trinket_prevalence),
        "trinket_pair_prevalence": _encode_prevalence_distribution(b.trinket_pair_prevalence),
        "set_summary": _encode_set_summary(b.set_summary),
        "secondary_stats": {k: _encode_descriptive_stats(v) for k, v in b.secondary_stats.items()},
        "duration_summary": _encode_descriptive_stats(b.duration_summary),
        "item_level_summary": _encode_descriptive_stats(b.item_level_summary),
    }


def _decode_band(d: dict[str, object]) -> BandBenchmark:
    secondary_raw = d["secondary_stats"]
    assert isinstance(secondary_raw, dict)
    return BandBenchmark(
        band_name=d["band_name"],  # type: ignore[arg-type]
        status=d["status"],  # type: ignore[arg-type]
        sample_size=d["sample_size"],  # type: ignore[arg-type]
        raw_observation_count=d["raw_observation_count"],  # type: ignore[arg-type]
        talent_build_prevalence=_decode_prevalence_distribution(d["talent_build_prevalence"]),  # type: ignore[arg-type]
        trinket_prevalence=_decode_prevalence_distribution(d["trinket_prevalence"]),  # type: ignore[arg-type]
        trinket_pair_prevalence=_decode_prevalence_distribution(d["trinket_pair_prevalence"]),  # type: ignore[arg-type]
        set_summary=_decode_set_summary(d["set_summary"]),  # type: ignore[arg-type]
        secondary_stats={k: _decode_descriptive_stats(v) for k, v in secondary_raw.items()},
        duration_summary=_decode_descriptive_stats(d["duration_summary"]),  # type: ignore[arg-type]
        item_level_summary=_decode_descriptive_stats(d["item_level_summary"]),  # type: ignore[arg-type]
    )


def encode_benchmark(benchmark: EncounterBenchmark) -> str:
    """JSON canônico (chaves ordenadas, sem espaço) — o round-trip
    (`decode_benchmark(encode_benchmark(b)) == b`) é a garantia central
    desta tarefa, testada diretamente.
    """
    payload = {
        "target": benchmark.target.to_dict(),
        "policy_version": benchmark.policy_version,
        "total_input_observations": benchmark.total_input_observations,
        "eligible_observations": benchmark.eligible_observations,
        "missing_setup_count": benchmark.missing_setup_count,
        "deduped_count": benchmark.deduped_count,
        "outside_policy_bands": benchmark.outside_policy_bands,
        "coverage": _encode_coverage(benchmark.coverage),
        "bands": {name: _encode_band(b) for name, b in benchmark.bands.items()},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_benchmark(raw: str) -> EncounterBenchmark:
    """Falha fechado: qualquer forma inesperada (JSON inválido, chave
    faltando, tipo errado) vira `EncounterBenchmarkCorruptPayloadError` —
    nunca um `EncounterBenchmark` parcial/incompleto.
    """
    try:
        payload = json.loads(raw)
        bands_raw = payload["bands"]
        assert isinstance(bands_raw, dict)
        return EncounterBenchmark(
            target=EncounterBenchmarkTarget.from_dict(payload["target"]),
            policy_version=payload["policy_version"],
            total_input_observations=payload["total_input_observations"],
            eligible_observations=payload["eligible_observations"],
            missing_setup_count=payload["missing_setup_count"],
            deduped_count=payload["deduped_count"],
            outside_policy_bands=payload["outside_policy_bands"],
            coverage=_decode_coverage(payload["coverage"]),
            bands={name: _decode_band(b) for name, b in bands_raw.items()},
        )
    except (KeyError, TypeError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        raise EncounterBenchmarkCorruptPayloadError(
            f"benchmark_payload corrompido/incompatível: {exc}"
        ) from exc


# -- freshness -------------------------------------------------------------------

# Heurística operacional, versionável — NÃO uma constante estatística
# validada. Ajustável livremente sem precisar de um bump em
# BenchmarkPolicy.policy_version (que é sobre elegibilidade de DADO de
# entrada, não sobre quando um benchmark JÁ CONSTRUÍDO deve ser refeito).
DEFAULT_TTL_DAYS = 7.0
DEFAULT_STALE_POPULATION_GROWTH_RATIO = 0.30

StalenessReason = Literal[
    "partition_changed",
    "policy_changed",
    "population_growth",
    "max_age_exceeded",
    "missing",
    "corrupt",
]
BenchmarkFreshnessStatus = Literal["fresh", "stale"]


@dataclass(frozen=True, slots=True)
class BenchmarkFreshnessPolicy:
    """Deliberadamente SEPARADA de `BenchmarkPolicy.max_data_age_days`
    (EB.1): aquele campo decide quais OBSERVAÇÕES são elegíveis para
    entrar num benchmark — uma política de DADO. `ttl_days` decide quando
    o BENCHMARK JÁ CONSTRUÍDO deve ser considerado velho demais para servir
    sem reconstrução — uma política de CACHE/STORE. São perguntas
    diferentes: um benchmark pode ter só observações "frescas" pelo
    critério de dado e ainda assim estar com o TTL de cache vencido.

    Fronteiras (documentadas para não ficarem implícitas): `age_days >
    ttl_days` é stale (o valor exato do TTL ainda conta como fresco);
    `population_growth_ratio > stale_population_growth_ratio` é stale
    (mesma convenção — exatamente no limiar ainda é fresco).
    """

    ttl_days: float = DEFAULT_TTL_DAYS
    stale_population_growth_ratio: float = DEFAULT_STALE_POPULATION_GROWTH_RATIO

    def __post_init__(self) -> None:
        if self.ttl_days <= 0:
            raise ValueError("ttl_days must be positive")
        if self.stale_population_growth_ratio < 0:
            raise ValueError("stale_population_growth_ratio must be non-negative")


DEFAULT_FRESHNESS_POLICY = BenchmarkFreshnessPolicy()


@dataclass(frozen=True, slots=True)
class BenchmarkFreshness:
    """Nunca um bool solto — `status` é fresh/stale, `reasons` é a lista
    ESTRUTURADA de por quê (vazia exatamente quando `status == "fresh"`).
    """

    status: BenchmarkFreshnessStatus
    reasons: tuple[StalenessReason, ...]
    benchmark_id: (
        str | None
    )  # id do benchmark mais recente encontrado, mesmo se stale; None só se "missing"
    age_days: float | None
    population_growth_ratio: float | None


def population_growth_ratio(baseline: int, current: int) -> float:
    """`(current - baseline) / baseline`. `baseline <= 0` é o caso
    degenerado: sem população anterior, qualquer população atual > 0 é
    crescimento "infinito" (sempre stale por esse critério); 0 -> 0 não é
    crescimento algum.
    """
    if baseline <= 0:
        return 0.0 if current <= 0 else float("inf")
    return (current - baseline) / baseline
