from __future__ import annotations

import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from botgitgud.analysis.damage_scope_guard import (
    MixedDamageScopeError,
    require_homogeneous_scope,
    select_comparable,
)
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.ingest.damage_aggregation import (
    aggregate_damage_by_ability,
    parse_damage_events,
    scope_total,
    support_subtracted_total,
)
from botgitgud.ingest.performance_parsing import extract_pet_owner_map, pet_ids_for_owner
from botgitgud.ingest.wcl_parsing import extract_scope_target_ids

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gate1_scope"


def _probe_data() -> tuple[dict, list[dict]]:
    meta_payload = json.loads((_FIXTURE / "phase1_meta.json").read_text(encoding="utf-8"))
    report = meta_payload["response_json"]["data"]["reportData"]["report"]
    events: list[dict] = []
    for path in sorted(_FIXTURE.glob("phase2_events_p*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            page = json.load(stream)
        events.extend(page["response_json"]["data"]["reportData"]["report"]["events"]["data"])
    return report, events


def _log(scope: DamageScopeVersion) -> PlayerLog:
    fight = FightRef("REPORT", 1, 3181, "Encounter", 5, 10.0, True)
    build = PlayerBuild("Player", None, "Mage", "Fire", "dps", None, None, None)
    return PlayerLog(fight, build, 1.0, None, {}, damage_scope=scope)


def test_homogeneous_legacy_and_v1_are_each_eligible() -> None:
    legacy = _log(DamageScopeVersion.LEGACY_UNSCOPED)
    assert (
        require_homogeneous_scope((legacy, replace(legacy))) is DamageScopeVersion.LEGACY_UNSCOPED
    )
    v1 = replace(legacy, damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1)
    assert require_homogeneous_scope((v1, replace(v1))) is DamageScopeVersion.WCL_TARGET_SCOPE_V1


def test_mixed_or_unreconciled_damage_fails_closed() -> None:
    legacy = _log(DamageScopeVersion.LEGACY_UNSCOPED)
    v1 = replace(legacy, damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1)
    bad = replace(legacy, damage_scope=DamageScopeVersion.UNRECONCILED)
    with pytest.raises(MixedDamageScopeError):
        require_homogeneous_scope((legacy, v1))
    with pytest.raises(MixedDamageScopeError):
        require_homogeneous_scope((bad,))


def test_select_comparable_excludes_other_contracts_without_raising() -> None:
    legacy = _log(DamageScopeVersion.LEGACY_UNSCOPED)
    v1 = replace(legacy, damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1)
    selected, excluded = select_comparable(legacy, (v1, replace(legacy), v1))
    assert selected == (legacy,)
    assert excluded == 2


def test_gate1_probe_reconciles_all_twenty_players_exactly() -> None:
    report, events = _probe_data()
    actors = report["masterData"]["actors"]
    entries = report["damageTable"]["data"]["entries"]
    target_ids = extract_scope_target_ids(entries, actors)
    owner_map = extract_pet_owner_map(actors)
    assert len(entries) == 20
    assert all(entry["total"] > 0 for entry in entries)

    results: dict[str, tuple[float, float]] = {}
    for entry in entries:
        player_id = int(entry["id"])
        source_ids = frozenset({player_id}) | pet_ids_for_owner(owner_map, player_id)
        unscoped, _ = aggregate_damage_by_ability(parse_damage_events(events, source_ids), {})
        scoped, _ = aggregate_damage_by_ability(
            parse_damage_events(events, source_ids, target_ids), {}
        )
        subtracted = support_subtracted_total(events, player_id, owner_map, target_ids)
        reconstructed = scope_total(scoped, subtracted)
        assert reconstructed == entry["total"]
        results[entry["name"]] = (
            sum(ability.total for ability in unscoped.values()),
            reconstructed,
        )

    # 167_527 was the intermediate phase-target proxy, not the unscoped total.
    assert results["Zaljan"] == (834_546, 141_869)
    assert 270 not in target_ids
    silver_damage = sum(
        (event.get("amount") or 0) + (event.get("absorbed") or 0)
        for event in events
        if event.get("type") == "damage" and event.get("targetID") == 270
    )
    assert silver_damage == 6_563_717
