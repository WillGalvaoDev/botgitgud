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
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

PhaseKey = tuple[int, int]  # (phase_id, occurrence) — see PhaseInterval


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


@dataclass(frozen=True, slots=True)
class AbilityDamage:
    spell_id: int
    total: float
    hits: int
    casts: int


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
    deaths: int = 0
    # T2.4: spell_id -> {(phase_id, occurrence): sorted times relative to
    # THAT interval's own start} — cast_timeline above stays flat/relative
    # to fight start, unchanged, for every pre-T2.4 consumer.
    phase_cast_timeline: Mapping[int, Mapping[PhaseKey, tuple[float, ...]]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class CohortCriteria:
    """T1.5: deterministic identity for a cohort — every field that defines
    "what counts as comparable" for a reference-log query.
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

    def cohort_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


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
