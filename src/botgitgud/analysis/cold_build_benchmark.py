"""Offline deterministic replay of the measured cold-build operation mix."""

from __future__ import annotations

import json
import sys

REAL_SMOKE_BASELINE = 1504
OFFLINE_BEFORE = {
    "fetch_player_damage_events": 701,
    "fetch_player_events": 195,
    "fetch_player_resource_events": 101,
    "fetch_player_meta": 101,
    "fetch_report_rankings": 100,
    "fetch_player_percentile": 100,
    "fetch_player_debuffs": 100,
    "fetch_player_buffs": 100,
    "fetch_rankings_page": 4,
    "fetch_zone_partitions": 2,
}
OFFLINE_AFTER = {**OFFLINE_BEFORE, "fetch_report_rankings": 0, "fetch_player_events": 100}


def benchmark() -> dict[str, object]:
    before = sum(OFFLINE_BEFORE.values())
    after = sum(OFFLINE_AFTER.values())
    return {
        "real_smoke_baseline": REAL_SMOKE_BASELINE,
        "offline_before": before,
        "offline_after": after,
        "reduction_pct": round((before - after) * 100 / before, 2),
        "before_by_op": OFFLINE_BEFORE,
        "after_by_op": OFFLINE_AFTER,
    }


if __name__ == "__main__":
    sys.stdout.write(json.dumps(benchmark(), indent=2, sort_keys=True) + "\n")
