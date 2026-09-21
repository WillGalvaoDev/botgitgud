"""T-DG.5 (docs/fase4-data-acquisition-plan.md §10) — objective inspection
of Data Acquisition Gate progress: candidate discovery/triage counts from
ingest/discovery_store.py, and — for one declared target
`(class_name, spec_name, encounter_id, difficulty, partition)` — the full
validity contract from §10.2 applied to the `logs` table, plus the §10.3
PASS/BLOCKED gate verdict.

Every SQL query here is read-only; this module never calls the WCL API
(§11 T-DG.5 acceptance criterion). `GATE_TARGET`/`TEMPORAL_MIN_PER_SIDE`
mirror the plan's own P1/P2 numbers — not tunable Settings fields, since
relaxing them would be "mudar o gate de 5.000", explicitly forbidden by
the user's approval of this round's work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from botgitgud.analysis.cohort import SANITY_BAND_PCT
from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.store import Store
from botgitgud.phase4.registry import ModelStatus, Phase4ModelRegistry
from botgitgud.phase4.target import Phase4Target

GATE_TARGET = 5000
TEMPORAL_MIN_PER_SIDE = 1000

# §10.2's ordered rejection reasons, checked in this order — the first one
# that fails is the reason recorded (a row failing partition AND percentile
# is counted once, under "partition_divergente").
_REASON_PARTITION = "partition_divergente"
_REASON_PERCENTILE = "percentile_ausente"
_REASON_NOT_KILL = "fight_nao_kill"
_REASON_DURATION_BAND = "duracao_fora_da_banda"
_REASON_FEATURE_INCOMPLETE = "feature_incompleta"


@dataclass(frozen=True, slots=True)
class CandidateGroup:
    """One row of the general (no target declared) view: a
    (class, spec, encounter, difficulty, partition) combination and how
    many triaged, killed fights have it — the census's own raw material
    for picking a pilot target (plan §7.3)."""

    class_name: str
    spec_name: str
    encounter_id: int
    difficulty: int
    partition: int | None
    n_candidates: int
    ingested: int = 0
    observations_remaining: int = GATE_TARGET
    gate_pass: bool = False
    model_status: ModelStatus = ModelStatus.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class TargetStatus:
    class_name: str
    spec_name: str
    encounter_id: int
    difficulty: int
    partition: int
    reports_discovered: int
    fights_triaged: int
    candidates: int
    ingested: int
    valid: int
    duplicates: int
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    earliest_valid_ingested_at: datetime | None = None
    latest_valid_ingested_at: datetime | None = None
    temporal_split_ok: bool = False  # P2

    @property
    def rejected(self) -> int:
        return sum(self.rejected_by_reason.values())

    @property
    def observations_remaining(self) -> int:
        return max(0, GATE_TARGET - self.valid)

    @property
    def progress_pct(self) -> float:
        return min(100.0, 100.0 * self.valid / GATE_TARGET)

    @property
    def gate_pass(self) -> bool:
        return self.valid >= GATE_TARGET and self.temporal_split_ok


def top_candidate_groups(
    store: Store, *, limit: int = 20, registry: Phase4ModelRegistry | None = None
) -> list[CandidateGroup]:
    """The general `dataset-status` view — best candidates across every
    spec/encounter/difficulty/partition discovered so far, ranked by size.
    Only fights the triage stage confirmed as kills count (a wipe is never
    a training observation).
    """
    df = store.query(
        """
        SELECT dt.class_name, dt.spec_name, df.encounter_id, df.difficulty, df.partition,
               count(*) AS n_candidates
        FROM discovery_targets dt
        JOIN discovery_fights df
          ON dt.report_code = df.report_code AND dt.fight_id = df.fight_id
        WHERE df.kill = true
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY n_candidates DESC
        """,
    )
    groups = [
        CandidateGroup(
            class_name=row["class_name"],
            spec_name=row["spec_name"],
            encounter_id=row["encounter_id"],
            difficulty=row["difficulty"],
            partition=row["partition"],
            n_candidates=row["n_candidates"],
        )
        for row in df.iter_rows(named=True)
        if classify_spec(SpecId(row["class_name"], row["spec_name"])) is SpecSupport.SUPPORTED
    ][:limit]
    records = {record.target: record for record in registry.list_all()} if registry else {}
    enriched: list[CandidateGroup] = []
    for group in groups:
        if group.partition is None:
            enriched.append(group)
            continue
        target = Phase4Target(
            SpecId(group.class_name, group.spec_name),
            group.encounter_id,
            group.difficulty,
            group.partition,
        )
        ingested_df = store.query(
            "SELECT count(DISTINCT (report_code, fight_id, player_name)) AS n FROM logs "
            "WHERE class_name=$c AND spec_name=$s AND encounter_id=$e "
            "AND difficulty=$d AND partition=$p",
            c=group.class_name,
            s=group.spec_name,
            e=group.encounter_id,
            d=group.difficulty,
            p=group.partition,
        )
        ingested = int(ingested_df["n"][0])
        record = records.get(target)
        enriched.append(
            CandidateGroup(
                class_name=group.class_name,
                spec_name=group.spec_name,
                encounter_id=group.encounter_id,
                difficulty=group.difficulty,
                partition=group.partition,
                n_candidates=group.n_candidates,
                ingested=ingested,
                observations_remaining=max(0, GATE_TARGET - ingested),
                gate_pass=ingested >= GATE_TARGET,
                model_status=record.status if record else ModelStatus.UNAVAILABLE,
            )
        )
    return enriched


def _classify_rejection(
    row: dict[str, object], *, target_partition: int, median_duration_s: float | None
) -> str | None:
    """None means valid. Checked in §10.2's order — class/spec/encounter/
    difficulty aren't re-checked here because the SQL query that produces
    `row` already filters on them (they define the candidate pool itself,
    not a rejection reason within it).
    """
    if row["partition"] != target_partition:
        return _REASON_PARTITION
    if row["percentile"] is None:
        return _REASON_PERCENTILE
    if not row["kill"]:
        return _REASON_NOT_KILL
    duration_s = row["duration_s"]
    if (
        median_duration_s is not None
        and median_duration_s > 0
        and isinstance(duration_s, int | float)
        and abs(duration_s - median_duration_s) / median_duration_s > SANITY_BAND_PCT
    ):
        return _REASON_DURATION_BAND
    if row["active_time_pct"] is None:
        return _REASON_FEATURE_INCOMPLETE
    return None


def target_status(
    store: Store,
    discovery_store: DiscoveryStore,
    *,
    class_name: str,
    spec_name: str,
    encounter_id: int,
    difficulty: int,
    partition: int,
) -> TargetStatus:
    """The specific `dataset-status --spec ... --encounter ... --difficulty
    ... --partition ...` view: applies the full §10.2 validity contract to
    every `logs` row for this class/spec/encounter/difficulty (any
    partition — a partition mismatch is itself a rejection reason to
    surface, not a pre-filter) and evaluates the §10.3 gate.
    """
    reports_discovered = discovery_store.count_reports()

    fights_df = store.query(
        "SELECT count(*) AS n FROM discovery_fights "
        "WHERE encounter_id = $e AND difficulty = $d AND partition = $p",
        e=encounter_id,
        d=difficulty,
        p=partition,
    )
    fights_triaged = int(fights_df["n"][0])

    candidates_df = store.query(
        """
        SELECT count(*) AS n
        FROM discovery_targets dt
        JOIN discovery_fights df
          ON dt.report_code = df.report_code AND dt.fight_id = df.fight_id
        WHERE dt.class_name = $c AND dt.spec_name = $s
          AND df.encounter_id = $e AND df.difficulty = $d AND df.partition = $p
          AND df.kill = true
        """,
        c=class_name,
        s=spec_name,
        e=encounter_id,
        d=difficulty,
        p=partition,
    )
    candidates = int(candidates_df["n"][0])

    total_df = store.query(
        "SELECT count(*) AS n FROM logs "
        "WHERE class_name = $c AND spec_name = $s AND encounter_id = $e AND difficulty = $d",
        c=class_name,
        s=spec_name,
        e=encounter_id,
        d=difficulty,
    )
    total_ingested_rows = int(total_df["n"][0])

    # §9.1 logs_latest: dedup by the natural key, latest ingested_at wins —
    # `logs` itself stays insert-only (D-12c), this is a query-time view.
    latest_df = store.query(
        """
        SELECT partition, percentile, kill, duration_s, active_time_pct, ingested_at
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY report_code, fight_id, player_name ORDER BY ingested_at DESC
            ) AS rn
            FROM logs
            WHERE class_name = $c AND spec_name = $s AND encounter_id = $e AND difficulty = $d
        )
        WHERE rn = 1
        """,
        c=class_name,
        s=spec_name,
        e=encounter_id,
        d=difficulty,
    )
    ingested = len(latest_df)
    duplicates = total_ingested_rows - ingested

    median_df = store.query(
        "SELECT median(duration_s) AS m FROM logs "
        "WHERE class_name = $c AND spec_name = $s AND encounter_id = $e "
        "AND difficulty = $d AND partition = $p",
        c=class_name,
        s=spec_name,
        e=encounter_id,
        d=difficulty,
        p=partition,
    )
    median_duration_s = median_df["m"][0] if len(median_df) else None

    rejected_by_reason: dict[str, int] = {}
    valid_timestamps: list[datetime] = []
    for row in latest_df.iter_rows(named=True):
        reason = _classify_rejection(
            row, target_partition=partition, median_duration_s=median_duration_s
        )
        if reason is None:
            valid_timestamps.append(row["ingested_at"])
        else:
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1

    valid_timestamps.sort()
    valid = len(valid_timestamps)
    # P2: a cutoff exists with >= TEMPORAL_MIN_PER_SIDE observations on
    # each side. With `valid_timestamps` sorted, the (TEMPORAL_MIN_PER_SIDE)-th
    # earliest timestamp is exactly that cutoff when valid >= 2*TEMPORAL_MIN_PER_SIDE.
    temporal_split_ok = valid >= 2 * TEMPORAL_MIN_PER_SIDE

    return TargetStatus(
        class_name=class_name,
        spec_name=spec_name,
        encounter_id=encounter_id,
        difficulty=difficulty,
        partition=partition,
        reports_discovered=reports_discovered,
        fights_triaged=fights_triaged,
        candidates=candidates,
        ingested=ingested,
        valid=valid,
        duplicates=duplicates,
        rejected_by_reason=rejected_by_reason,
        earliest_valid_ingested_at=valid_timestamps[0] if valid_timestamps else None,
        latest_valid_ingested_at=valid_timestamps[-1] if valid_timestamps else None,
        temporal_split_ok=temporal_split_ok,
    )
