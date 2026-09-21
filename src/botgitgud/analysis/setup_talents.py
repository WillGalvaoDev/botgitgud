"""SA.2 — compara o `SetupProfile.talents` do jogador analisado contra o
`EncounterBenchmark` (EB.2), exclusivamente para `TALENT_BUILD`.

Regra arquitetural mais importante deste módulo (repetida aqui de propósito
porque é o motivo do Encounter Benchmark existir): **o talent build do
jogador NÃO filtra a população do benchmark.** O benchmark agregado por
`build_encounter_benchmark` (EB.2) já contém TODOS os builds observados,
independente de qual jogador está sendo analisado — `compare_talent_build`
só LÊ essa agregação e localiza o build do jogador dentro dela; nunca
reagrega, nunca filtra `EncounterBenchmark.bands` pelo build do jogador.
Fazer isso reintroduziria a circularidade que a Execution Cohort tinha
(cohort_match.py's `talent_cluster`, onde uma build ruim valida a si mesma
contra outros que fizeram a mesma escolha) — ver
docs/production-readiness-cold-build.md. Por isso este módulo NUNCA importa
`analysis/cohort.py`/`cohort_match.py`/findings da Execution Cohort.

SA.2 responde perguntas observacionais (o build do jogador aparece? com que
prevalência? como isso se distribui pelas bandas? qual é o padrão mais
comum? o jogador bate com esse padrão? há evidência suficiente?) — nunca
"qual build é melhor", "qual dá mais DPS", "o que trocar". Não existe
`estimated_gain_pct` neste módulo, pelo mesmo motivo documentado em
`setup_finding.py`.

Talent names permanecem NÃO resolvidos (D-26, domain/models.py) — este
módulo trabalha só com a identidade `(node_id, rank)` que
`benchmark_aggregate.talent_build_key` (EB.2) já produz, reutilizada sem
reimplementação e sem mudança de semântica.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import EncounterBenchmark, talent_build_key
from botgitgud.analysis.setup_finding import (
    BandPrevalence,
    BenchmarkSampleRef,
    CaveatCode,
    EvidenceLevel,
    FindingSubject,
    ObservationCode,
    PrevalenceSummary,
    SetupFinding,
    category_unavailable_finding,
    compute_evidence_level,
    compute_publicability,
    missing_benchmark_finding,
    missing_player_setup_finding,
)
from botgitgud.domain.models import SetupProfile


class SetupTalentComparisonError(ValueError):
    """Uma chamada inconsistente a `compare_talent_build` — hoje só o caso
    de `benchmark` não pertencer ao `target` informado — falha fechado.
    Nunca compara silenciosamente contra um benchmark de outro target.
    """


# Fingerprint sentinela, NUNCA um build real: usado apenas como `subject`
# de um finding de dado ausente, onde não há build nenhum para identificar
# (setup do jogador ausente, ou talents ausentes/vazios). Documentado aqui
# para nunca ser confundido com uma chave real de `talent_build_key`.
_UNRESOLVED_BUILD_SUBJECT = "unresolved"


def _subject_for_player(player_setup: SetupProfile) -> tuple[FindingSubject, str | None]:
    """`player_setup` existe, mas pode não ter talents utilizáveis —
    devolve o subject certo e a `player_key` (`None` quando não há talents).
    """
    player_key = talent_build_key(player_setup)
    if player_key is None:
        return FindingSubject.talent_build(_UNRESOLVED_BUILD_SUBJECT), None
    return FindingSubject.talent_build(player_key), player_key


def _common_pattern_key(counts: Mapping[str, int]) -> str | None:
    """Build mais comum por CONTAGEM agregada — nunca por ordem de chegada,
    ordem de dict, ordem de set, ou `hash()`. Empate: menor `key` (string)
    vence, sempre — o mesmo empate produz o mesmo vencedor não importa a
    ordem em que `counts` foi construído, porque `min()` compara todos os
    itens contra o MESMO critério `(-count, key)`.
    """
    if not counts:
        return None
    return min(counts, key=lambda k: (-counts[k], k))


@dataclass(frozen=True, slots=True)
class _BandTally:
    band: PercentileBand
    n_available: int
    player_count: int
    coverage_partial: bool


def _tally_band(band: PercentileBand, benchmark: EncounterBenchmark, player_key: str) -> _BandTally:
    band_bm = benchmark.bands.get(band.name)
    if band_bm is None:
        # Defensivo: só acontece se `policy` não é a mesma que construiu
        # `benchmark` — EB.2's `build_encounter_benchmark` sempre povoa
        # TODAS as bandas de `policy.bands`, mesmo com sample_size=0.
        return _BandTally(band=band, n_available=0, player_count=0, coverage_partial=False)

    dist = band_bm.talent_build_prevalence
    player_count = next((e.n_observed for e in dist.entries if e.key == player_key), 0)
    coverage_partial = dist.n_available < band_bm.sample_size
    return _BandTally(
        band=band,
        n_available=dist.n_available,
        player_count=player_count,
        coverage_partial=coverage_partial,
    )


def _aggregate_counts(
    benchmark: EncounterBenchmark, bands: tuple[PercentileBand, ...]
) -> dict[str, int]:
    """Contagem agregada POR BUILD, somando `n_observed` de TODAS as bandas
    — nunca uma média de prevalências (pesos desiguais entre bandas com
    denominadores diferentes seriam distorcidos). Cobre a população
    INTEIRA do benchmark, nunca filtrada pelo build de nenhum jogador.
    """
    counts: dict[str, int] = {}
    for band in bands:
        band_bm = benchmark.bands.get(band.name)
        if band_bm is None:
            continue
        for entry in band_bm.talent_build_prevalence.entries:
            counts[entry.key] = counts.get(entry.key, 0) + entry.n_observed
    return counts


def compare_talent_build(
    *,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    player_setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
) -> tuple[SetupFinding, ...]:
    """Compara `player_setup.talents` contra `benchmark` para `target`/
    `policy` — sempre exatamente UM finding (nunca um por banda; a banda
    aparece só como `PrevalenceSummary.bands` dentro dele).

    `target` é sempre exigido (identidade estável mesmo quando `benchmark`
    ainda não existe) — `benchmark` pode ser `None` (ainda não construído).
    Quando `benchmark` é dado, precisa pertencer a `target` (senão
    `SetupTalentComparisonError`) — nunca compara contra o benchmark
    errado silenciosamente.
    """
    if benchmark is not None and benchmark.target != target:
        raise SetupTalentComparisonError(
            f"benchmark {benchmark.target.benchmark_id!r} does not belong to target "
            f"{target.benchmark_id!r}"
        )

    sample = BenchmarkSampleRef(benchmark_id=target.benchmark_id, band_name=None)

    if player_setup is None:
        subject = FindingSubject.talent_build(_UNRESOLVED_BUILD_SUBJECT)
        return (missing_player_setup_finding(subject=subject, sample=sample),)

    if benchmark is None:
        subject, _ = _subject_for_player(player_setup)
        return (missing_benchmark_finding(subject=subject, sample=sample),)

    subject, player_key = _subject_for_player(player_setup)
    if player_key is None:
        return (category_unavailable_finding(subject=subject, sample=sample),)

    bands = policy.bands  # já canônico: ordenado por `low`, EB.1's __post_init__
    tallies = tuple(_tally_band(band, benchmark, player_key) for band in bands)

    total_available = sum(t.n_available for t in tallies)
    if total_available == 0:
        # "Zero disponível" != "zero observado" (ticket, seção EMPTY TALENT
        # BENCHMARK): o benchmark existe, mas não há NENHUM dado de talent
        # build para comparar — é indisponibilidade de categoria, nunca
        # LOW_PREVALENCE (que exigiria haver denominador para comparar contra).
        return (category_unavailable_finding(subject=subject, sample=sample),)

    evidence_level = compute_evidence_level(
        n_available=total_available, min_sample_size=policy.min_sample_size
    )
    publicability = compute_publicability(evidence_level)

    band_breakdown = tuple(
        BandPrevalence(
            band=t.band,
            n_available=t.n_available,
            prevalence=(t.player_count / t.n_available if t.n_available else 0.0),
        )
        for t in tallies
    )
    player_total_count = sum(t.player_count for t in tallies)
    prevalence = PrevalenceSummary(
        count=player_total_count,
        n_available=total_available,
        prevalence=player_total_count / total_available,
        bands=band_breakdown,
    )

    caveats = [CaveatCode.OBSERVATIONAL_ONLY, CaveatCode.TALENT_NAMES_UNRESOLVED]
    if any(t.coverage_partial for t in tallies):
        caveats.append(CaveatCode.PARTIAL_SETUP_COVERAGE)

    if evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK):
        # "Evidência insuficiente" (ticket) cobre AMBOS os tiers fracos de
        # `EvidenceLevel` — não só o denominador zero (já tratado acima
        # como CATEGORY_UNAVAILABLE). Nenhuma alegação de match/diferença/
        # baixa prevalência é feita sem amostra que sustente a comparação.
        observation = ObservationCode.INSUFFICIENT_EVIDENCE
        actionable = False
    else:
        counts = _aggregate_counts(benchmark, bands)
        common_key = _common_pattern_key(counts)
        assert common_key is not None  # total_available>0 implica >=1 build com n_observed>0

        if player_key == common_key:
            observation = ObservationCode.MATCHES_COMMON_PATTERN
            actionable = False
        elif player_total_count > 0:
            # Build observado, mas não o padrão mais comum — mesmo com
            # prevalência baixa (ex.: 20/60), continua DIFFERS, nunca
            # LOW_PREVALENCE: "0 ocorrências" é o único gatilho de
            # LOW_PREVALENCE (ver docstring do módulo e ticket).
            observation = ObservationCode.DIFFERS_FROM_COMMON_PATTERN
            actionable = True
        else:
            # player_total_count == 0: build não observado nesta amostra —
            # NUNCA "build ruim"/"build inválido", só ausência de observação.
            observation = ObservationCode.LOW_PREVALENCE
            actionable = True

    finding = SetupFinding(
        subject=subject,
        observation=observation,
        evidence_level=evidence_level,
        publicability=publicability,
        sample=sample,
        prevalence=prevalence,
        caveats=tuple(caveats),
        actionable=actionable,
    )
    return (finding,)
