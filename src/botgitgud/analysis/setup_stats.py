"""SA.5 — compara os secondary stat ratings do jogador
(`SetupProfile.stats`) contra as distribuições descritivas do
`EncounterBenchmark` (EB.2), para `SECONDARY_STATS`.

Mesma regra arquitetural de SA.2-SA.4: os stats do jogador NUNCA filtram a
população do benchmark; este módulo não importa nada de
`cohort.py`/`cohort_match.py`/findings da Execution Cohort.

**Valores são raw ratings, sempre.** `SetupProfile.stats` já documenta
(domain/models.py) que são ratings brutos, não convertidos para
porcentagem — este módulo preserva essa garantia: nenhuma divisão por 100,
nenhum cap inventado, nenhum stat weight, nenhuma recomendação de
reforge/gem/enchant. `CaveatCode.RAW_RATING_ONLY` acompanha todo finding
substantivo produzido aqui, sem exceção.

**Diferença estrutural de SA.2-SA.4**: aqueles comparam PREVALÊNCIA
(contagem categórica, agregável entre bandas por soma). Secondary stats
comparam uma DISTRIBUIÇÃO CONTÍNUA (`DescriptiveStats`: n/median/p25/p75)
— medianas e quartis de bandas diferentes NÃO podem ser combinados por
soma nem por média sem os valores brutos originais (que este módulo não
tem, só o já agregado por EB.2). Por isso a comparação usa UMA banda de
referência — a de percentil mais alto com dado disponível (`n>0`) para
aquele stat —, nunca uma "banda global" fabricada. Ver `_reference_band`.

**Sem posição normalizada inventada**: o `ObservationCode` (MATCHES/
DIFFERS) já comunica "dentro" ou "fora" do intervalo interquartil
observado — puramente descritivo, nunca chamado de score, nunca uma
unidade nova. `HIGH_PREVALENCE`/`LOW_PREVALENCE` (linguagem de PREVALÊNCIA
categórica) deliberadamente não são usados aqui — misturariam vocabulário
de categoria com o de distribuição contínua.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import (
    CANONICAL_SECONDARY_STATS,
    BandBenchmark,
    DescriptiveStats,
    EncounterBenchmark,
)
from botgitgud.analysis.setup_finding import (
    BenchmarkSampleRef,
    CaveatCode,
    DistributionContext,
    EvidenceLevel,
    FindingSubject,
    ObservationCode,
    SetupFinding,
    category_unavailable_finding,
    compute_evidence_level,
    compute_publicability,
    missing_benchmark_finding,
    missing_player_setup_finding,
)
from botgitgud.domain.models import SetupProfile


class SetupStatsComparisonError(ValueError):
    """Mesma convenção dos erros de comparação de SA.2-SA.4: `benchmark`
    que não pertence ao `target` informado falha fechado.
    """


@dataclass(frozen=True, slots=True)
class _ReferenceBand:
    band: PercentileBand
    band_bm: BandBenchmark
    stats: DescriptiveStats


def _reference_band(
    stat_name: str, policy: BenchmarkPolicy, benchmark: EncounterBenchmark
) -> _ReferenceBand | None:
    """A banda de percentil MAIS ALTO com `n>0` para este stat — nunca uma
    combinação entre bandas (ver docstring do módulo). Maior percentil
    primeiro porque é literalmente o propósito do Encounter Benchmark:
    "o que é observado entre strong performers" (benchmark_aggregate.py).
    `None` quando NENHUMA banda tem dado para este stat.
    """
    for band in sorted(policy.bands, key=lambda b: b.low, reverse=True):
        band_bm = benchmark.bands.get(band.name)
        if band_bm is None:
            continue
        stats = band_bm.secondary_stats.get(stat_name)
        if stats is not None and stats.n > 0:
            return _ReferenceBand(band=band, band_bm=band_bm, stats=stats)
    return None


def _compare_one_stat(
    *,
    stat_name: str,
    player_value: float,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    benchmark: EncounterBenchmark,
) -> SetupFinding:
    subject = FindingSubject.secondary_stat(stat_name)
    reference = _reference_band(stat_name, policy, benchmark)

    if reference is None:
        sample = BenchmarkSampleRef(benchmark_id=target.benchmark_id, band_name=None)
        return category_unavailable_finding(subject=subject, sample=sample)

    sample = BenchmarkSampleRef(benchmark_id=target.benchmark_id, band_name=reference.band.name)
    evidence_level = compute_evidence_level(
        n_available=reference.stats.n, min_sample_size=policy.min_sample_size
    )
    publicability = compute_publicability(evidence_level)

    caveats = [CaveatCode.OBSERVATIONAL_ONLY, CaveatCode.RAW_RATING_ONLY]
    if reference.stats.n < reference.band_bm.sample_size:
        caveats.append(CaveatCode.PARTIAL_SETUP_COVERAGE)

    if evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK):
        observation = ObservationCode.INSUFFICIENT_EVIDENCE
        actionable = False
    else:
        assert reference.stats.p25 is not None and reference.stats.p75 is not None
        within_iqr = reference.stats.p25 <= player_value <= reference.stats.p75
        if within_iqr:
            observation = ObservationCode.MATCHES_COMMON_PATTERN
            actionable = False
        else:
            observation = ObservationCode.DIFFERS_FROM_COMMON_PATTERN
            actionable = True

    return SetupFinding(
        subject=subject,
        observation=observation,
        evidence_level=evidence_level,
        publicability=publicability,
        sample=sample,
        distribution=DistributionContext(player_value=player_value, benchmark=reference.stats),
        caveats=tuple(caveats),
        actionable=actionable,
    )


def compare_secondary_stats(
    *,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    player_setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
) -> tuple[SetupFinding, ...]:
    """Sempre exatamente `len(CANONICAL_SECONDARY_STATS)` findings (hoje 4:
    Crit/Haste/Mastery/Versatility) — o vocabulário de stats é fixo e
    conhecido de antemão (diferente das categorias de SA.2-SA.4, cujo
    subject só existe se houver dado), então cada stat SEMPRE recebe seu
    próprio finding, mesmo em degradação total.
    """
    if benchmark is not None and benchmark.target != target:
        raise SetupStatsComparisonError(
            f"benchmark {benchmark.target.benchmark_id!r} does not belong to target "
            f"{target.benchmark_id!r}"
        )

    generic_sample = BenchmarkSampleRef(benchmark_id=target.benchmark_id, band_name=None)

    if player_setup is None:
        return tuple(
            missing_player_setup_finding(
                subject=FindingSubject.secondary_stat(stat_name), sample=generic_sample
            )
            for stat_name in CANONICAL_SECONDARY_STATS
        )

    if benchmark is None:
        return tuple(
            missing_benchmark_finding(
                subject=FindingSubject.secondary_stat(stat_name), sample=generic_sample
            )
            for stat_name in CANONICAL_SECONDARY_STATS
        )

    findings = []
    for stat_name in CANONICAL_SECONDARY_STATS:
        player_value = player_setup.stats.get(stat_name)
        if player_value is None:
            findings.append(
                category_unavailable_finding(
                    subject=FindingSubject.secondary_stat(stat_name), sample=generic_sample
                )
            )
            continue
        findings.append(
            _compare_one_stat(
                stat_name=stat_name,
                player_value=player_value,
                target=target,
                policy=policy,
                benchmark=benchmark,
            )
        )
    return tuple(findings)
