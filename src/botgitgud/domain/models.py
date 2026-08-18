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
class SpellProfile:
    """T1.3 (docs/desvios.md D-12(b)): one spell's aggregate stats across a
    cohort — formalizes the shape build_cd_reference_profile() (Fase 0,
    bot.py) already produces ad-hoc as a dict.
    """

    spell_id: int
    presence: float
    ref_times: tuple[float, ...]
    n_usages_median: float


@dataclass(frozen=True, slots=True)
class CohortProfile:
    cohort_id: str
    n_members: int
    built_at: datetime
    spells: Mapping[int, SpellProfile]
