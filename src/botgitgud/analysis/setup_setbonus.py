"""SA.4 — compara as peças de set/tier equipadas do jogador
(`SetupProfile.set_pieces`) contra o `EncounterBenchmark` (EB.2), para
`SET_BONUS`.

Mesma regra arquitetural de SA.2/SA.3: as peças de set do jogador NUNCA
filtram a população do benchmark; este módulo não importa nada de
`cohort.py`/`cohort_match.py`/findings da Execution Cohort.

**Auditoria obrigatória do bug legado (`count_tier_pieces`,
`ingest/wcl_parsing.py`)**: essa função soma peças de setIDs DISTINTOS
juntas (ex.: 2 peças do set A + 1 peça do set B viram "3", perdendo a
distinção entre sets). Esse bug afeta `PlayerBuild.tier_pieces` (campo
legado usado pela Execution Cohort/T1.2) — NÃO afeta este módulo, porque
SA.4 nunca lê `count_tier_pieces`/`tier_pieces`. `EncounterBenchmark.bands[
].set_summary` (EB.2's `_set_summary`) já foi construído CORRETO desde
EB.2 — cada `SetPieceEntry` é por `set_id` distinto, nunca somado com
outro. Nenhuma correção era necessária nesta tarefa: SA.4 só precisava
evitar repetir o erro legado, o que ele faz por construção (agrupa por
`set_id`, nunca soma sets diferentes).

**Limite de dado deliberado, documentado em vez de inventado**: EB.2's
`SetSummary`/`SetPieceEntry` dá presença (`n_players` com >=1 peça de um
`set_id`) e uma soma bruta (`total_pieces`), mas NUNCA uma distribuição de
"quantos jogadores têm exatamente N peças" — não há dado para determinar
normativamente se um jogador "tem o bônus de 2 peças" ou "de 4 peças" (isso
exigiria um catálogo de itens cruzando quais peças específicas compõem
qual threshold de bônus, que este projeto não tem). Por isso este módulo
NUNCA alega 2pc/4pc: compara só presença/prevalência do `set_id` — a MESMA
forma observacional de SA.3 (comparação de itens equipados), nunca uma
taxonomia de bônus inventada.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import EncounterBenchmark
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

# set_id sentinela, NUNCA um set real — usado só como subject de findings
# de dado ausente, onde não há peça nenhuma equipada para identificar.
_UNRESOLVED_SET_ID = "unresolved"


class SetupSetBonusComparisonError(ValueError):
    """Mesma convenção dos erros de comparação de SA.2/SA.3: `benchmark`
    que não pertence ao `target` informado falha fechado.
    """


def _player_set_ids(player_setup: SetupProfile) -> tuple[str, ...]:
    """`set_id`s distintos que o jogador tem >=1 peça, ordenados
    deterministicamente (nunca ordem de `gear`/dict). Normalmente 0 ou 1
    (um jogador só usa o set do tier atual), mas o contrato não assume
    isso — cada `set_id` distinto vira seu próprio finding.
    """
    seen = {str(piece.set_id) for piece in player_setup.set_pieces}
    return tuple(sorted(seen))


def _common_pattern_key(counts: Mapping[str, int]) -> str | None:
    if not counts:
        return None
    return min(counts, key=lambda k: (-counts[k], k))


@dataclass(frozen=True, slots=True)
class _Tally:
    band: PercentileBand
    n_available: int
    count: int
    coverage_partial: bool


def _tally_band(band: PercentileBand, benchmark: EncounterBenchmark, set_id: str) -> _Tally:
    band_bm = benchmark.bands.get(band.name)
    if band_bm is None:
        return _Tally(band=band, n_available=0, count=0, coverage_partial=False)
    summary = band_bm.set_summary
    count = next((e.n_players for e in summary.entries if e.set_id == set_id), 0)
    return _Tally(
        band=band,
        n_available=summary.n_available,
        count=count,
        coverage_partial=summary.n_available < band_bm.sample_size,
    )


def _aggregate_counts(
    benchmark: EncounterBenchmark, bands: tuple[PercentileBand, ...]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for band in bands:
        band_bm = benchmark.bands.get(band.name)
        if band_bm is None:
            continue
        for entry in band_bm.set_summary.entries:
            counts[entry.set_id] = counts.get(entry.set_id, 0) + entry.n_players
    return counts


def _compare_one_set(
    *,
    set_id: str,
    sample: BenchmarkSampleRef,
    policy: BenchmarkPolicy,
    benchmark: EncounterBenchmark,
) -> SetupFinding:
    subject = FindingSubject.set_bonus(set_id)
    bands = policy.bands
    tallies = tuple(_tally_band(b, benchmark, set_id) for b in bands)
    total_available = sum(t.n_available for t in tallies)

    if total_available == 0:
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
    total_count = sum(t.count for t in tallies)
    prevalence = PrevalenceSummary(
        count=total_count,
        n_available=total_available,
        prevalence=total_count / total_available,
        bands=band_breakdown,
    )

    caveats = [CaveatCode.OBSERVATIONAL_ONLY]
    if any(t.coverage_partial for t in tallies):
        caveats.append(CaveatCode.PARTIAL_SETUP_COVERAGE)

    if evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK):
        observation = ObservationCode.INSUFFICIENT_EVIDENCE
        actionable = False
    else:
        counts = _aggregate_counts(benchmark, bands)
        common_key = _common_pattern_key(counts)
        assert common_key is not None

        if set_id == common_key:
            observation = ObservationCode.MATCHES_COMMON_PATTERN
            actionable = False
        elif total_count > 0:
            observation = ObservationCode.DIFFERS_FROM_COMMON_PATTERN
            actionable = True
        else:
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


def compare_set_bonus(
    *,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    player_setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
) -> tuple[SetupFinding, ...]:
    """Um finding `SET_BONUS` por `set_id` distinto que o jogador tem >=1
    peça equipada; um único finding sentinela quando não há peça de set
    nenhuma. Nunca alega bônus de 2pc/4pc (ver docstring do módulo).
    """
    if benchmark is not None and benchmark.target != target:
        raise SetupSetBonusComparisonError(
            f"benchmark {benchmark.target.benchmark_id!r} does not belong to target "
            f"{target.benchmark_id!r}"
        )

    sample = BenchmarkSampleRef(benchmark_id=target.benchmark_id, band_name=None)

    if player_setup is None:
        subject = FindingSubject.set_bonus(_UNRESOLVED_SET_ID)
        return (missing_player_setup_finding(subject=subject, sample=sample),)

    set_ids = _player_set_ids(player_setup)

    if benchmark is None:
        if not set_ids:
            subject = FindingSubject.set_bonus(_UNRESOLVED_SET_ID)
            return (missing_benchmark_finding(subject=subject, sample=sample),)
        return tuple(
            missing_benchmark_finding(subject=FindingSubject.set_bonus(sid), sample=sample)
            for sid in set_ids
        )

    if not set_ids:
        subject = FindingSubject.set_bonus(_UNRESOLVED_SET_ID)
        return (category_unavailable_finding(subject=subject, sample=sample),)

    return tuple(
        _compare_one_set(set_id=sid, sample=sample, policy=policy, benchmark=benchmark)
        for sid in set_ids
    )
