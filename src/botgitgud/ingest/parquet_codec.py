"""T1.6 split of store.py's Parquet (de)serialization out of the Store
class itself, to keep both files under the 300-line limit
(docs/implementacao.md T1.6). Pure encode/decode, no DuckDB access — see
ingest/store.py's module docstring for why nested PlayerLog/CohortProfile
fields are JSON-string columns rather than native Arrow nested types.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from botgitgud.domain.models import (
    AbilityDamage,
    CohortProfile,
    FightRef,
    PlayerBuild,
    PlayerLog,
    SpellProfile,
)


def write_parquet_log(log: PlayerLog, path: Path) -> None:
    fight = log.fight
    build = log.build

    cast_timeline_json = json.dumps({str(k): list(v) for k, v in log.cast_timeline.items()})
    damage_by_ability_json = json.dumps(
        {
            str(k): {"spell_id": v.spell_id, "total": v.total, "hits": v.hits, "casts": v.casts}
            for k, v in log.damage_by_ability.items()
        }
    )
    uptimes_json = json.dumps({str(k): v for k, v in log.uptimes.items()})
    resource_waste_json = json.dumps(dict(log.resource_waste))

    table = pa.table(
        {
            "report_code": [fight.report_code],
            "fight_id": [fight.fight_id],
            "encounter_id": [fight.encounter_id],
            "boss_name": [fight.boss_name],
            "difficulty": [fight.difficulty],
            "duration_s": [fight.duration_s],
            "kill": [fight.kill],
            "partition": [fight.partition],
            "character_name": [build.character_name],
            "server": [build.server],
            "class_name": [build.class_name],
            "spec_name": [build.spec_name],
            "role": [build.role],
            "item_level": [build.item_level],
            "talent_hash": [build.talent_hash],
            "tier_pieces": [build.tier_pieces],
            "external_buffs": [list(build.external_buffs)],
            "dps": [log.dps],
            "percentile": [log.percentile],
            "active_time_pct": [log.active_time_pct],
            "deaths": [log.deaths],
            "cast_timeline_json": [cast_timeline_json],
            "damage_by_ability_json": [damage_by_ability_json],
            "uptimes_json": [uptimes_json],
            "resource_waste_json": [resource_waste_json],
        }
    )
    pq.write_table(table, path)


def read_parquet_log(path: Path) -> PlayerLog:
    row = pq.read_table(path).to_pylist()[0]

    fight = FightRef(
        report_code=row["report_code"],
        fight_id=row["fight_id"],
        encounter_id=row["encounter_id"],
        boss_name=row["boss_name"],
        difficulty=row["difficulty"],
        duration_s=row["duration_s"],
        kill=row["kill"],
        partition=row["partition"],
    )
    build = PlayerBuild(
        character_name=row["character_name"],
        server=row["server"],
        class_name=row["class_name"],
        spec_name=row["spec_name"],
        role=row["role"],
        item_level=row["item_level"],
        talent_hash=row["talent_hash"],
        tier_pieces=row["tier_pieces"],
        external_buffs=frozenset(row["external_buffs"] or []),
    )
    cast_timeline = {int(k): tuple(v) for k, v in json.loads(row["cast_timeline_json"]).items()}
    damage_by_ability = {
        int(k): AbilityDamage(**v) for k, v in json.loads(row["damage_by_ability_json"]).items()
    }
    uptimes = {int(k): v for k, v in json.loads(row["uptimes_json"]).items()}
    resource_waste = json.loads(row["resource_waste_json"])

    return PlayerLog(
        fight=fight,
        build=build,
        dps=row["dps"],
        percentile=row["percentile"],
        cast_timeline=cast_timeline,
        active_time_pct=row["active_time_pct"],
        damage_by_ability=damage_by_ability,
        uptimes=uptimes,
        resource_waste=resource_waste,
        deaths=row["deaths"],
    )


def write_parquet_profile(profile: CohortProfile, path: Path) -> None:
    spells_json = json.dumps(
        {
            str(sid): {
                "spell_id": sp.spell_id,
                "presence": sp.presence,
                "ref_times": list(sp.ref_times),
                "n_usages_median": sp.n_usages_median,
            }
            for sid, sp in profile.spells.items()
        }
    )
    table = pa.table(
        {
            "cohort_id": [profile.cohort_id],
            "n_members": [profile.n_members],
            "built_at": [profile.built_at.isoformat()],
            "spells_json": [spells_json],
        }
    )
    pq.write_table(table, path)


def read_parquet_profile(path: Path) -> CohortProfile:
    row = pq.read_table(path).to_pylist()[0]
    spells_raw = json.loads(row["spells_json"])
    spells = {
        int(sid): SpellProfile(
            spell_id=v["spell_id"],
            presence=v["presence"],
            ref_times=tuple(v["ref_times"]),
            n_usages_median=v["n_usages_median"],
        )
        for sid, v in spells_raw.items()
    }
    return CohortProfile(
        cohort_id=row["cohort_id"],
        n_members=row["n_members"],
        built_at=datetime.fromisoformat(row["built_at"]),
        spells=spells,
    )
