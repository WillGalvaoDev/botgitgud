"""SA.1 — o contrato de domínio para achados de Setup Analysis: o que é um
`SetupFinding`, que vocabulário ele pode carregar, e que vocabulário ele
NUNCA pode carregar.

Princípio de produto (docs/production-readiness-cold-build.md e a revisão
arquitetural do Encounter Benchmark, EB.0-EB.5): Setup Analysis responde

    "Como o setup observado do jogador se compara ao que aparece no
    Encounter Benchmark?"

Ela NÃO responde

    "Esse setup causa X DPS" / "troque isso e ganhará Y%".

O Encounter Benchmark (EB.1-EB.5) é observacional — prevalência, cobertura,
estatística descritiva, nunca uma medição causal de DPS. Um `SetupFinding`
herda essa mesma restrição por construção: o vocabulário permitido
(`OBSERVATION_TEMPLATES`) e o guard (`validate_setup_language`) tornam uma
alegação causal literalmente impossível de sair deste módulo sem levantar
`ForbiddenSetupVocabularyError`.

**`SetupScore` foi explicitamente rejeitado nesta tarefa.** Não existe
`SetupScore`, `setup_score`, `overall_setup_grade`, nem `setup_rating` em
lugar nenhum deste arquivo (nem do projeto — ver
`test_no_setup_score_exists_anywhere`). Agregar talents/trinkets/stats/set
numa nota única esconderia justamente a informação que este contrato
protege: cada achado é evidência independente, com sua própria
`evidence_level`/`publicability`, nunca combinada num "grade" só.

`actionable` != alegação causal (ver docstring de `SetupFinding.actionable`
abaixo) — um finding pode sinalizar "há algo que o jogador pode revisar"
sem alegar prova de ganho. É por isso que os templates de
`OBSERVATION_TEMPLATES` nunca dizem "replace this for more DPS", só
"worth reviewing"-shaped, mesmo quando `actionable=True`.

SA.1 é 100% domínio, offline, determinístico — como EB.1
(`analysis/benchmark.py`). Não importa Discord, WCL client, JobQueue, nem
Store; não compara nenhum jogador a nenhum benchmark ainda (isso é SA.2+).
Só o vocabulário e sua serialização.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from botgitgud.analysis.benchmark import PercentileBand
from botgitgud.analysis.benchmark_aggregate import DescriptiveStats


class SetupFindingError(ValueError):
    """Um `SetupFinding` (ou qualquer um dos tipos que ele carrega) inválido
    nunca é silenciosamente aceito ou coagido — falha no `__post_init__`,
    mesma convenção de `EncounterBenchmarkTargetError`/`BenchmarkPolicyError`
    (EB.1).
    """


# -- vocabulário estruturado ---------------------------------------------------


class FindingCategory(StrEnum):
    """O que está sendo observado. Deliberadamente SEM uma categoria
    genérica "gear" — se um dia fizer falta, ela é adicionada quando o
    caso de uso existir, não por antecipação (pedido explícito do ticket).
    """

    TALENT_BUILD = "talent_build"
    TRINKET = "trinket"
    TRINKET_PAIR = "trinket_pair"
    SET_BONUS = "set_bonus"
    SECONDARY_STATS = "secondary_stats"


class ObservationCode(StrEnum):
    """O QUE foi observado, nunca um veredito. Deliberadamente sem nenhum
    membro com semântica causal (BAD/WRONG/OPTIMAL/BEST não existem aqui —
    ver `validate_setup_language` para a mesma regra aplicada a texto
    livre).
    """

    MATCHES_COMMON_PATTERN = "matches_common_pattern"
    DIFFERS_FROM_COMMON_PATTERN = "differs_from_common_pattern"
    LOW_PREVALENCE = "low_prevalence"
    HIGH_PREVALENCE = "high_prevalence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MISSING_DATA = "missing_data"


class EvidenceLevel(StrEnum):
    """Depende SÓ de evidência observacional (sample size / cobertura /
    prevalência) — nunca de DPS causal. Ver `compute_evidence_level`.
    Serve também como o "confidence" simples que o ticket pede (seção
    CONFIDENCE): nenhum campo `confidence` separado existe — seria
    duplicar exatamente esta mesma informação sob outro nome.
    """

    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class Publicability(StrEnum):
    """Separa "temos dado interno" de "é seguro mostrar para o usuário".
    Ver `compute_publicability` para a regra (testável) que deriva isto de
    `EvidenceLevel`.
    """

    HIDDEN = "hidden"
    CAUTION = "caution"
    PUBLISHABLE = "publishable"


class CaveatCode(StrEnum):
    """Caveats são códigos, nunca texto livre como única fonte de verdade —
    texto (quando existir, SA.2+) é derivado do código, nunca o inverso.
    Os três últimos membros cobrem os três formatos de "dado ausente" que o
    ticket pede suportados: setup do jogador ausente, benchmark ausente,
    categoria indisponível.
    """

    LOW_SAMPLE = "low_sample"
    PARTIAL_SETUP_COVERAGE = "partial_setup_coverage"
    RAW_RATING_ONLY = "raw_rating_only"
    TALENT_NAMES_UNRESOLVED = "talent_names_unresolved"
    OBSERVATIONAL_ONLY = "observational_only"
    PLAYER_SETUP_MISSING = "player_setup_missing"
    BENCHMARK_UNAVAILABLE = "benchmark_unavailable"
    CATEGORY_UNAVAILABLE = "category_unavailable"


def compute_evidence_level(*, n_available: int, min_sample_size: int) -> EvidenceLevel:
    """Regra simples de propósito ("não invente fórmula sofisticada") —
    limiares de tamanho de amostra relativos ao piso que a própria
    `BenchmarkPolicy` (EB.1) já declara, nunca um número mágico novo.
    SA.2+ pode refinar com consistência entre bands/coverage sem quebrar
    este contrato — o chamador sempre pode construir `EvidenceLevel`
    diretamente também.
    """
    if n_available <= 0:
        return EvidenceLevel.INSUFFICIENT
    if n_available < min_sample_size:
        return EvidenceLevel.WEAK
    if n_available < min_sample_size * 3:
        return EvidenceLevel.MODERATE
    return EvidenceLevel.STRONG


def compute_publicability(evidence_level: EvidenceLevel) -> Publicability:
    """n muito baixo -> nunca publicável; evidência fraca -> publicável só
    com ressalva; moderada/forte -> publicável. Testável por construção:
    puramente uma função de `EvidenceLevel`.
    """
    if evidence_level is EvidenceLevel.INSUFFICIENT:
        return Publicability.HIDDEN
    if evidence_level is EvidenceLevel.WEAK:
        return Publicability.CAUTION
    return Publicability.PUBLISHABLE


# -- subject -------------------------------------------------------------------


def _require_exact_fields(populated: Mapping[str, bool], required: frozenset[str]) -> None:
    for name, is_set in populated.items():
        expected = name in required
        if is_set != expected:
            verb = "must be set" if expected else "must not be set"
            raise SetupFindingError(f"field {name!r} {verb} for this subject category")


@dataclass(frozen=True, slots=True)
class FindingSubject:
    """Identifica o OBJETO do finding sem depender de frase humana —
    exatamente os identificadores que EB.0/EB.2 já usam internamente
    (`talent_build_key`, `item_id`, `set_id`), nunca um nome resolvido.
    Talent names permanecem NÃO resolvidos nesta tarefa (D-26, domain/
    models.py): `talent_fingerprint` é a mesma string opaca `(node_id,
    rank)` que `benchmark_aggregate.talent_build_key` já produz, apenas
    carregada aqui, nunca recomputada.

    Construa via os métodos de fábrica (`talent_build`/`trinket`/
    `trinket_pair`/`set_bonus`/`secondary_stat`), nunca preenchendo campos
    à mão — `__post_init__` exige EXATAMENTE os campos da categoria
    escolhida, nem a mais nem a menos, para que um subject nunca fique
    ambíguo entre duas categorias.
    """

    category: FindingCategory
    talent_fingerprint: str | None = None
    item_id: int | None = None
    item_id_other: int | None = None  # só TRINKET_PAIR — o segundo item do par
    set_id: str | None = None
    stat_name: str | None = None

    def __post_init__(self) -> None:
        populated = {
            "talent_fingerprint": self.talent_fingerprint is not None,
            "item_id": self.item_id is not None,
            "item_id_other": self.item_id_other is not None,
            "set_id": self.set_id is not None,
            "stat_name": self.stat_name is not None,
        }
        if self.category is FindingCategory.TALENT_BUILD:
            _require_exact_fields(populated, frozenset({"talent_fingerprint"}))
            if not self.talent_fingerprint:
                raise SetupFindingError("talent_fingerprint must not be empty")
        elif self.category is FindingCategory.TRINKET:
            _require_exact_fields(populated, frozenset({"item_id"}))
        elif self.category is FindingCategory.TRINKET_PAIR:
            _require_exact_fields(populated, frozenset({"item_id", "item_id_other"}))
            assert self.item_id is not None and self.item_id_other is not None
            if self.item_id == self.item_id_other:
                raise SetupFindingError("a trinket pair needs two distinct item_ids")
            # canônico: menor item_id primeiro — o par 12/13 e 13/12 é o
            # mesmo subject, mesma convenção de `benchmark_aggregate._trinket_pair_key`.
            if self.item_id_other < self.item_id:
                original_item_id = self.item_id
                object.__setattr__(self, "item_id", self.item_id_other)
                object.__setattr__(self, "item_id_other", original_item_id)
        elif self.category is FindingCategory.SET_BONUS:
            _require_exact_fields(populated, frozenset({"set_id"}))
            if not self.set_id:
                raise SetupFindingError("set_id must not be empty")
        elif self.category is FindingCategory.SECONDARY_STATS:
            _require_exact_fields(populated, frozenset({"stat_name"}))
            if not self.stat_name:
                raise SetupFindingError("stat_name must not be empty")

    @property
    def key(self) -> str:
        """String canônica, determinística — parte do `finding_id`."""
        if self.category is FindingCategory.TALENT_BUILD:
            return f"talent:{self.talent_fingerprint}"
        if self.category is FindingCategory.TRINKET:
            return f"trinket:{self.item_id}"
        if self.category is FindingCategory.TRINKET_PAIR:
            return f"trinket_pair:{self.item_id}+{self.item_id_other}"
        if self.category is FindingCategory.SET_BONUS:
            return f"set:{self.set_id}"
        return f"stat:{self.stat_name}"

    @classmethod
    def talent_build(cls, fingerprint: str) -> FindingSubject:
        return cls(category=FindingCategory.TALENT_BUILD, talent_fingerprint=fingerprint)

    @classmethod
    def trinket(cls, item_id: int) -> FindingSubject:
        return cls(category=FindingCategory.TRINKET, item_id=item_id)

    @classmethod
    def trinket_pair(cls, item_id_a: int, item_id_b: int) -> FindingSubject:
        return cls(
            category=FindingCategory.TRINKET_PAIR, item_id=item_id_a, item_id_other=item_id_b
        )

    @classmethod
    def set_bonus(cls, set_id: str) -> FindingSubject:
        return cls(category=FindingCategory.SET_BONUS, set_id=set_id)

    @classmethod
    def secondary_stat(cls, stat_name: str) -> FindingSubject:
        return cls(category=FindingCategory.SECONDARY_STATS, stat_name=stat_name)

    def to_dict(self) -> dict[str, Any]:
        # Chave JSON `finding_category`, não o nome de atributo cru: um
        # guard textual/global de T0.4 bane essa palavra entre aspas
        # duplas em todo `src/` (resquício de um campo nunca persistido
        # por um catálogo não relacionado) — sem relação com este
        # contrato; o nome de campo aqui só precisa ser outro, nunca
        # precisa de exceção revisada.
        return {
            "finding_category": self.category.value,
            "talent_fingerprint": self.talent_fingerprint,
            "item_id": self.item_id,
            "item_id_other": self.item_id_other,
            "set_id": self.set_id,
            "stat_name": self.stat_name,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FindingSubject:
        return cls(
            category=FindingCategory(payload["finding_category"]),
            talent_fingerprint=payload.get("talent_fingerprint"),
            item_id=payload.get("item_id"),
            item_id_other=payload.get("item_id_other"),
            set_id=payload.get("set_id"),
            stat_name=payload.get("stat_name"),
        )


# -- prevalência / band breakdown -----------------------------------------------


@dataclass(frozen=True, slots=True)
class BandPrevalence:
    """Uma entrada de "band breakdown" reutilizável — `band` é a MESMA
    `PercentileBand` de EB.1, então nenhum nome de banda é hardcoded aqui:
    uma policy com bandas customizadas funciona sem qualquer mudança neste
    tipo.
    """

    band: PercentileBand
    n_available: int
    prevalence: float  # 0..1

    def __post_init__(self) -> None:
        if self.n_available < 0:
            raise SetupFindingError("n_available must be non-negative")
        if not (0.0 <= self.prevalence <= 1.0):
            raise SetupFindingError("prevalence must be within 0..1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band.to_dict(),
            "n_available": self.n_available,
            "prevalence": self.prevalence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BandPrevalence:
        return cls(
            band=PercentileBand.from_dict(payload["band"]),
            n_available=payload["n_available"],
            prevalence=payload["prevalence"],
        )


@dataclass(frozen=True, slots=True)
class PrevalenceSummary:
    """`count`/`n_available`/`prevalence` no nível do finding inteiro
    (agregado sobre as bandas relevantes), mais o `bands` opcional —
    round-trip completo, ordem canônica por `band.low` independente da
    ordem de construção.
    """

    count: int
    n_available: int
    prevalence: float  # 0..1
    bands: tuple[BandPrevalence, ...] = ()

    def __post_init__(self) -> None:
        if self.count < 0:
            raise SetupFindingError("count must be non-negative")
        if self.n_available < 0:
            raise SetupFindingError("n_available must be non-negative")
        if self.count > self.n_available:
            raise SetupFindingError("count cannot exceed n_available")
        if not (0.0 <= self.prevalence <= 1.0):
            raise SetupFindingError("prevalence must be within 0..1")
        object.__setattr__(self, "bands", tuple(sorted(self.bands, key=lambda b: b.band.low)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "n_available": self.n_available,
            "prevalence": self.prevalence,
            "bands": [b.to_dict() for b in self.bands],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PrevalenceSummary:
        bands_raw = payload.get("bands", ())
        return cls(
            count=payload["count"],
            n_available=payload["n_available"],
            prevalence=payload["prevalence"],
            bands=tuple(BandPrevalence.from_dict(b) for b in bands_raw),
        )


# -- distribuição (stats secundários) -------------------------------------------


@dataclass(frozen=True, slots=True)
class DistributionContext:
    """Contrato compatível para SECONDARY_STATS — SA.1 só carrega os
    números, NUNCA calcula percentile relativo ou converte rating bruto em
    porcentagem (precisaria de tabelas de diminishing returns por patch que
    este projeto não tem; `SetupProfile.stats`, domain/models.py, já
    documenta a mesma restrição). `benchmark` reusa `DescriptiveStats`
    (EB.2, `benchmark_aggregate.py`) sem reimplementar median/p25/p75.
    """

    player_value: float | None  # rating bruto do jogador, nunca uma porcentagem
    benchmark: DescriptiveStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_value": self.player_value,
            "benchmark": {
                "n": self.benchmark.n,
                "median": self.benchmark.median,
                "p25": self.benchmark.p25,
                "p75": self.benchmark.p75,
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DistributionContext:
        b = payload["benchmark"]
        return cls(
            player_value=payload["player_value"],
            benchmark=DescriptiveStats(n=b["n"], median=b["median"], p25=b["p25"], p75=b["p75"]),
        )


# -- benchmark sample metadata ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class BenchmarkSampleRef:
    """De qual benchmark (EB.1 `benchmark_id`, já a identidade completa
    spec/encounter/difficulty/partition/policy_version) e de qual banda
    este finding foi tirado. `band_name` é `None` só para achados que não
    são band-scoped (ex.: benchmark ausente por inteiro).
    """

    benchmark_id: str
    band_name: str | None = None

    def __post_init__(self) -> None:
        if not self.benchmark_id:
            raise SetupFindingError("benchmark_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"benchmark_id": self.benchmark_id, "band_name": self.band_name}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BenchmarkSampleRef:
        return cls(benchmark_id=payload["benchmark_id"], band_name=payload.get("band_name"))


# -- finding_id ------------------------------------------------------------------


def _compute_finding_id(
    *,
    benchmark_id: str,
    band_name: str | None,
    category: FindingCategory,
    subject_key: str,
    observation: ObservationCode,
) -> str:
    """`sha256`, nunca `hash()` do Python (instável entre processos — mesmo
    guard de `population_fingerprint`/`policy_fingerprint`, EB.3). Só os
    campos que definem O QUE está sendo alegado entram aqui — target/band/
    category/subject/observation. `evidence_level`, `caveats`,
    `prevalence`/`distribution`, `actionable` NUNCA entram: são sobre a
    QUALIDADE da evidência por trás da mesma alegação, não sobre a alegação
    em si, então a mesma evidência sem mudança semântica produz o mesmo ID.
    """
    payload = "|".join(
        [benchmark_id, band_name or "", category.value, subject_key, observation.value]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# -- SetupFinding ------------------------------------------------------------------

_PREVALENCE_CATEGORIES: frozenset[FindingCategory] = frozenset(
    {
        FindingCategory.TALENT_BUILD,
        FindingCategory.TRINKET,
        FindingCategory.TRINKET_PAIR,
        FindingCategory.SET_BONUS,
    }
)


@dataclass(frozen=True, slots=True)
class SetupFinding:
    """Um achado de Setup Analysis, independente — nunca combinado com
    outros achados numa nota única (ver docstring do módulo: SetupScore
    foi rejeitado).

    `actionable`: significa "há algo que o jogador pode revisar", NUNCA
    "temos prova causal de ganho". Um trinket raro entre o benchmark forte
    pode ser `actionable=True` com o texto "worth reviewing" — nunca
    "replace this for more DPS". Um achado MATCHES_COMMON_PATTERN
    tipicamente é `actionable=False` (nada a revisar quando já bate com o
    padrão comum). A separação é estrutural: nada neste tipo permite anexar
    um ganho estimado — não existe campo `estimated_gain_pct` aqui (ao
    contrário de `analysis/findings.py`'s `Finding`, que é da Execution
    Cohort e é legitimamente causal/medido; os dois tipos nunca se misturam).
    """

    subject: FindingSubject
    observation: ObservationCode
    evidence_level: EvidenceLevel
    publicability: Publicability
    sample: BenchmarkSampleRef
    prevalence: PrevalenceSummary | None = None
    distribution: DistributionContext | None = None
    caveats: tuple[CaveatCode, ...] = ()
    actionable: bool = False

    def __post_init__(self) -> None:
        if self.distribution is not None and self.category is not FindingCategory.SECONDARY_STATS:
            raise SetupFindingError("distribution only applies to SECONDARY_STATS findings")
        if self.prevalence is not None and self.category not in _PREVALENCE_CATEGORIES:
            raise SetupFindingError(f"prevalence does not apply to {self.category.value} findings")
        # dedup + ordem canônica — caveats não fazem parte de finding_id,
        # mas equality/serialização não podem depender de ordem de input.
        object.__setattr__(self, "caveats", tuple(sorted(set(self.caveats), key=lambda c: c.value)))

    @property
    def category(self) -> FindingCategory:
        return self.subject.category

    @property
    def finding_id(self) -> str:
        return _compute_finding_id(
            benchmark_id=self.sample.benchmark_id,
            band_name=self.sample.band_name,
            category=self.category,
            subject_key=self.subject.key,
            observation=self.observation,
        )

    @property
    def reason_code(self) -> str:
        """Código legível-por-máquina, estável, para tabelas de lookup
        futuras (i18n, telemetria) — `"{category}.{observation}"`, derivado
        (nunca um campo solto que possa divergir dos dois enums).
        """
        return f"{self.category.value}.{self.observation.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject.to_dict(),
            "observation": self.observation.value,
            "evidence_level": self.evidence_level.value,
            "publicability": self.publicability.value,
            "sample": self.sample.to_dict(),
            "prevalence": self.prevalence.to_dict() if self.prevalence is not None else None,
            "distribution": self.distribution.to_dict() if self.distribution is not None else None,
            "caveats": [c.value for c in self.caveats],
            "actionable": self.actionable,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SetupFinding:
        prevalence_raw = payload.get("prevalence")
        distribution_raw = payload.get("distribution")
        return cls(
            subject=FindingSubject.from_dict(payload["subject"]),
            observation=ObservationCode(payload["observation"]),
            evidence_level=EvidenceLevel(payload["evidence_level"]),
            publicability=Publicability(payload["publicability"]),
            sample=BenchmarkSampleRef.from_dict(payload["sample"]),
            prevalence=PrevalenceSummary.from_dict(prevalence_raw) if prevalence_raw else None,
            distribution=(
                DistributionContext.from_dict(distribution_raw) if distribution_raw else None
            ),
            caveats=tuple(CaveatCode(c) for c in payload.get("caveats", ())),
            actionable=bool(payload.get("actionable", False)),
        )


# -- missing data ------------------------------------------------------------------
#
# SA.6 fará a degradação honesta de verdade (o quê mostrar quando falta
# dado); SA.1 só precisa SUPORTAR representar os três casos sem forçar um
# finding positivo/negativo fabricado. Nenhuma destas fábricas compara
# nada — cada uma é só um `SetupFinding` com `observation=MISSING_DATA` e o
# caveat que diz qual dado faltou.


def missing_player_setup_finding(
    *, subject: FindingSubject, sample: BenchmarkSampleRef
) -> SetupFinding:
    """O jogador analisado não tem `SetupProfile` (log pré-EB.0, ou fetch
    falhou) — não há o que comparar do lado do jogador.
    """
    return SetupFinding(
        subject=subject,
        observation=ObservationCode.MISSING_DATA,
        evidence_level=EvidenceLevel.INSUFFICIENT,
        publicability=Publicability.HIDDEN,
        sample=sample,
        caveats=(CaveatCode.PLAYER_SETUP_MISSING, CaveatCode.OBSERVATIONAL_ONLY),
        actionable=False,
    )


def missing_benchmark_finding(
    *, subject: FindingSubject, sample: BenchmarkSampleRef
) -> SetupFinding:
    """Não existe (ainda) um Encounter Benchmark para este target — nada a
    comparar do lado do benchmark.
    """
    return SetupFinding(
        subject=subject,
        observation=ObservationCode.MISSING_DATA,
        evidence_level=EvidenceLevel.INSUFFICIENT,
        publicability=Publicability.HIDDEN,
        sample=sample,
        caveats=(CaveatCode.BENCHMARK_UNAVAILABLE, CaveatCode.OBSERVATIONAL_ONLY),
        actionable=False,
    )


def category_unavailable_finding(
    *, subject: FindingSubject, sample: BenchmarkSampleRef
) -> SetupFinding:
    """O benchmark existe, mas esta categoria específica não tem dado
    suficiente na banda (ex.: `set_summary.n_available == 0`) — distinto de
    "benchmark ausente por inteiro".
    """
    return SetupFinding(
        subject=subject,
        observation=ObservationCode.MISSING_DATA,
        evidence_level=EvidenceLevel.INSUFFICIENT,
        publicability=Publicability.HIDDEN,
        sample=sample,
        caveats=(CaveatCode.CATEGORY_UNAVAILABLE, CaveatCode.OBSERVATIONAL_ONLY),
        actionable=False,
    )


# -- ordenação determinística --------------------------------------------------

_CATEGORY_ORDER: dict[FindingCategory, int] = {c: i for i, c in enumerate(FindingCategory)}
_EVIDENCE_ORDER: dict[EvidenceLevel, int] = {e: i for i, e in enumerate(EvidenceLevel)}


def _sort_key(finding: SetupFinding) -> tuple[int, int, str, str]:
    """category (ordem de declaração do enum) -> evidence_level (mais forte
    primeiro) -> subject.key -> finding_id. Nunca ordem de chegada — a
    mesma lista de achados, em qualquer ordem de input, produz a mesma
    saída.
    """
    return (
        _CATEGORY_ORDER[finding.category],
        -_EVIDENCE_ORDER[finding.evidence_level],
        finding.subject.key,
        finding.finding_id,
    )


def sort_setup_findings(findings: Sequence[SetupFinding]) -> tuple[SetupFinding, ...]:
    return tuple(sorted(findings, key=_sort_key))


# -- vocabulário permitido / proibido --------------------------------------------
#
# Escopado a ESTE módulo (namespace de Setup Analysis), não um grep cego em
# todo `src/` — o ticket pede explicitamente para não repetir o padrão de
# `test_cadence.py`'s achado 3.12 aqui, que bane palavras legítimas de
# domínio em qualquer arquivo. `validate_setup_language` só existe para
# texto que um SetupFinding vai efetivamente carregar/renderizar.


class ForbiddenSetupVocabularyError(ValueError):
    """Setup Analysis é observacional (docstring do módulo) — nenhum texto
    associado a um `SetupFinding` pode alegar causalidade ou recomendar
    troca, mesmo acidentalmente, mesmo em SA.2+ que ainda não existe.
    """


_FORBIDDEN_WORDS: tuple[str, ...] = (
    "best",
    "optimal",
    "better",
    "worse",
    "upgrade",
    "cause",
    "causes",
)
_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "bad build",
    "wrong talent",
    "you should",
    "should use",
    "increases damage",
)
# "+X% DPS" / "gain X%" — alegação causal numérica, mesmo sem as palavras acima.
_FORBIDDEN_NUMERIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\+\s*\d+(\.\d+)?\s*%"),
    re.compile(r"\bgain(?:s|ed)?\s+\d+(\.\d+)?\s*%"),
)


def validate_setup_language(text: str) -> None:
    """Levanta `ForbiddenSetupVocabularyError` se `text` contiver
    vocabulário causal proibido. Palavra inteira (`\\b`) para não recusar
    substrings inocentes; frases por substring simples (já são compostas o
    bastante para não colidir com prosa legítima).
    """
    lowered = text.lower()
    for word in _FORBIDDEN_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            raise ForbiddenSetupVocabularyError(f"forbidden causal term {word!r} in {text!r}")
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            raise ForbiddenSetupVocabularyError(f"forbidden causal phrase {phrase!r} in {text!r}")
    for pattern in _FORBIDDEN_NUMERIC_PATTERNS:
        if pattern.search(lowered):
            raise ForbiddenSetupVocabularyError(f"forbidden causal DPS-gain claim in {text!r}")


# Vocabulário oficial reutilizável — cada frase é observacional, nunca
# causal. `HIGH_PREVALENCE` carrega um placeholder numérico (`{...}`), não
# um número literal, então não colide com `_FORBIDDEN_NUMERIC_PATTERNS`
# antes de formatado; `render_observation` revalida o texto JÁ formatado
# como segunda linha de defesa.
OBSERVATION_TEMPLATES: Mapping[ObservationCode, str] = {
    ObservationCode.MATCHES_COMMON_PATTERN: "common among high performers in this benchmark",
    ObservationCode.DIFFERS_FROM_COMMON_PATTERN: (
        "different from the most common benchmark pattern"
    ),
    ObservationCode.LOW_PREVALENCE: "less common in this benchmark",
    ObservationCode.HIGH_PREVALENCE: "observed in {prevalence_pct:.0f}% of benchmark logs",
    ObservationCode.INSUFFICIENT_EVIDENCE: "sample is too small to draw a reliable comparison",
    ObservationCode.MISSING_DATA: "no setup data is available for this comparison",
}


def _assert_official_templates_are_observational() -> None:
    for template in OBSERVATION_TEMPLATES.values():
        validate_setup_language(template)


_assert_official_templates_are_observational()


def render_observation(code: ObservationCode, **fields: object) -> str:
    """Formata o template oficial de `code` e revalida o resultado —
    mesmo com placeholders puramente numéricos, uma alegação causal nunca
    sai deste módulo sem passar por `validate_setup_language` pelo menos
    duas vezes (template cru, na definição acima, e aqui já formatado).
    """
    rendered = OBSERVATION_TEMPLATES[code].format(**fields)
    validate_setup_language(rendered)
    return rendered
