"""M2.3 §7 — comparability-provenance-v1: the minimal provenance record
connecting M2.1 (reference_eligibility.py) and M2.2 (metric_population.py)
to the ledger (`cohort_match.py`) and the six contracted metrics, so N,
exclusions, relaxations and policy versions survive analysis -> contract ->
persistence. Pure summarization and canonical (de)serialization — no I/O,
no wiring into pipeline.py's control flow, no new policy. Every sequence of
ids is ordered by reference_id; every mapping has ordered keys.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from botgitgud.analysis.cohort_match import DuplicateConflictReport, HygieneReport, MatchReport
from botgitgud.analysis.measurement import damage_reference_id
from botgitgud.analysis.metric_population import MetricPopulation, MetricPopulationSet
from botgitgud.analysis.reference_eligibility import ReferenceEligibilityPopulation
from botgitgud.domain.models import PlayerLog

COMPARABILITY_PROVENANCE_VERSION = "comparability-provenance-v1"


@dataclass(frozen=True, slots=True)
class RelaxationStepSummary:
    covariate: str
    rule: str
    band_index: int | None
    band_pct: float | None
    n_before: int
    n_after: int


@dataclass(frozen=True, slots=True)
class PopulationSummary:
    kind: str
    n: int
    sufficiency: str
    member_ids: tuple[str, ...]
    final_duration_band_pct: float
    matched_covariates: tuple[str, ...]
    relaxed_covariates: tuple[str, ...]
    declared_covariates: tuple[str, ...]
    relaxation_steps: tuple[RelaxationStepSummary, ...]
    # code -> count over the dominant (first) exclusion code per id — not
    # the ids themselves, to bound the persisted size (SPEC §7.1).
    exclusion_counts: Mapping[str, int]
    declared_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricPopulationSummary:
    metric_id: str
    descriptive: PopulationSummary
    aspirational: PopulationSummary


@dataclass(frozen=True, slots=True)
class HygieneSummary:
    # M2.3 v002 §4.0/§7.1 (D-M23-07): n_input is the fetched list BEFORE the
    # quarantine stage; excluded_conflicting_duplicates/conflicting_duplicate_ids
    # come from DuplicateConflictReport, the rest from HygieneReport (the
    # stage AFTER quarantine). n_input == n_output + excluded_conflicting_
    # duplicates + excluded_self + excluded_non_kill + deduped_pull +
    # deduped_player.
    n_input: int
    n_output: int
    excluded_conflicting_duplicates: int
    conflicting_duplicate_ids: tuple[str, ...]
    excluded_self: int
    excluded_non_kill: int
    deduped_pull: int
    deduped_player: int


@dataclass(frozen=True, slots=True)
class EligibilitySummary:
    n_evaluated: int
    eligible_ids: tuple[str, ...]
    indeterminate_ids: tuple[str, ...]
    ineligible_ids: tuple[str, ...]
    excluded_reasons: Mapping[str, tuple[str, ...]]
    declared_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    state: str  # "SUFFICIENT" | "INSUFFICIENT_REFERENCES"
    member_ids: tuple[str, ...]
    n: int
    matched_covariates: tuple[str, ...]
    relaxed_covariates: tuple[str, ...]
    adjustment_covariates: tuple[str, ...]
    cohort_level: str
    accepted_ids: tuple[str, ...]
    accounting_exclusions: Mapping[str, str]
    aspirational_member_ids: tuple[str, ...]
    aspirational_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparabilityProvenance:
    provenance_version: str
    reference_eligibility_policy_version: str
    metric_population_policy_version: str
    ledger_matching_policy_version: str
    target_id: str
    hygiene: HygieneSummary
    eligibility: EligibilitySummary
    ledger: LedgerSummary
    metrics: Mapping[str, MetricPopulationSummary]


def summarize_hygiene(report: HygieneReport, quarantine: DuplicateConflictReport) -> HygieneSummary:
    """M2.3 v002 §7.1: `quarantine` is the §4.0 stage that ran on the
    fetched list BEFORE `report` (the hygiene stage) ever saw it —
    `report.n_input == quarantine.n_output` (the pipeline threads
    quarantine's output straight into `hygienic_candidates`).
    """
    return HygieneSummary(
        n_input=quarantine.n_input,
        n_output=report.n_output,
        excluded_conflicting_duplicates=quarantine.excluded_logs,
        conflicting_duplicate_ids=quarantine.conflicting_ids,
        excluded_self=report.excluded_self,
        excluded_non_kill=report.excluded_non_kill,
        deduped_pull=report.deduped_pull,
        deduped_player=report.deduped_player,
    )


def summarize_eligibility(population: ReferenceEligibilityPopulation) -> EligibilitySummary:
    return EligibilitySummary(
        n_evaluated=len(population.results),
        eligible_ids=tuple(sorted(population.eligible_ids)),
        indeterminate_ids=tuple(sorted(population.indeterminate_ids)),
        ineligible_ids=tuple(sorted(population.ineligible_ids)),
        excluded_reasons={
            rid: population.excluded_reasons[rid] for rid in sorted(population.excluded_reasons)
        },
        declared_limitations=population.declared_limitations,
    )


def _exclusion_counts(excluded_reasons: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for reasons in excluded_reasons.values():
        if reasons:
            counter[reasons[0]] += 1
    return dict(sorted(counter.items()))


def summarize_population(population: MetricPopulation) -> PopulationSummary:
    return PopulationSummary(
        kind=population.kind.value,
        n=population.n,
        sufficiency=population.sufficiency.value,
        member_ids=population.members,
        final_duration_band_pct=population.final_duration_band_pct,
        matched_covariates=tuple(c.value for c in population.matched_covariates),
        relaxed_covariates=tuple(c.value for c in population.relaxed_covariates),
        declared_covariates=tuple(c.value for c in population.declared_covariates),
        relaxation_steps=tuple(
            RelaxationStepSummary(
                covariate=step.covariate.value,
                rule=step.rule.value,
                band_index=step.band_index,
                band_pct=step.band_pct,
                n_before=step.n_before,
                n_after=step.n_after,
            )
            for step in population.relaxation_steps
        ),
        exclusion_counts=_exclusion_counts(population.excluded_reasons),
        declared_limitations=population.declared_limitations,
    )


def summarize_metric_population(
    metric_id: str, population_set: MetricPopulationSet
) -> MetricPopulationSummary:
    return MetricPopulationSummary(
        metric_id=metric_id,
        descriptive=summarize_population(population_set.descriptive),
        aspirational=summarize_population(population_set.aspirational),
    )


def summarize_metrics(
    populations: Mapping[str, MetricPopulationSet],
) -> dict[str, MetricPopulationSummary]:
    return {
        metric_id: summarize_metric_population(metric_id, populations[metric_id])
        for metric_id in sorted(populations)
    }


def summarize_ledger(
    *,
    state: str,
    members: Sequence[PlayerLog],
    match_report: MatchReport,
    accepted_ids: Sequence[str],
    accounting_exclusions: Mapping[str, str],
    aspirational_member_ids: Sequence[str],
    aspirational_limitations: Sequence[str],
) -> LedgerSummary:
    member_ids = tuple(sorted(damage_reference_id(log) for log in members))
    return LedgerSummary(
        state=state,
        member_ids=member_ids,
        n=len(member_ids),
        matched_covariates=match_report.matched,
        relaxed_covariates=match_report.relaxed,
        adjustment_covariates=match_report.adjustment_covariates,
        cohort_level=match_report.cohort_level,
        accepted_ids=tuple(sorted(accepted_ids)),
        accounting_exclusions={
            rid: accounting_exclusions[rid] for rid in sorted(accounting_exclusions)
        },
        aspirational_member_ids=tuple(sorted(aspirational_member_ids)),
        aspirational_limitations=tuple(sorted(set(aspirational_limitations))),
    )


def build_comparability_provenance(
    *,
    target_id: str,
    reference_eligibility_policy_version: str,
    metric_population_policy_version: str,
    ledger_matching_policy_version: str,
    hygiene: HygieneSummary,
    eligibility: EligibilitySummary,
    ledger: LedgerSummary,
    metrics: Mapping[str, MetricPopulationSummary],
) -> ComparabilityProvenance:
    return ComparabilityProvenance(
        provenance_version=COMPARABILITY_PROVENANCE_VERSION,
        reference_eligibility_policy_version=reference_eligibility_policy_version,
        metric_population_policy_version=metric_population_policy_version,
        ledger_matching_policy_version=ledger_matching_policy_version,
        target_id=target_id,
        hygiene=hygiene,
        eligibility=eligibility,
        ledger=ledger,
        metrics=metrics,
    )


def encode_comparability_provenance(provenance: ComparabilityProvenance) -> str:
    """SPEC §7.3: canonical serialization — sorted keys, compact separators,
    ASCII-only, no NaN/Infinity. A round-trip through `decode_...` must
    reproduce an equal (`==`) `ComparabilityProvenance`.
    """
    return json.dumps(
        asdict(provenance),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _decode_relaxation_step(data: Mapping[str, object]) -> RelaxationStepSummary:
    return RelaxationStepSummary(
        covariate=str(data["covariate"]),
        rule=str(data["rule"]),
        band_index=data["band_index"],  # type: ignore[arg-type]
        band_pct=data["band_pct"],  # type: ignore[arg-type]
        n_before=data["n_before"],  # type: ignore[arg-type]
        n_after=data["n_after"],  # type: ignore[arg-type]
    )


def _decode_population(data: Mapping[str, object]) -> PopulationSummary:
    return PopulationSummary(
        kind=str(data["kind"]),
        n=data["n"],  # type: ignore[arg-type]
        sufficiency=str(data["sufficiency"]),
        member_ids=tuple(data["member_ids"]),  # type: ignore[arg-type]
        final_duration_band_pct=data["final_duration_band_pct"],  # type: ignore[arg-type]
        matched_covariates=tuple(data["matched_covariates"]),  # type: ignore[arg-type]
        relaxed_covariates=tuple(data["relaxed_covariates"]),  # type: ignore[arg-type]
        declared_covariates=tuple(data["declared_covariates"]),  # type: ignore[arg-type]
        relaxation_steps=tuple(
            _decode_relaxation_step(step)
            for step in data["relaxation_steps"]  # type: ignore[union-attr]
        ),
        exclusion_counts=dict(data["exclusion_counts"]),  # type: ignore[arg-type]
        declared_limitations=tuple(data["declared_limitations"]),  # type: ignore[arg-type]
    )


def decode_comparability_provenance(payload: str) -> ComparabilityProvenance:
    """Inverse of `encode_comparability_provenance`. Raises `ValueError` for
    any version other than `COMPARABILITY_PROVENANCE_VERSION` — SPEC §7.3
    requires a legacy/unknown version to be handled explicitly by the
    caller (RunManifest reads it as unknown), never silently reinterpreted.
    """
    data = json.loads(payload)
    version = data.get("provenance_version")
    if version != COMPARABILITY_PROVENANCE_VERSION:
        raise ValueError(f"unsupported comparability provenance version: {version!r}")

    metrics = {
        metric_id: MetricPopulationSummary(
            metric_id=entry["metric_id"],
            descriptive=_decode_population(entry["descriptive"]),
            aspirational=_decode_population(entry["aspirational"]),
        )
        for metric_id, entry in data["metrics"].items()
    }
    hygiene_data = data["hygiene"]
    eligibility_data = data["eligibility"]
    ledger_data = data["ledger"]
    return ComparabilityProvenance(
        provenance_version=data["provenance_version"],
        reference_eligibility_policy_version=data["reference_eligibility_policy_version"],
        metric_population_policy_version=data["metric_population_policy_version"],
        ledger_matching_policy_version=data["ledger_matching_policy_version"],
        target_id=data["target_id"],
        hygiene=HygieneSummary(
            n_input=hygiene_data["n_input"],
            n_output=hygiene_data["n_output"],
            excluded_conflicting_duplicates=hygiene_data["excluded_conflicting_duplicates"],
            conflicting_duplicate_ids=tuple(hygiene_data["conflicting_duplicate_ids"]),
            excluded_self=hygiene_data["excluded_self"],
            excluded_non_kill=hygiene_data["excluded_non_kill"],
            deduped_pull=hygiene_data["deduped_pull"],
            deduped_player=hygiene_data["deduped_player"],
        ),
        eligibility=EligibilitySummary(
            n_evaluated=eligibility_data["n_evaluated"],
            eligible_ids=tuple(eligibility_data["eligible_ids"]),
            indeterminate_ids=tuple(eligibility_data["indeterminate_ids"]),
            ineligible_ids=tuple(eligibility_data["ineligible_ids"]),
            excluded_reasons={
                rid: tuple(reasons) for rid, reasons in eligibility_data["excluded_reasons"].items()
            },
            declared_limitations=tuple(eligibility_data["declared_limitations"]),
        ),
        ledger=LedgerSummary(
            state=ledger_data["state"],
            member_ids=tuple(ledger_data["member_ids"]),
            n=ledger_data["n"],
            matched_covariates=tuple(ledger_data["matched_covariates"]),
            relaxed_covariates=tuple(ledger_data["relaxed_covariates"]),
            adjustment_covariates=tuple(ledger_data["adjustment_covariates"]),
            cohort_level=ledger_data["cohort_level"],
            accepted_ids=tuple(ledger_data["accepted_ids"]),
            accounting_exclusions=dict(ledger_data["accounting_exclusions"]),
            aspirational_member_ids=tuple(ledger_data["aspirational_member_ids"]),
            aspirational_limitations=tuple(ledger_data["aspirational_limitations"]),
        ),
        metrics=metrics,
    )
