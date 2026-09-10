from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from real_corpus import CORPUS_ROOT, discover_corpus_paths

from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import AbilityDamage
from botgitgud.ingest.parquet_codec import read_parquet_log


def test_ability_damage_accepts_legacy_json_shape() -> None:
    legacy = {"spell_id": 1, "total": 10.0, "hits": 2, "casts": 1}
    assert AbilityDamage(**legacy).by_source == {}


def test_every_pre_m6_parquet_reads_with_empty_new_fields() -> None:
    paths = discover_corpus_paths()
    if not paths:
        pytest.skip(f"corpus real ausente em {CORPUS_ROOT}")
    assert len(paths) == 1274
    for path in paths:
        log = read_parquet_log(path)
        assert log.resource_waste_by_ability == {}
        assert log.aura_details == {}
        assert all(ability.by_source == {} for ability in log.damage_by_ability.values())
        assert log.damage_scope is DamageScopeVersion.LEGACY_UNSCOPED


def test_extra_columns_are_ignored_by_legacy_projection(tmp_path: Path) -> None:
    path = tmp_path / "extra.parquet"
    pq.write_table(pa.table({"old": [1], "m6_extra_json": ["{}"]}), path)
    assert pq.read_table(path, columns=["old"]).to_pylist() == [{"old": 1}]
