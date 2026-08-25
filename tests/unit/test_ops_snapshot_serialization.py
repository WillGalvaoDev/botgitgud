"""HOT-PATH smoke attempt #1: FAIL antes da analise.

`cohort_registry.updated_at` chega do DuckDB como `datetime` e ia direto para
`json.dumps` dentro do ops snapshot:

    TypeError: Object of type datetime is not JSON serializable

O `except OSError` do publisher era estreito demais, o TypeError escapou e o
worker morreu no boot — antes de qualquer `!analisar`.

Duas propriedades DISTINTAS, testadas separadamente:
  A. o snapshot serializa datetime corretamente;
  B. falha de observabilidade nao derruba o worker (contrato non-fatal D-34).

Nada aqui toca WCL ou Discord.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import botgitgud.bot.discord_bot as discord_module
from botgitgud.bot.job_models import Job, now_utc_naive
from botgitgud.bot.ops_snapshot import (
    SNAPSHOT_FILENAME,
    _json_safe,
    read_snapshot,
    write_snapshot,
)
from botgitgud.cli_ops import warehouse_status

COHORT_ID = "f91dffaf13ddc899"


def _ready_cohort(updated_at: Any) -> dict[str, Any]:
    """A forma exata que `Store.list_ready_cohorts()` devolve."""
    return {
        "cohort_id": COHORT_ID,
        "encounter_id": 3183,
        "difficulty": 5,
        "partition": 3,
        "class_name": "Warlock",
        "spec_name": "Demonology",
        "duration_min_s": 491.0,
        "duration_max_s": 516.0,
        "n_members": 37,
        "updated_at": updated_at,
    }


def _job(status: str = "done") -> Job:
    return Job(
        job_id="job-1",
        job_type="analyze",  # type: ignore[arg-type]
        dedup_key="FhYZDLMbwBVAx4KX:19:Fiskowl",
        discord_user_id="1",
        discord_channel_id="2",
        status=status,  # type: ignore[arg-type]
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=now_utc_naive(),
        error=None,
        report_path=None,
    )


# == A. serializacao ===================================================================


def test_incident_regression_ready_cohort_with_datetime_serializes(tmp_path: Path) -> None:
    """A reproducao exata: ready_cohorts[0]["updated_at"] = datetime(...)."""
    write_snapshot(
        tmp_path,
        active=[],
        ready_cohorts=[_ready_cohort(datetime(2026, 8, 25, 19, 45, 1))],
    )
    payload = json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    assert payload["ready_cohorts"][0]["updated_at"] == "2026-08-25T19:45:01"


def test_updated_at_becomes_a_string_not_a_python_object(tmp_path: Path) -> None:
    write_snapshot(tmp_path, active=[], ready_cohorts=[_ready_cohort(now_utc_naive())])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    value = snapshot.ready_cohorts[0]["updated_at"]
    assert isinstance(value, str)
    datetime.fromisoformat(value)  # ISO-8601 valido


def test_multiple_ready_cohorts_with_timestamps_serialize(tmp_path: Path) -> None:
    cohorts = [
        _ready_cohort(datetime(2026, 8, 25, 19, 45, 1)),
        _ready_cohort(datetime(2026, 8, 24, 10, 0, 0)),
        _ready_cohort(datetime(2026, 8, 23, 8, 30, 15)),
    ]
    write_snapshot(tmp_path, active=[], ready_cohorts=cohorts)
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    stamps = [c["updated_at"] for c in snapshot.ready_cohorts]
    assert stamps == ["2026-08-25T19:45:01", "2026-08-24T10:00:00", "2026-08-23T08:30:15"]


def test_timezone_aware_datetime_keeps_its_offset(tmp_path: Path) -> None:
    aware = datetime(2026, 8, 25, 19, 45, 1, tzinfo=UTC)
    write_snapshot(tmp_path, active=[], ready_cohorts=[_ready_cohort(aware)])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    value = snapshot.ready_cohorts[0]["updated_at"]
    assert value == "2026-08-25T19:45:01+00:00"
    assert datetime.fromisoformat(value).tzinfo is not None


def test_naive_datetime_follows_the_project_convention(tmp_path: Path) -> None:
    """T1.8/`now_utc_naive`: todo datetime deste dominio e naive-porem-UTC, e o
    projeto ja serializa com `.isoformat()` puro (finished_at,
    oldest_running_started_at). Reusamos a mesma convencao: sem offset
    inventado.
    """
    naive = datetime(2026, 8, 25, 19, 45, 1)
    write_snapshot(tmp_path, active=[], ready_cohorts=[_ready_cohort(naive)])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    value = snapshot.ready_cohorts[0]["updated_at"]
    assert value == "2026-08-25T19:45:01"
    assert datetime.fromisoformat(value).tzinfo is None


def test_a_full_snapshot_with_every_section_serializes(tmp_path: Path) -> None:
    write_snapshot(
        tmp_path,
        active=[_job("running")],
        recent=[_job("done"), _job("failed")],
        ready_cohorts=[_ready_cohort(now_utc_naive())],
        worker_alive=True,
        cold_build={"mode": "prewarm", "stage": "completed", "buckets": [{"planned": 37}]},
        points_remaining=2520.44,
        points_limit=3600.0,
    )
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.worker_alive is True
    assert snapshot.active_job is not None
    assert snapshot.latest_completed_job is not None
    assert snapshot.ready_cohorts[0]["n_members"] == 37
    assert snapshot.cold_build is not None
    assert snapshot.points_remaining == 2520.44


def test_nested_structures_stay_json_safe(tmp_path: Path) -> None:
    write_snapshot(
        tmp_path,
        active=[],
        cold_build={"buckets": [{"at": datetime(2026, 8, 25, 12, 0), "tags": ("a", "b")}]},
    )
    payload = json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    bucket = payload["cold_build"]["buckets"][0]
    assert bucket["at"] == "2026-08-25T12:00:00"
    assert bucket["tags"] == ["a", "b"]  # tupla vira lista


def test_date_is_also_normalized() -> None:
    assert _json_safe(date(2026, 8, 25)) == "2026-08-25"


# -- fail closed: nada de default=str ---------------------------------------------------


def test_unexpected_type_raises_instead_of_being_stringified() -> None:
    class Weird:
        def __str__(self) -> str:
            return "parece-uma-string"

    with pytest.raises(TypeError, match="Weird"):
        _json_safe({"campo": Weird()})


def test_error_names_the_exact_path_inside_the_payload() -> None:
    with pytest.raises(TypeError, match=r"\$\.ready_cohorts\[0\]\.updated_at"):
        _json_safe({"ready_cohorts": [{"updated_at": object()}]})


def test_write_snapshot_does_not_use_default_str(tmp_path: Path) -> None:
    """Se `default=str` voltasse, este objeto viraria string em vez de estourar."""
    with pytest.raises(TypeError):
        write_snapshot(tmp_path, active=[], cold_build={"x": object()})


def test_path_objects_are_not_silently_accepted() -> None:
    with pytest.raises(TypeError, match=r"PosixPath|WindowsPath|Path"):
        _json_safe({"dir": Path("data")})


def test_atomic_write_survives_the_new_boundary(tmp_path: Path) -> None:
    for _ in range(4):
        write_snapshot(tmp_path, active=[], ready_cohorts=[_ready_cohort(now_utc_naive())])
    assert sorted(p.name for p in tmp_path.iterdir()) == [SNAPSHOT_FILENAME]


def test_ops_status_reads_ready_cohorts_normally(tmp_path: Path) -> None:
    write_snapshot(
        tmp_path, active=[], ready_cohorts=[_ready_cohort(datetime(2026, 8, 25, 19, 45, 1))]
    )
    lines = warehouse_status(tmp_path)
    assert lines is not None
    text = "\n".join(lines)
    assert COHORT_ID in text
    assert "cohorts ready=1" in text


# == B. contrato non-fatal (propriedade separada) ======================================


def _deps(tmp_path: Path, store: Any) -> Any:
    return SimpleNamespace(store=store, settings=SimpleNamespace(data_dir=tmp_path))


class _Queue:
    def list_recent(self) -> list[Job]:
        return []


def test_snapshot_serialization_failure_never_kills_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-34 documenta: "falhar ao escrever um arquivo de diagnostico nunca pode
    derrubar o worker". O `except OSError` original nao cobria TypeError.
    """
    monkeypatch.setattr(
        discord_module,
        "write_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(TypeError("Object of type datetime ...")),
    )
    store = SimpleNamespace(list_ready_cohorts=lambda: [_ready_cohort(now_utc_naive())])
    discord_module._publish_snapshot(_deps(tmp_path, store), _Queue(), [], None, None)  # type: ignore[arg-type]


def test_a_registry_returning_datetime_does_not_kill_the_worker(tmp_path: Path) -> None:
    """Ponta a ponta do incidente, agora sem monkeypatch: o store devolve
    datetime de verdade e o publish tem de sobreviver.
    """
    store = SimpleNamespace(list_ready_cohorts=lambda: [_ready_cohort(now_utc_naive())])
    discord_module._publish_snapshot(_deps(tmp_path, store), _Queue(), [], None, None)  # type: ignore[arg-type]
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert isinstance(snapshot.ready_cohorts[0]["updated_at"], str)


def test_os_error_is_still_contained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discord_module,
        "write_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disco cheio")),
    )
    store = SimpleNamespace(list_ready_cohorts=list)
    discord_module._publish_snapshot(_deps(tmp_path, store), _Queue(), [], None, None)  # type: ignore[arg-type]


def test_the_next_tick_can_publish_again_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery: uma falha transitoria nao envenena os ticks seguintes."""
    calls = {"n": 0}
    real = discord_module.write_snapshot

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("falha transitoria")
        return real(*args, **kwargs)

    monkeypatch.setattr(discord_module, "write_snapshot", flaky)
    store = SimpleNamespace(list_ready_cohorts=lambda: [_ready_cohort(now_utc_naive())])
    deps = _deps(tmp_path, store)

    publish = discord_module._publish_snapshot
    publish(deps, _Queue(), [], None, None)  # type: ignore[arg-type]  # falha, contida
    assert read_snapshot(tmp_path) is None

    publish(deps, _Queue(), [], None, None)  # type: ignore[arg-type]  # tick seguinte
    assert read_snapshot(tmp_path) is not None
