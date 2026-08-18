"""T1.6 split of store.py's Parquet (de)serialization out of the Store
class itself, to keep both files under the 300-line limit
(docs/implementacao.md T1.6). Pure encode/decode, no DuckDB access — see
ingest/store.py's module docstring for why nested PlayerLog fields are
JSON-string columns rather than native Arrow nested types.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from botgitgud.domain.models import AbilityDamage, FightRef, PhaseInterval, PlayerBuild, PlayerLog


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
    avg_targets_per_cast_json = json.dumps({str(k): v for k, v in log.avg_targets_per_cast.items()})
    talent_pairs_json = json.dumps([list(p) for p in sorted(build.talent_pairs)])
    phase_intervals_json = json.dumps(
        [[iv.phase_id, iv.occurrence, iv.start_ms, iv.end_ms] for iv in fight.phase_intervals]
    )
    phase_cast_timeline_json = json.dumps(
        {
            str(spell_id): [
                [phase_id, occurrence, list(times)]
                for (phase_id, occurrence), times in by_key.items()
            ]
            for spell_id, by_key in log.phase_cast_timeline.items()
        }
    )

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
            "has_augmentation": [build.has_augmentation],
            "dps": [log.dps],
            "percentile": [log.percentile],
            "active_time_pct": [log.active_time_pct],
            "deaths": [log.deaths],
            "downtime_s": [log.downtime_s],
            "cast_timeline_json": [cast_timeline_json],
            "damage_by_ability_json": [damage_by_ability_json],
            "uptimes_json": [uptimes_json],
            "resource_waste_json": [resource_waste_json],
            "avg_targets_per_cast_json": [avg_targets_per_cast_json],
            "talent_pairs_json": [talent_pairs_json],
            "phase_intervals_json": [phase_intervals_json],
            "phase_cast_timeline_json": [phase_cast_timeline_json],
        }
    )
    pq.write_table(table, path)


def read_parquet_log(path: Path) -> PlayerLog:
    row = pq.read_table(path).to_pylist()[0]

    phase_intervals = tuple(
        PhaseInterval(phase_id=p[0], occurrence=p[1], start_ms=p[2], end_ms=p[3])
        for p in json.loads(row.get("phase_intervals_json") or "[]")
    )
    fight = FightRef(
        report_code=row["report_code"],
        fight_id=row["fight_id"],
        encounter_id=row["encounter_id"],
        boss_name=row["boss_name"],
        difficulty=row["difficulty"],
        duration_s=row["duration_s"],
        kill=row["kill"],
        partition=row["partition"],
        phase_intervals=phase_intervals,
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
        has_augmentation=row.get("has_augmentation", False),
        talent_pairs=frozenset(
            (p[0], p[1]) for p in json.loads(row.get("talent_pairs_json") or "[]")
        ),
    )
    cast_timeline = {int(k): tuple(v) for k, v in json.loads(row["cast_timeline_json"]).items()}
    damage_by_ability = {
        int(k): AbilityDamage(**v) for k, v in json.loads(row["damage_by_ability_json"]).items()
    }
    uptimes = {int(k): v for k, v in json.loads(row["uptimes_json"]).items()}
    resource_waste = json.loads(row["resource_waste_json"])
    avg_targets_per_cast = {
        int(k): v for k, v in json.loads(row.get("avg_targets_per_cast_json") or "{}").items()
    }
    phase_cast_timeline = {
        int(spell_id): {(p[0], p[1]): tuple(p[2]) for p in entries}
        for spell_id, entries in json.loads(row.get("phase_cast_timeline_json") or "{}").items()
    }

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
        downtime_s=row.get("downtime_s") or 0.0,
        avg_targets_per_cast=avg_targets_per_cast,
        phase_cast_timeline=phase_cast_timeline,
    )
