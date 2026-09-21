"""SA.3 — compara os trinkets equipados do jogador (`SetupProfile.trinkets`,
slots 12/13) contra o `EncounterBenchmark` (EB.2), para `TRINKET` (individual)
e `TRINKET_PAIR`.

Mesma regra arquitetural de `setup_talents.py` (SA.2), repetida aqui: os
trinkets do jogador NUNCA filtram a população do benchmark. Este módulo só
localiza o(s) item(ns) do jogador dentro da agregação já pronta de EB.2 —
nunca reagrega, nunca importa `cohort.py`/`cohort_match.py`/findings da
Execution Cohort.

`item_id` é a identidade normativa de um trinket (WCL nunca precisa de
resolução de nome para isso, ao contrário de talents) — nenhuma taxonomy
paralela de "raro"/"bom"/"ruim" é criada; um item nunca observado só
significa "not observed in this benchmark sample" (ver `setup_finding.
validate_setup_language`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import (
    BandBenchmark,
    EncounterBenchmark,
    PrevalenceDistribution,
)
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

# Item id sentinela, NUNCA um item real (todo item_id de WCL é positivo) —
# usado só como subject de findings de dado ausente, onde não há trinket
# nenhum equipado para identificar.
_UNRESOLVED_ITEM_ID = -1


class SetupTrinketComparisonError(ValueError):
    """Mesma convenção de `SetupTalentComparisonError` (SA.2): `benchmark`
    que não pertence ao `target` informado falha fechado.
    """


def _trinket_subjects(player_setup: SetupProfile) -> tuple[FindingSubject, ...]:
    """Um subject por trinket REALMENTE equipado (0, 1 ou 2); sentinela
    único quando nenhum está equipado — nunca inventa um segundo sentinela
    para representar "o segundo slot vazio" (slot vazio não é um item).
    """
    trinkets = player_setup.trinkets
    if not trinkets:
        return (FindingSubject.trinket(_UNRESOLVED_ITEM_ID),)
    return tuple(FindingSubject.trinket(g.item_id) for g in trinkets)


def _pair_subject(player_setup: SetupProfile) -> tuple[FindingSubject, str | None]:
    """`(subject, pair_key)` — `pair_key` no MESMO formato que
    `benchmark_aggregate.trinket_pair_key` produz (`"{menor}+{maior}"`),
    reutilizado sem reimplementação divergente; `None` a menos que
    EXATAMENTE 2 trinkets estejam equipados.
    """
    trinkets = player_setup.trinkets
    if len(trinkets) != 2:
        return FindingSubject.trinket_pair(_UNRESOLVED_ITEM_ID, _UNRESOLVED_ITEM_ID - 1), None
    a, b = sorted(g.item_id for g in trinkets)
    return FindingSubject.trinket_pair(a, b), f"{a}+{b}"


def _common_pattern_key(counts: Mapping[str, int]) -> str | None:
    """Idêntico em espírito a `setup_talents._common_pattern_key` — build/
    item mais comum por contagem agregada, empate resolvido pela menor key.
    Duplicado deliberadamente (3 linhas) em vez de importar um símbolo
    privado de outro módulo.
    """
    if not counts:
        return None
    return min(counts, key=lambda k: (-counts[k], k))


@dataclass(frozen=True, slots=True)
class _Tally:
    band: PercentileBand
    n_available: int
    count: int
    coverage_partial: bool


def _tally_band(
    band: PercentileBand,
    benchmark: EncounterBenchmark,
    subject_key: str,
    distribution_for: Callable[[BandBenchmark], PrevalenceDistribution],
) -> _Tally:
    band_bm = benchmark.bands.get(band.name)
    if band_bm is None:
        return _Tally(band=band, n_available=0, count=0, coverage_partial=False)
    dist = distribution_for(band_bm)
    count = next((e.n_observed for e in dist.entries if e.key == subject_key), 0)
    return _Tally(
        band=band,
        n_available=dist.n_available,
        count=count,
        coverage_partial=dist.n_available < band_bm.sample_size,
    )


def _aggregate_counts(
    benchmark: EncounterBenchmark,
    bands: tuple[PercentileBand, ...],
    distribution_for: Callable[[BandBenchmark], PrevalenceDistribution],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for band in bands:
        band_bm = benchmark.bands.get(band.name)
        if band_bm is None:
            continue
        for entry in distribution_for(band_bm).entries:
            counts[entry.key] = counts.get(entry.key, 0) + entry.n_observed
    return counts


def _compare_prevalence_subject(
    *,
    subject: FindingSubject,
    subject_key: str,
    sample: BenchmarkSampleRef,
    policy: BenchmarkPolicy,
    benchmark: EncounterBenchmark,
    distribution_for: Callable[[BandBenchmark], PrevalenceDistribution],
) -> SetupFinding:
    """Núcleo genérico de comparação observacional item-vs-benchmark —
    idêntico em forma a `setup_talents.compare_talent_build`'s corpo
    principal, parametrizado por QUAL distribuição de prevalência da banda
    ler (`trinket_prevalence` ou `trinket_pair_prevalence`), para não
    duplicar a lógica de agregação/evidence/caveats entre as duas
    categorias deste módulo.
    """
    bands = policy.bands
    tallies = tuple(_tally_band(b, benchmark, subject_key, distribution_for) for b in bands)
    total_available = sum(t.n_available for t in tallies)

    if total_available == 0:
        # "Zero disponível" != "zero observado" — categoria sem NENHUM dado
        # para comparar, nunca LOW_PREVALENCE.
        return category_unavailable_finding(subject=subject, sample=sample)

    evidence_level = compute_evidence_level(
        n_available=total_available, min_sample_size=policy.min_sample_size
    )
    publicability = compute_publicability(evidence_level)

    band_breakdown = tuple(
        BandPrevalence(
            band=t.band,
            n_available=t.n_available,
            prevalence=(t.count / t.n_available if t.n_available else 0.0),
        )
        for t in tallies
    )
    subject_total_count = sum(t.count for t in tallies)
    prevalence = PrevalenceSummary(
        count=subject_total_count,
        n_available=total_available,
        prevalence=subject_total_count / total_available,
        bands=band_breakdown,
    )

    caveats = [CaveatCode.OBSERVATIONAL_ONLY]
    if any(t.coverage_partial for t in tallies):
        caveats.append(CaveatCode.PARTIAL_SETUP_COVERAGE)

    if evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK):
        observation = ObservationCode.INSUFFICIENT_EVIDENCE
        actionable = False
    else:
        counts = _aggregate_counts(benchmark, bands, distribution_for)
        common_key = _common_pattern_key(counts)
        assert common_key is not None  # total_available>0 implica >=1 chave observada

        if subject_key == common_key:
            observation = ObservationCode.MATCHES_COMMON_PATTERN
            actionable = False
        elif subject_total_count > 0:
            observation = ObservationCode.DIFFERS_FROM_COMMON_PATTERN
            actionable = True
        else:
            # nunca observado nesta amostra — nunca uma troca recomendada,
            # só ausência de observação (ver docstring do módulo).
            observation = ObservationCode.LOW_PREVALENCE
            actionable = True

    return SetupFinding(
        subject=subject,
        observation=observation,
        evidence_level=evidence_level,
        publicability=publicability,
        sample=sample,
        prevalence=prevalence,
        caveats=tuple(caveats),
        actionable=actionable,
    )


def compare_trinkets(
    *,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    player_setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
) -> tuple[SetupFinding, ...]:
    """Compara trinkets equipados (0-2 findings `TRINKET`, um por item
    real) + o par (exatamente 1 finding `TRINKET_PAIR`) contra `benchmark`.

    Cada categoria degrada de forma independente — perder o par (< 2
    trinkets equipados) nunca impede a comparação individual do trinket
    que ESTÁ equipado, e vice-versa.
    """
    if benchmark is not None and benchmark.target != target:
        raise SetupTrinketComparisonError(
            f"benchmark {benchmark.target.benchmark_id!r} does not belong to target "
            f"{target.benchmark_id!r}"
        )

    sample = BenchmarkSampleRef(benchmark_id=target.benchmark_id, band_name=None)

    if player_setup is None:
        item_subject = FindingSubject.trinket(_UNRESOLVED_ITEM_ID)
        pair_subject = FindingSubject.trinket_pair(_UNRESOLVED_ITEM_ID, _UNRESOLVED_ITEM_ID - 1)
        return (
            missing_player_setup_finding(subject=item_subject, sample=sample),
            missing_player_setup_finding(subject=pair_subject, sample=sample),
        )

    item_subjects = _trinket_subjects(player_setup)
    pair_subject, pair_key = _pair_subject(player_setup)
    has_any_trinket = bool(player_setup.trinkets)

    if benchmark is None:
        individual = tuple(
            missing_benchmark_finding(subject=s, sample=sample) for s in item_subjects
        )
        pair_finding = missing_benchmark_finding(subject=pair_subject, sample=sample)
        return (*individual, pair_finding)

    if not has_any_trinket:
        individual = (category_unavailable_finding(subject=item_subjects[0], sample=sample),)
    else:
        individual = tuple(
            _compare_prevalence_subject(
                subject=s,
                subject_key=str(g.item_id),
                sample=sample,
                policy=policy,
                benchmark=benchmark,
                distribution_for=lambda b: b.trinket_prevalence,
            )
            for s, g in zip(item_subjects, player_setup.trinkets, strict=True)
        )

    if pair_key is not None:
        pair_finding = _compare_prevalence_subject(
            subject=pair_subject,
            subject_key=pair_key,
            sample=sample,
            policy=policy,
            benchmark=benchmark,
            distribution_for=lambda b: b.trinket_pair_prevalence,
        )
    else:
        pair_finding = category_unavailable_finding(subject=pair_subject, sample=sample)

    return (*individual, pair_finding)
