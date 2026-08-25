"""RC.1/RC.12 — durabilidade do relatório."""

from __future__ import annotations

from pathlib import Path

import pytest

from botgitgud.bot.report_store import (
    ReportPersistenceError,
    load_report,
    persist_report,
    report_path_for,
    reports_dir,
)


def test_persist_writes_the_file_and_returns_its_path(tmp_path: Path) -> None:
    path = persist_report(tmp_path, "job1", "<html>ok</html>")
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "<html>ok</html>"
    assert path.parent == reports_dir(tmp_path)


def test_path_is_deterministic_for_a_job(tmp_path: Path) -> None:
    assert report_path_for(tmp_path, "abc") == report_path_for(tmp_path, "abc")
    assert report_path_for(tmp_path, "abc") != report_path_for(tmp_path, "abd")


def test_two_jobs_never_collide(tmp_path: Path) -> None:
    a = persist_report(tmp_path, "job-a", "<html>A</html>")
    b = persist_report(tmp_path, "job-b", "<html>B</html>")
    assert a != b
    assert a.read_text(encoding="utf-8") != b.read_text(encoding="utf-8")


def test_write_is_atomic_and_leaves_no_partial_artifact(tmp_path: Path) -> None:
    persist_report(tmp_path, "job1", "<html>1</html>")
    persist_report(tmp_path, "job1", "<html>2</html>")
    names = sorted(p.name for p in reports_dir(tmp_path).iterdir())
    assert names == ["job1.html"]  # nenhum .tmp sobrevivente
    assert (reports_dir(tmp_path) / "job1.html").read_text(encoding="utf-8") == "<html>2</html>"


def test_filesystem_failure_raises_a_typed_error_and_leaves_no_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise PermissionError("disco somente leitura")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(ReportPersistenceError):
        persist_report(tmp_path, "job1", "<html/>")
    assert not report_path_for(tmp_path, "job1").exists()


def test_job_id_can_never_escape_the_reports_directory(tmp_path: Path) -> None:
    for evil in ("../../etc/passwd", "a/b", "", "x" * 65):
        with pytest.raises(ReportPersistenceError):
            report_path_for(tmp_path, evil)


def test_load_round_trips_and_reports_unreadable_paths(tmp_path: Path) -> None:
    path = persist_report(tmp_path, "job1", "<html>x</html>")
    assert load_report(path) == "<html>x</html>"
    with pytest.raises(ReportPersistenceError):
        load_report(tmp_path / "nao-existe.html")
