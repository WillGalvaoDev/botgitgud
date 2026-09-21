"""T1.2 — domain dataclasses, replacing the long return-tuples used
throughout Fase 0 (achado 4.7). No function in analysis/ should accept or
return a raw dict or tuple — only these types.

See docs/desvios.md D-11: CohortCriteria lives here (not in T1.5, where
docs/implementacao.md's own pseudocode introduces it) because T1.2's own
Cohort.criteria field needs the type to already exist, and T1.5
chronologically comes after T1.2 in task order despite being its
dependency here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from botgitgud.domain.damage_scope import DamageScopeVersion

PhaseKey = tuple[int, int]  # (phase_id, occurrence) — see PhaseInterval


class CollectionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CollectionProvenance:
    status: CollectionStatus
    reasons: tuple[str, ...] = ()
    requested_start_ms: float | None = None
    requested_end_ms: float | None = None

    def __post_init__(self) -> None:
        if self.status is CollectionStatus.COMPLETE and self.reasons:
            raise ValueError("complete collection cannot have reasons")
        if self.status is not CollectionStatus.COMPLETE and not self.reasons:
            raise ValueError("incomplete collection requires a reason")


@dataclass(frozen=True, slots=True)
class EventMix:
    count: int
    damage: float

    def __post_init__(self) -> None:
        if self.count < 0 or not math.isfinite(self.damage) or self.damage < 0:
            raise ValueError("invalid event mix")


@dataclass(frozen=True, slots=True)
class MeasurementProvenance:
    schema_version: str = "measurement-input-v1"
    damage_collection: CollectionProvenance = field(
        default_factory=lambda: CollectionProvenance(CollectionStatus.UNKNOWN, ("missing",))
    )
    casts_collection: CollectionProvenance = field(
        default_factory=lambda: CollectionProvenance(CollectionStatus.UNKNOWN, ("missing",))
    )
    damage_table_total: float | None = None
    player_actor_id: int | None = None
    pet_actor_ids: tuple[int, ...] = ()
    damage_event_mix_by_spell: Mapping[int, Mapping[str, EventMix]] = field(default_factory=dict)
    damage_reconciliation_status: str = "UNKNOWN"
    damage_reconciliation_residual: float | None = None
    targets_per_cast_status: str = "UNKNOWN"
    targets_per_cast_reasons: tuple[str, ...] = ("CAST_INSTANCE_LINK_UNAVAILABLE",)

    def __post_init__(self) -> None:
        if self.schema_version != "measurement-input-v1":
            raise ValueError("unsupported measurement provenance schema")
        if self.damage_table_total is not None and (
            not math.isfinite(self.damage_table_total) or self.damage_table_total < 0
        ):
            raise ValueError("invalid damage table total")


@dataclass(frozen=True, slots=True)
class PhaseInterval:
    """T2.4: one occurrence of one phase. `phase_id` alone is NOT a valid
    key — WCL's phases repeat in a cycle (docs/schema_confirmado.md §7),
    so the 1st and 3rd occurrence of phase_id=1 are different intervals.
    Lives here, not analysis/phases.py (which derives/looks these up), so
    FightRef/PlayerLog — domain/'s own types — can reference it without
    domain/ depending on analysis/ (analysis/ already depends on domain/,
    never the other way).
    """

    phase_id: int
    occurrence: int  # 0-based: how many times this phase_id was seen before this one
    start_ms: float
    end_ms: float

    @property
    def key(self) -> PhaseKey:
        return (self.phase_id, self.occurrence)


@dataclass(frozen=True, slots=True)
class FightRef:
    report_code: str
    fight_id: int
    encounter_id: int
    boss_name: str
    difficulty: int
    duration_s: float
    kill: bool
    partition: int | None = None  # zone/patch metadata; see docs/desvios.md D-12(a)
    # T2.4: always populated by ingest (single fallback interval spanning
    # the whole fight for phase-less encounters) — never empty in a
    # PlayerLog that came from LogFetcher.
    phase_intervals: tuple[PhaseInterval, ...] = ()


TRINKET_SLOTS: tuple[int, int] = (12, 13)


@dataclass(frozen=True, slots=True)
class GearPiece:
    """EB.0: one equipped item, as `combatantInfo.gear[]` reports it.

    `set_id` is non-null exactly for the raid's current tier-set pieces
    (docs/schema_confirmado.md §4) — the same signal `count_tier_pieces`
    already collapses into a bare count, kept here per-slot so a future
    benchmark can say WHICH pieces, not just how many.

    Empty slots (WCL reports `id: 0`) are never materialized as GearPiece.
    """

    slot: int
    item_id: int
    item_level: float | None = None
    set_id: int | None = None


@dataclass(frozen=True, slots=True)
class TalentNode:
    """EB.0: one talent-tree pick. `(node_id, rank)` is the pair T2.2's
    Jaccard clustering already uses; `spell_id` is `talentTree[].id`, kept
    because it is the only bridge from a talent to an observable cast.

    docs/desvios.md D-26: `spell_id` does NOT resolve through
    `gameData.ability` and there is no talent-name catalog anywhere in this
    project — it is an identity, never a display name.
    """

    node_id: int
    rank: int
    spell_id: int | None = None


@dataclass(frozen=True, slots=True)
class SetupProfile:
    """EB.0: everything the player CHOSE before the pull, as opposed to how
    they executed. Parsed from the `combatantInfo` that
    `QUERY_PLAYER_META` already fetches — zero additional API cost.

    Exists so the Encounter Benchmark can ask "what are high performers
    using?" independently of the analyzed player's own choices; see
    docs/production-readiness-cold-build.md and the architecture review for
    why deriving that from the player's own matched cohort is circular.

    `stats` holds the LOWEST observed value per stat (`stats.<name>.min`),
    the closest available proxy for the gear-derived baseline. It is NOT
    armory-equivalent: a raid buff active for the whole fight is included,
    and these are raw ratings, not percentages — converting to % needs
    per-patch diminishing-returns tables this project does not have.
    """

    talents: tuple[TalentNode, ...] = ()
    gear: tuple[GearPiece, ...] = ()
    stats: Mapping[str, float] = field(default_factory=dict)

    @property
    def trinkets(self) -> tuple[GearPiece, ...]:
        """Slots 12/13, ascending. May be shorter than 2 — a missing trinket
        is reported as missing, never padded with a placeholder.
        """
        worn = (g for g in self.gear if g.slot in TRINKET_SLOTS)
        return tuple(sorted(worn, key=lambda g: g.slot))

    @property
    def set_pieces(self) -> tuple[GearPiece, ...]:
        return tuple(g for g in self.gear if g.set_id is not None)


@dataclass(frozen=True, slots=True)
class PlayerBuild:
    character_name: str
    server: str | None
    class_name: str
    spec_name: str
    role: Literal["dps", "healer", "tank"]  # detected to REJECT (T0.9), not to branch
    item_level: float | None
    talent_hash: str | None
    tier_pieces: int | None
    external_buffs: frozenset[int] = frozenset()  # spell_ids of received external buffs (T2.1)
    has_augmentation: bool = False  # an Augmentation Evoker buffed this player (T2.1)
    talent_pairs: frozenset[tuple[int, int]] = frozenset()  # (nodeID, rank) set (T2.2)
    # EB.0: None means "this log predates setup capture", NOT "this player
    # had no setup" — the 977 logs cached before EB.0 read back this way,
    # and every consumer must degrade honestly instead of inferring.
    setup: SetupProfile | None = None


@dataclass(frozen=True, slots=True)
class AbilitySourceDamage:
    source_id: int
    total: float
    hits: int


@dataclass(frozen=True, slots=True)
class AbilityDamage:
    spell_id: int
    total: float
    hits: int
    casts: int
    by_source: Mapping[int, AbilitySourceDamage] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuraBand:
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class AuraDetail:
    total_uses: int
    bands: tuple[AuraBand, ...]


@dataclass(frozen=True, slots=True)
class PlayerLog:
    fight: FightRef
    build: PlayerBuild
    dps: float | None
    percentile: float | None
    cast_timeline: Mapping[int, tuple[float, ...]]  # spell_id -> sorted times
    active_time_pct: float | None = None
    damage_by_ability: Mapping[int, AbilityDamage] = field(default_factory=dict)
    uptimes: Mapping[int, float] = field(default_factory=dict)
    resource_waste: Mapping[str, float] = field(default_factory=dict)
    resource_waste_by_ability: Mapping[int, Mapping[int, float]] = field(default_factory=dict)
    aura_details: Mapping[int, AuraDetail] = field(default_factory=dict)
    deaths: int = 0
    # T3.1: seconds spent dead — from each Summary.deathEvents entry to
    # whichever comes first, this player's next own cast or fight end (no
    # API exposes an explicit revive timestamp; see ingest/wcl_parsing.py's
    # compute_downtime_s).
    downtime_s: float = 0.0
    # T3.1: spell_id -> distinct targets hit / that spell's own cast count
    # (0.0 when casts == 0, e.g. a pure pet-cast ability never in this
    # player's own cast_timeline) — feeds T3.2's "few targets hit" diagnosis.
    avg_targets_per_cast: Mapping[int, float] = field(default_factory=dict)
    # T2.4: spell_id -> {(phase_id, occurrence): sorted times relative to
    # THAT interval's own start} — cast_timeline above stays flat/relative
    # to fight start, unchanged, for every pre-T2.4 consumer.
    phase_cast_timeline: Mapping[int, Mapping[PhaseKey, tuple[float, ...]]] = field(
        default_factory=dict
    )
    # M22: historical rows omit this field and therefore retain their
    # original, explicitly versioned semantics.
    damage_scope: DamageScopeVersion = DamageScopeVersion.LEGACY_UNSCOPED
    # WCL support attribution is a term of the V1 aggregate, not an
    # ability. It is always zero for legacy/unreconciled logs: applying the
    # term without the target scope would create an undeclared third
    # population semantics.
    support_subtracted_damage: float = 0.0
    measurement_provenance: MeasurementProvenance | None = None


# EC.2/M4: a política histórica v1 coexiste com a v2 sem invalidar caches;
# a identidade retrocompatível está em `CohortCriteria.cohort_id()`.
DEFAULT_MATCHING_POLICY_VERSION = "v2"


@dataclass(frozen=True, slots=True)
class CohortCriteria:
    """T1.5: deterministic identity for a cohort — every field that defines
    "what counts as comparable" for a reference-log query.

    `matching_policy_version` (EC.2): versiona a POLÍTICA de matching em
    si (quais campos contam como critério de inclusão), separado de
    qualquer campo individual mudar de valor. Default `"v2"`; a política
    histórica v1 continua endereçável explicitamente.
    """

    encounter_id: int
    difficulty: int
    partition: int
    class_name: str
    spec_name: str
    metric: str
    duration_min_s: float
    duration_max_s: float
    ilvl_min: float | None = None
    ilvl_max: float | None = None
    talent_cluster: str | None = None
    matching_policy_version: str = DEFAULT_MATCHING_POLICY_VERSION

    def cohort_id(self) -> str:
        """`sha256`, 16 hex chars — mesmo formato de antes do EC.2.

        Compatibilidade retroativa DELIBERADA: para `matching_policy_version
        == "v1"`, o campo é removido do
        payload ANTES de hashear, então o `cohort_id()` de uma criteria v1
        é BYTE-A-BYTE idêntico ao que este método produzia antes deste
        campo existir — nenhum cache/pool/parquet v1 existente fica
        órfão, nenhuma reinterpretação silenciosa. Qualquer OUTRA versão
        (v2+) inclui o campo no payload, então a MESMA combinação de
        outros campos sob uma política diferente produz um `cohort_id`
        genuinely diferente — v1 e v2 nunca colidem nem se confundem.
        """
        payload = asdict(self)
        # v1 predates this field, so its serialized identity always omits it.
        # This must not depend on which policy is currently the live default.
        if self.matching_policy_version == "v1":
            del payload["matching_policy_version"]
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload_json.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Cohort:
    cohort_id: str  # deterministic hash of the covariates — see CohortCriteria.cohort_id()
    criteria: CohortCriteria
    members: tuple[PlayerLog, ...]
    built_at: datetime


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    """T1.6: one characterRankings leaderboard entry, resolved to a
    report/fight/player identity fetchable via LogFetcher. Lives here
    (not ingest/rankings.py, which used to define it) so T2.1's
    ingest/store.py can persist a candidate pool per cohort_id without
    importing ingest/rankings.py — that would cycle back through
    ingest/log_fetcher.py, which already imports ingest/store.py.
    """

    report_code: str
    fight_id: int
    player_name: str
    duration_s: float


@dataclass(frozen=True, slots=True)
class SpellProfile:
    """T1.3 (docs/desvios.md D-12(b)): one spell's aggregate stats across a
    cohort — formalizes the shape build_cd_reference_profile() (Fase 0,
    bot.py) already produces ad-hoc as a dict.
    """

    spell_id: int
    presence: float
    ref_times: tuple[float, ...]
    n_usages_median: float
    # T2.3: raw per-slot reference times, index-aligned with ref_times —
    # slot_ref_times[i] is every log's observed time at ref_times[i]'s
    # position, the empirical distribution grading/bootstrap CI need
    # (ref_times[i] is just that distribution's median).
    slot_ref_times: tuple[tuple[float, ...], ...] = ()
    # T2.4: the same ref_times/slot_ref_times shape, but partitioned by
    # (phase_id, occurrence) first — each key's times are relative to
    # THAT interval's own start, never mixed with another phase or
    # occurrence (achado 3.2, rotação-fantasma).
    phase_ref_times: Mapping[PhaseKey, tuple[float, ...]] = field(default_factory=dict)
    phase_slot_ref_times: Mapping[PhaseKey, tuple[tuple[float, ...], ...]] = field(
        default_factory=dict
    )
    # EC.1: tamanho ABSOLUTO do subgrupo do cohort que realmente lançou
    # este spell (o numerador de `presence`, exposto separadamente) —
    # nunca o cohort inteiro. Uma vez que o cohort deixar de ser
    # homogêneo por build (EC.3), `presence` (uma razão sobre o cohort
    # MISTURADO) pode ficar baixa para um spell de build minoritário
    # mesmo sendo usado de forma consistente por quem tem acesso a ele —
    # `n_with_spell` permite `cadence.is_eligible` reconhecer essa
    # evidência absoluta em vez de deixar o spell desaparecer só pela
    # razão global. Default 0 preserva o comportamento de qualquer
    # `SpellProfile` construído sem este campo (testes existentes, etc.).
    n_with_spell: int = 0
    n_positional_with_spell: int | None = None


@dataclass(frozen=True, slots=True)
class RunManifest:
    """T1.5: every rendered report and every persisted cohort carries this,
    so any result can be traced back to the exact cohort, code, and
    settings that produced it (docs/implementacao.md T1.5).
    """

    cohort_id: str
    code_version: str
    generated_at: datetime
    n_members: int
    wcl_partition: int | None
    settings_hash: str
    measurement_input_version: str = "unknown"
    damage_comparison_version: str = "unknown"
    reference_n_quantitative: int = 0
