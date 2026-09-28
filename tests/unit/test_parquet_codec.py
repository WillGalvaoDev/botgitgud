from __future__ import annotations

import dataclasses
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from real_corpus import CORPUS_ROOT, discover_corpus_paths
from test_m1_astra_regressions import _log

from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import (
    AbilityDamage,
    AuraTableProvenance,
    CollectionProvenance,
    CollectionStatus,
    ResourceStreamProvenance,
    StreamProvenance,
)
from botgitgud.ingest.parquet_codec import read_parquet_log, write_parquet_log


def test_ability_damage_accepts_legacy_json_shape() -> None:
    legacy = {"spell_id": 1, "total": 10.0, "hits": 2, "casts": 1}
    assert AbilityDamage(**legacy).by_source == {}


def test_every_pre_m6_parquet_reads_with_empty_new_fields() -> None:
    paths = discover_corpus_paths()
    if not paths:
        pytest.skip(f"corpus real ausente em {CORPUS_ROOT}")
    assert len(paths) >= 1274
    for path in paths:
        log = read_parquet_log(path)
        assert log.measurement_provenance is None
        # M3.1 (D-M31-07): the corpus predates stream_provenance entirely —
        # None, never a fabricated COMPLETE or empty StreamProvenance.
        assert log.stream_provenance is None
        assert log.damage_scope in {
            DamageScopeVersion.LEGACY_UNSCOPED,
            DamageScopeVersion.WCL_TARGET_SCOPE_V1,
            DamageScopeVersion.UNRECONCILED,
        }


# -- M3.1: stream_provenance round-trip ----------------------------------------


def test_stream_provenance_none_round_trips_to_none(tmp_path: Path) -> None:
    log = _log(100.0)
    assert log.stream_provenance is None
    path = tmp_path / "no_stream_provenance.parquet"
    write_parquet_log(log, path)
    assert read_parquet_log(path).stream_provenance is None


def test_stream_provenance_full_round_trips_byte_for_byte(tmp_path: Path) -> None:
    provenance = StreamProvenance(
        buffs=AuraTableProvenance(
            collection=CollectionProvenance(CollectionStatus.COMPLETE),
            total_time_ms=300000.0,
            auras={395152: (150000.0, 3), 777: (0.0, 0)},
        ),
        debuffs=AuraTableProvenance(
            collection=CollectionProvenance(
                CollectionStatus.PARTIAL, ("AURA_TABLE_TOTAL_TIME_MISMATCH",)
            ),
            total_time_ms=1000.0,
            auras={5: (300.0, 1)},
        ),
        resources=ResourceStreamProvenance(
            collection=CollectionProvenance(
                CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE", "API_ERROR"), 0.0, 300000.0
            ),
            player_event_count=0,
            by_type={},
        ),
    )
    log = dataclasses.replace(_log(100.0), stream_provenance=provenance)
    path = tmp_path / "stream_provenance.parquet"
    write_parquet_log(log, path)
    result = read_parquet_log(path)
    assert result.stream_provenance == provenance


def test_stream_provenance_complete_resources_round_trips_with_by_type(tmp_path: Path) -> None:
    provenance = StreamProvenance(
        resources=ResourceStreamProvenance(
            collection=CollectionProvenance(CollectionStatus.COMPLETE, (), 0.0, 300000.0),
            player_event_count=4,
            by_type={7: (3, 30.0), 0: (1, 0.0)},
        )
    )
    log = dataclasses.replace(_log(100.0), stream_provenance=provenance)
    path = tmp_path / "resources.parquet"
    write_parquet_log(log, path)
    result = read_parquet_log(path)
    assert result.stream_provenance == provenance
    assert result.stream_provenance is not None
    assert result.stream_provenance.resources is not None
    assert result.stream_provenance.resources.by_type == {7: (3, 30.0), 0: (1, 0.0)}


def test_stream_provenance_with_nan_aura_uptime_refuses_to_write(tmp_path: Path) -> None:
    # M3.1 independent review R4 (defense in depth): parse_aura_table_provenance
    # already drops a non-finite totalUptime before it reaches a
    # StreamProvenance, so this only fires for a hand-built one bypassing
    # that parser — allow_nan=False raises loudly rather than ever writing
    # non-canonical JSON (D-M31-08).
    provenance = StreamProvenance(
        buffs=AuraTableProvenance(
            collection=CollectionProvenance(CollectionStatus.COMPLETE, (), 0.0, 300000.0),
            total_time_ms=300000.0,
            auras={42: (float("nan"), 0)},
        )
    )
    log = dataclasses.replace(_log(100.0), stream_provenance=provenance)
    path = tmp_path / "nan.parquet"
    with pytest.raises(ValueError, match="not JSON compliant"):
        write_parquet_log(log, path)


def test_extra_columns_are_ignored_by_legacy_projection(tmp_path: Path) -> None:
    path = tmp_path / "extra.parquet"
    pq.write_table(pa.table({"old": [1], "m6_extra_json": ["{}"]}), path)
    assert pq.read_table(path, columns=["old"]).to_pylist() == [{"old": 1}]
