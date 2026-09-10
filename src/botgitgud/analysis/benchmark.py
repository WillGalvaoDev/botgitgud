"""EB.1 — deterministic, offline domain contracts for the Encounter
Benchmark: what identifies one benchmark population, and what policy
governs how it will eventually be built.

Ver a revisão arquitetural (docs/production-readiness-cold-build.md): a
Execution Cohort de hoje filtra candidatos pelas escolhas do PRÓPRIO
jogador analisado (cohort_match.py, covariável talent_cluster), então uma
build ruim é validada contra outros que fizeram a mesma escolha ruim. O
Encounter Benchmark existe para responder "o que quem vai bem está usando?"
de forma INDEPENDENTE do jogador analisado — mas antes de agregar um único
log (isso é EB.2+), a identidade e a política precisam existir como
contratos fechados, versionados e testáveis por si só.

EB.1 é 100% offline e determinístico: nenhuma leitura de disco, nenhuma
query WCL, nenhuma agregação real. Só o vocabulário.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from botgitgud.domain.specs import SpecId

DEFAULT_BENCHMARK_POLICY_VERSION = "v2"


class EncounterBenchmarkTargetError(ValueError):
    """Um `EncounterBenchmarkTarget` inválido nunca é silenciosamente aceito
    ou coagido — falha no `__post_init__`, antes de qualquer uso.
    """


class BenchmarkPolicyError(ValueError):
    """Uma `BenchmarkPolicy` inválida nunca é silenciosamente aceita —
    falha no `__post_init__`, antes de qualquer uso.
    """


def _canonical_identifier(value: str, *, field_name: str) -> str:
    """O mesmo contrato que `phase4/target.py`'s `Phase4Target` já aplica
    para class/spec — replicado aqui (não importado) porque EB.1 não pode
    tocar Phase 4. WCL nunca retorna pontuação nesses nomes
    ("DemonHunter", "Havoc"), então isto é um invariante real, não uma
    restrição arbitrária.
    """
    part = value.replace(" ", "")
    if not part or not part.isascii() or not part.isalnum():
        raise EncounterBenchmarkTargetError(
            f"{field_name} must contain only ASCII letters and digits"
        )
    return part


_VERSION_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _canonical_version(
    value: str, *, field_name: str, error_type: type[ValueError] = EncounterBenchmarkTargetError
) -> str:
    """Mais permissivo que `_canonical_identifier`: uma versão de policy real
    se parece com "v1" ou "1.2.0" — pontos e hífens são legítimos; o que não
    pode aparecer é qualquer coisa que quebre um caminho de arquivo (barras,
    espaço, etc.), já que o ID final é usado como caminho.

    Compartilhada por `EncounterBenchmarkTarget` (versão como componente do
    ID) e `BenchmarkPolicy` (a mesma string, como identidade da própria
    política) — `error_type` deixa cada chamador levantar o erro do seu
    próprio domínio em vez de um dos dois vazar o tipo de exceção do outro.
    """
    if not value or any(ch not in _VERSION_CHARS for ch in value):
        raise error_type(
            f"{field_name} must be a non-empty path-safe string "
            "(letters, digits, '.', '_', '-' only)"
        )
    return value


@dataclass(frozen=True, slots=True)
class EncounterBenchmarkTarget:
    """QUEM o benchmark é para, e QUAL versão das regras o construiu —
    deliberadamente nada sobre QUANTOS logs ou ONDE estão guardados (isso é
    o store do EB.2, não esta identidade).

    Campos que participam da identidade (aprovados na revisão
    arquitetural, verbatim):
        spec (class_name, spec_name), encounter_id, difficulty, partition,
        benchmark_policy_version.

    Campos explicitamente FORA da identidade, pela mesma aprovação:
        duration — controlada DENTRO de um benchmark (uma futura
            estratificação/covariável), nunca usada para bifurcar
            identidade, então duas bandas de duração nunca viram
            silenciosamente "dois" benchmarks.
        item_level — um sinal de covariável/report-only a jusante (Setup
            Analysis), nunca uma dimensão de matching aqui.
    Nenhum dos dois campos existe nesta dataclass — não há nada para
    acidentalmente entrar no ID mais tarde.
    """

    spec: SpecId
    encounter_id: int
    difficulty: int
    partition: int
    benchmark_policy_version: str = DEFAULT_BENCHMARK_POLICY_VERSION

    def __post_init__(self) -> None:
        class_name = _canonical_identifier(self.spec.class_name, field_name="class_name")
        spec_name = _canonical_identifier(self.spec.spec_name, field_name="spec_name")
        version = _canonical_version(
            self.benchmark_policy_version, field_name="benchmark_policy_version"
        )
        if self.encounter_id <= 0:
            raise EncounterBenchmarkTargetError("encounter_id must be positive")
        if self.difficulty <= 0:
            raise EncounterBenchmarkTargetError("difficulty must be positive")
        if self.partition < 0:
            raise EncounterBenchmarkTargetError("partition must be non-negative")
        object.__setattr__(self, "spec", SpecId(class_name=class_name, spec_name=spec_name))
        object.__setattr__(self, "benchmark_policy_version", version)

    @property
    def benchmark_id(self) -> str:
        """Determinístico, path-safe, reversível via `parse()`. Cada campo
        canônico já foi validado alnum/version-safe pelo `__post_init__`,
        então isto pode virar um caminho de diretório em qualquer
        filesystem sem escaping adicional — o mesmo padrão que
        `Phase4Target.target_id` já usa neste projeto, escolhido aqui em
        vez de um hash opaco porque um ID legível e re-parseável é
        exatamente o que um store baseado em arquivo (EB.2) precisa, e
        porque só um formato reversível permite o round-trip exato que este
        ticket exige.
        """
        return (
            f"{self.spec.class_name}/{self.spec.spec_name}/{self.encounter_id}/"
            f"{self.difficulty}/{self.partition}/{self.benchmark_policy_version}"
        )

    def to_dict(self) -> dict[str, str | int]:
        return {
            "class_name": self.spec.class_name,
            "spec_name": self.spec.spec_name,
            "encounter_id": self.encounter_id,
            "difficulty": self.difficulty,
            "partition": self.partition,
            "benchmark_policy_version": self.benchmark_policy_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EncounterBenchmarkTarget:
        return cls(
            spec=SpecId(payload["class_name"], payload["spec_name"]),
            encounter_id=payload["encounter_id"],
            difficulty=payload["difficulty"],
            partition=payload["partition"],
            benchmark_policy_version=payload.get(
                "benchmark_policy_version", DEFAULT_BENCHMARK_POLICY_VERSION
            ),
        )

    @classmethod
    def parse(cls, value: str) -> EncounterBenchmarkTarget:
        parts = value.split("/")
        if len(parts) != 6:
            raise EncounterBenchmarkTargetError(
                "benchmark id must have six slash-separated components"
            )
        class_name, spec_name, encounter, difficulty, partition, version = parts
        try:
            numeric = tuple(int(item) for item in (encounter, difficulty, partition))
        except ValueError as exc:
            raise EncounterBenchmarkTargetError(
                "encounter, difficulty and partition must be integers"
            ) from exc
        return cls(SpecId(class_name, spec_name), *numeric, benchmark_policy_version=version)


class DedupPolicy(StrEnum):
    """EB.2 implementa o comportamento; EB.1 só nomeia a escolha, para que
    uma `policy_version` já possa se comprometer com uma regra antes de a
    agregação existir.
    """

    ONE_LOG_PER_PLAYER = "one_log_per_player"  # um jogador não pode inflar o benchmark
    NONE = "none"


@dataclass(frozen=True, slots=True)
class PercentileBand:
    """`[low, high)` — `low` inclusivo, `high` exclusivo.

    `contains` preserva essa regra geral. O ponto 100 da policy padrão é
    tratado explicitamente por `BenchmarkPolicy.band_for`, pois 100 é o
    limite superior válido do domínio e pertence à banda final.
    """

    name: str
    low: float
    high: float

    def __post_init__(self) -> None:
        if not self.name:
            raise BenchmarkPolicyError("band name must not be empty")
        if not (0.0 <= self.low <= 100.0) or not (0.0 <= self.high <= 100.0):
            raise BenchmarkPolicyError(f"band {self.name!r} must be within 0..100")
        if self.low >= self.high:
            raise BenchmarkPolicyError(f"band {self.name!r} must have low < high")

    def contains(self, percentile: float) -> bool:
        return self.low <= percentile < self.high

    def to_dict(self) -> dict[str, str | float]:
        return {"name": self.name, "low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PercentileBand:
        return cls(name=payload["name"], low=payload["low"], high=payload["high"])


DEFAULT_BANDS: tuple[PercentileBand, ...] = (
    PercentileBand("p99-100", 99.0, 100.0),
    PercentileBand("p95-99", 95.0, 99.0),
    PercentileBand("p75-95", 75.0, 95.0),
    PercentileBand("p50-75", 50.0, 75.0),
)


@dataclass(frozen=True, slots=True)
class BenchmarkPolicy:
    """As regras que vão governar UMA construção de benchmark — versionada
    porque uma mudança em qualquer campo abaixo pode mudar quem entra no
    benchmark, e isso nunca pode sobrescrever silenciosamente um benchmark
    anterior (condição obrigatória da revisão arquitetural).

    `bands`: a estratificação por desempenho aprovada — ver `DEFAULT_BANDS`.
        Cobre 50..100; a banda final inclui explicitamente o ponto 100.
        Percentis abaixo de 50 continuam deliberadamente sem banda.
    `min_sample_size`: tamanho mínimo BÁSICO — não é a regra final de
        suficiência estatística do benchmark (isso é EB.2); só a validação
        de "a policy declara um limiar coerente". 8 espelha o mesmo piso
        que `analysis/cohort.py`'s `COHORT_MIN_HARD` já usa para a
        Execution Cohort, sem acoplar os dois módulos.
    `dedup_policy`: declarativa — ver `DedupPolicy`. Nenhuma lógica de
        deduplicação real existe ainda.
    `max_data_age_days`: SOMENTE a regra de "dado velho demais para contar
        no benchmark" — uma decisão sobre quais logs são elegíveis, que
        pertence à política de dados. Expiração/staleness de um CACHE
        (quando reconstruir) é uma preocupação do store (EB.2), não desta
        policy, e não é modelada aqui. `None` == sem limite ainda decidido.
    `duration`/`item_level`: não são campos aqui, de propósito. `duration`
        será controlada DENTRO de um benchmark (estratificação futura,
        nunca uma nova policy_version por si só). `item_level` fica como
        covariável/report-only a jusante (Setup Analysis). Nenhum dos dois
        é implementado nesta tarefa.
    """

    policy_version: str = DEFAULT_BENCHMARK_POLICY_VERSION
    bands: tuple[PercentileBand, ...] = DEFAULT_BANDS
    min_sample_size: int = 8
    dedup_policy: DedupPolicy = DedupPolicy.ONE_LOG_PER_PLAYER
    max_data_age_days: float | None = None

    def __post_init__(self) -> None:
        version = _canonical_version(
            self.policy_version, field_name="policy_version", error_type=BenchmarkPolicyError
        )
        if not self.bands:
            raise BenchmarkPolicyError("policy must declare at least one band")

        # Canônico por VALOR (ordenado por `low`), nunca por ordem de
        # construção — dois `BenchmarkPolicy` com as mesmas bandas em
        # ordens diferentes devem serializar de forma idêntica.
        ordered = tuple(sorted(self.bands, key=lambda b: b.low))
        seen_names: set[str] = set()
        for i, band in enumerate(ordered):
            if band.name in seen_names:
                raise BenchmarkPolicyError(f"duplicate band name {band.name!r}")
            seen_names.add(band.name)
            if i > 0 and band.low < ordered[i - 1].high:
                raise BenchmarkPolicyError(
                    f"bands {ordered[i - 1].name!r} and {band.name!r} overlap"
                )

        if self.min_sample_size < 1:
            raise BenchmarkPolicyError("min_sample_size must be at least 1")
        if self.max_data_age_days is not None and self.max_data_age_days <= 0:
            raise BenchmarkPolicyError("max_data_age_days must be positive when set")

        object.__setattr__(self, "policy_version", version)
        object.__setattr__(self, "bands", ordered)

    def band_for(self, percentile: float | None) -> PercentileBand | None:
        """`None` in -> `None` out: ausência de percentile (sem dado de
        rank) nunca é um erro, é um caso de negócio real. Fora de 0..100
        FALHA fechado — isso é dado malformado, não "sem banda". Dentro de
        0..100 mas sem banda (0<=p<50 com a policy padrão) é
        um resultado válido: `None`, sem exceção.
        """
        if percentile is None:
            return None
        if not (0.0 <= percentile <= 100.0):
            raise BenchmarkPolicyError(f"percentile {percentile} is out of 0..100")
        for band in self.bands:
            if band.contains(percentile):
                return band
        # ``PercentileBand`` is half-open, but 100 is a valid percentile and
        # belongs to the policy's final band when that band ends at 100.
        if percentile == 100.0:
            for band in reversed(self.bands):
                if band.high == 100.0:
                    return band
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "bands": [b.to_dict() for b in self.bands],
            "min_sample_size": self.min_sample_size,
            "dedup_policy": self.dedup_policy.value,
            "max_data_age_days": self.max_data_age_days,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BenchmarkPolicy:
        return cls(
            policy_version=payload["policy_version"],
            bands=tuple(PercentileBand.from_dict(b) for b in payload["bands"]),
            min_sample_size=payload.get("min_sample_size", 8),
            dedup_policy=DedupPolicy(
                payload.get("dedup_policy", DedupPolicy.ONE_LOG_PER_PLAYER.value)
            ),
            max_data_age_days=payload.get("max_data_age_days"),
        )

    @classmethod
    def default(cls) -> BenchmarkPolicy:
        return cls()
