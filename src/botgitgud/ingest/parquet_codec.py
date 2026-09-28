"""T1.6 split of store.py's Parquet (de)serialization out of the Store
class itself, to keep both files under the 300-line limit
(T1.6). Pure encode/decode, no DuckDB access — see
ingest/store.py's module docstring for why nested PlayerLog fields are
JSON-string columns rather than native Arrow nested types.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import (
    AbilityDamage,
    AbilitySourceDamage,
    AuraBand,
    AuraDetail,
    AuraTableProvenance,
    CollectionProvenance,
    CollectionStatus,
    EventMix,
    FightRef,
    GearPiece,
    MeasurementProvenance,
    PhaseInterval,
    PlayerBuild,
    PlayerLog,
    ResourceStreamProvenance,
    SetupProfile,
    StreamProvenance,
    TalentNode,
)


def _encode_provenance(value: MeasurementProvenance | None) -> str | None:
    if value is None:
        return None
    payload = asdict(value)
    payload["damage_collection"]["status"] = value.damage_collection.status.value
    payload["casts_collection"]["status"] = value.casts_collection.status.value
    payload["damage_event_mix_by_spell"] = {
        str(spell): {key: asdict(mix) for key, mix in mixes.items()}
        for spell, mixes in value.damage_event_mix_by_spell.items()
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _decode_provenance(raw: str | None) -> MeasurementProvenance | None:
    if not raw:
        return None
    payload = json.loads(raw)
    for key in ("damage_collection", "casts_collection"):
        item = payload[key]
        item["status"] = CollectionStatus(item["status"])
        item["reasons"] = tuple(item.get("reasons", ()))
        payload[key] = CollectionProvenance(**item)
    payload["damage_event_mix_by_spell"] = {
        int(spell): {key: EventMix(**mix) for key, mix in mixes.items()}
        for spell, mixes in payload.get("damage_event_mix_by_spell", {}).items()
    }
    payload["pet_actor_ids"] = tuple(payload.get("pet_actor_ids", ()))
    payload["targets_per_cast_reasons"] = tuple(
        payload.get("targets_per_cast_reasons", ("CAST_INSTANCE_LINK_UNAVAILABLE",))
    )
    return MeasurementProvenance(**payload)


def _encode_collection(value: CollectionProvenance) -> dict:
    return {
        "status": value.status.value,
        "reasons": list(value.reasons),
        "requested_start_ms": value.requested_start_ms,
        "requested_end_ms": value.requested_end_ms,
    }


def _decode_collection(payload: dict) -> CollectionProvenance:
    return CollectionProvenance(
        CollectionStatus(payload["status"]),
        tuple(payload.get("reasons", ())),
        payload.get("requested_start_ms"),
        payload.get("requested_end_ms"),
    )


def _encode_aura_table(table: AuraTableProvenance | None) -> dict | None:
    if table is None:
        return None
    return {
        "collection": _encode_collection(table.collection),
        "total_time_ms": table.total_time_ms,
        "auras": {str(k): list(v) for k, v in table.auras.items()},
    }


def _decode_aura_table(payload: dict | None) -> AuraTableProvenance | None:
    if payload is None:
        return None
    return AuraTableProvenance(
        collection=_decode_collection(payload["collection"]),
        total_time_ms=payload.get("total_time_ms"),
        auras={int(k): (v[0], v[1]) for k, v in payload.get("auras", {}).items()},
    )


def _encode_resource_stream(resources: ResourceStreamProvenance | None) -> dict | None:
    if resources is None:
        return None
    return {
        "collection": _encode_collection(resources.collection),
        "player_event_count": resources.player_event_count,
        "by_type": {str(k): list(v) for k, v in resources.by_type.items()},
        "incomplete_types": sorted(resources.incomplete_types),
    }


def _decode_resource_stream(payload: dict | None) -> ResourceStreamProvenance | None:
    if payload is None:
        return None
    return ResourceStreamProvenance(
        collection=_decode_collection(payload["collection"]),
        player_event_count=payload.get("player_event_count", 0),
        by_type={int(k): (v[0], v[1]) for k, v in payload.get("by_type", {}).items()},
        incomplete_types=frozenset(payload.get("incomplete_types", ())),
    )


def _encode_stream_provenance(value: StreamProvenance | None) -> str | None:
    """M3.1 (docs/m3-1-specification.md D-M31-08): additive column; `None`
    (never a fabricated empty StreamProvenance) for every log fetched
    before this unit — read back as `None` by `_decode_stream_provenance`.

    `allow_nan=False`: D-M31-08 requires canonical JSON with no NaN.
    `parse_aura_table_provenance` already drops any non-finite
    `totalUptime` before it ever reaches a `StreamProvenance`, so this
    never fires for data produced by this unit's own parsers — it is a
    defense-in-depth guard against a hand-built StreamProvenance (or a
    future caller) smuggling one through, raising loudly instead of ever
    writing an out-of-spec JSON file.
    """
    if value is None:
        return None
    payload = {
        "schema_version": value.schema_version,
        "buffs": _encode_aura_table(value.buffs),
        "debuffs": _encode_aura_table(value.debuffs),
        "resources": _encode_resource_stream(value.resources),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _decode_stream_provenance(raw: str | None) -> StreamProvenance | None:
    if not raw:
        return None
    payload = json.loads(raw)
    schema_version = payload.get("schema_version", "stream-availability-v1")
    if schema_version != "stream-availability-v1":
        raise ValueError("unsupported stream provenance schema")
    return StreamProvenance(
        schema_version=schema_version,
        buffs=_decode_aura_table(payload.get("buffs")),
        debuffs=_decode_aura_table(payload.get("debuffs")),
        resources=_decode_resource_stream(payload.get("resources")),
    )


def _encode_setup(setup: SetupProfile | None) -> str | None:
    """EB.0: compact arrays, not objects — this is written once per log and
    there are already ~1k of them; per-row key repetition is pure waste.
    None (not "{}") when there is no setup, so a log written before EB.0
    and a log whose combatantInfo was genuinely absent read back the same
    honest way.
    """
    if setup is None:
        return None
    return json.dumps(
        {
            "talents": [[t.node_id, t.rank, t.spell_id] for t in setup.talents],
            "gear": [[g.slot, g.item_id, g.item_level, g.set_id] for g in setup.gear],
            "stats": dict(setup.stats),
        },
        separators=(",", ":"),
    )


def _decode_setup(raw: str | None) -> SetupProfile | None:
    """Tolerant by contract: the ~977 Parquet files written before EB.0 have
    no `setup_json` column at all, so `row.get(...)` yields None and this
    returns None — they stay readable, and nothing is inferred for them.
    """
    if not raw:
        return None
    payload = json.loads(raw)
    return SetupProfile(
        talents=tuple(
            TalentNode(node_id=t[0], rank=t[1], spell_id=t[2]) for t in payload.get("talents", [])
        ),
        gear=tuple(
            GearPiece(slot=g[0], item_id=g[1], item_level=g[2], set_id=g[3])
            for g in payload.get("gear", [])
        ),
        stats=dict(payload.get("stats", {})),
    )


def write_parquet_log(log: PlayerLog, path: Path) -> None:
    fight = log.fight
    build = log.build

    cast_timeline_json = json.dumps({str(k): list(v) for k, v in log.cast_timeline.items()})
    damage_by_ability_json = json.dumps(
        {
            str(k): {
                "spell_id": v.spell_id,
                "total": v.total,
                "hits": v.hits,
                "casts": v.casts,
                "by_source": {
                    str(source_id): {
                        "source_id": source.source_id,
                        "total": source.total,
                        "hits": source.hits,
                    }
                    for source_id, source in v.by_source.items()
                },
            }
            for k, v in log.damage_by_ability.items()
        }
    )
    uptimes_json = json.dumps({str(k): v for k, v in log.uptimes.items()})
    resource_waste_json = json.dumps(dict(log.resource_waste))
    resource_waste_by_ability_json = json.dumps(log.resource_waste_by_ability)
    aura_details_json = json.dumps(
        {
            str(spell_id): {
                "total_uses": detail.total_uses,
                "bands": [[band.start_ms, band.end_ms] for band in detail.bands],
            }
            for spell_id, detail in log.aura_details.items()
        }
    )
    avg_targets_per_cast_json = json.dumps({str(k): v for k, v in log.avg_targets_per_cast.items()})
    talent_pairs_json = json.dumps([list(p) for p in sorted(build.talent_pairs)])
    setup_json = _encode_setup(build.setup)
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
            "resource_waste_by_ability_json": [resource_waste_by_ability_json],
            "aura_details_json": [aura_details_json],
            "avg_targets_per_cast_json": [avg_targets_per_cast_json],
            "talent_pairs_json": [talent_pairs_json],
            "setup_json": [setup_json],
            "phase_intervals_json": [phase_intervals_json],
            "phase_cast_timeline_json": [phase_cast_timeline_json],
            "damage_scope": [log.damage_scope.value],
            "support_subtracted_damage": [
                log.support_subtracted_damage
                if log.damage_scope is DamageScopeVersion.WCL_TARGET_SCOPE_V1
                else 0.0
            ],
            "measurement_provenance_json": [_encode_provenance(log.measurement_provenance)],
            "stream_provenance_json": [_encode_stream_provenance(log.stream_provenance)],
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
        setup=_decode_setup(row.get("setup_json")),
    )
    cast_timeline = {int(k): tuple(v) for k, v in json.loads(row["cast_timeline_json"]).items()}
    damage_by_ability = {}
    for k, value in json.loads(row["damage_by_ability_json"]).items():
        by_source = {
            int(source_id): AbilitySourceDamage(**source)
            for source_id, source in value.pop("by_source", {}).items()
        }
        damage_by_ability[int(k)] = AbilityDamage(**value, by_source=by_source)
    uptimes = {int(k): v for k, v in json.loads(row["uptimes_json"]).items()}
    resource_waste = json.loads(row["resource_waste_json"])
    resource_waste_by_ability = {
        int(rtype): {int(ability_id): waste for ability_id, waste in by_ability.items()}
        for rtype, by_ability in json.loads(
            row.get("resource_waste_by_ability_json") or "{}"
        ).items()
    }
    aura_details = {
        int(spell_id): AuraDetail(
            total_uses=detail["total_uses"],
            bands=tuple(AuraBand(start_ms=b[0], end_ms=b[1]) for b in detail["bands"]),
        )
        for spell_id, detail in json.loads(row.get("aura_details_json") or "{}").items()
    }
    avg_targets_per_cast = {
        int(k): v for k, v in json.loads(row.get("avg_targets_per_cast_json") or "{}").items()
    }
    phase_cast_timeline = {
        int(spell_id): {(p[0], p[1]): tuple(p[2]) for p in entries}
        for spell_id, entries in json.loads(row.get("phase_cast_timeline_json") or "{}").items()
    }

    damage_scope = DamageScopeVersion(row.get("damage_scope") or DamageScopeVersion.LEGACY_UNSCOPED)
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
        resource_waste_by_ability=resource_waste_by_ability,
        aura_details=aura_details,
        deaths=row["deaths"],
        downtime_s=row.get("downtime_s") or 0.0,
        avg_targets_per_cast=avg_targets_per_cast,
        phase_cast_timeline=phase_cast_timeline,
        damage_scope=damage_scope,
        support_subtracted_damage=(
            (row.get("support_subtracted_damage") or 0.0)
            if damage_scope is DamageScopeVersion.WCL_TARGET_SCOPE_V1
            else 0.0
        ),
        measurement_provenance=_decode_provenance(row.get("measurement_provenance_json")),
        stream_provenance=_decode_stream_provenance(row.get("stream_provenance_json")),
    )
