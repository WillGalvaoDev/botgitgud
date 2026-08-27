"""B5 — a trilha operacional precisa sobreviver ao processo.

O soak de 24h exige reconstruir startup, worker, jobs, defer/resume, WCL,
entrega e exceções DEPOIS que o terminal morreu. Estes testes verificam o
arquivo em disco, não o console: é o arquivo que é a evidência.

Todos usam `tmp_path` — nada escreve em `data/logs` real, e nenhum toca rede.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from botgitgud.logging_setup import (
    LOG_FILENAME,
    REDACTED,
    configure_logging,
    current_session_id,
    disable_file_logging,
    enable_file_logging,
    log_dir_for,
)

BIG = 10_000_000


@pytest.fixture(autouse=True)
def _restore_logging() -> Any:
    """O logging é global do processo: cada teste devolve o estado anterior,
    senão um sink de teste continuaria escrevendo durante a suíte inteira.
    """
    root = logging.getLogger()
    previous = list(root.handlers)
    previous_level = root.level
    yield
    disable_file_logging()
    root.handlers = previous
    root.setLevel(previous_level)
    structlog.reset_defaults()


def _lines(data_dir: Path) -> list[dict[str, Any]]:
    logging.getLogger().handlers[-1].flush()
    text = (log_dir_for(data_dir) / LOG_FILENAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _emit(event: str, **fields: Any) -> None:
    structlog.get_logger("test.operational").info(event, **fields)


# -- 1/2/3: arquivo, JSON válido, timestamp ------------------------------------------


def test_log_file_is_created_under_the_configured_data_dir(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started")

    assert (log_dir_for(tmp_path) / LOG_FILENAME).exists()
    assert log_dir_for(tmp_path) == tmp_path / "logs"


def test_every_line_is_one_valid_json_object_with_a_timestamp(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    for i in range(5):
        _emit("job.started", job_id=f"job-{i}")

    records = _lines(tmp_path)
    assert len(records) == 5
    for record in records:
        assert isinstance(record, dict)
        assert record["timestamp"]
        assert record["level"] == "info"
        assert record["pid"] > 0


def test_no_partial_lines_are_ever_produced(tmp_path: Path) -> None:
    """Um traceback tem quebras de linha; se elas vazassem para o arquivo, uma
    entrada viraria várias linhas quebradas e o JSONL deixaria de ser parseável.
    """
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    try:
        raise RuntimeError("linha 1\nlinha 2\nlinha 3")
    except RuntimeError as e:
        structlog.get_logger("test.operational").error("job.crashed", exc_info=e)
    _emit("job.done", job_id="job-1")

    raw = (log_dir_for(tmp_path) / LOG_FILENAME).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 2  # o traceback multilinha continua sendo UMA linha
    assert "\n" in records[0]["traceback"]  # preservado, mas escapado no JSON


# -- 4-9: eventos críticos persistidos ------------------------------------------------


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        ("process.started", {"command": "serve"}),
        ("discord_bot.worker_crashed", {"error": "RuntimeError('x')"}),
        ("worker.job_deferred_budget", {"job_id": "j1", "cohort_id": "c1", "completed": 30}),
        ("job.resumed", {"job_id": "j1", "defer_count": 1}),
        ("delivery.failed", {"job_id": "j1", "channel_id": "123", "error_type": "Forbidden"}),
        ("wcl.query_transport_error", {"attempt": 2, "op_name": "fetch_player_meta"}),
        ("wcl.rate_limit_budget_exceeded", {"points_remaining": 990.0, "floor": 1000.0}),
        ("ops_snapshot_write_failed", {"error_type": "TypeError"}),
    ],
)
def test_critical_events_survive_in_the_file(
    tmp_path: Path, event: str, fields: dict[str, Any]
) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit(event, **fields)

    record = _lines(tmp_path)[0]
    assert record["event"] == event
    for key, value in fields.items():
        assert record[key] == value


# -- 10: exceção inesperada mantém evidência útil -------------------------------------


def test_unexpected_exception_keeps_type_message_and_traceback(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)

    def _inner() -> None:
        raise ValueError("algo quebrou fundo na pilha")

    try:
        _inner()
    except ValueError as e:
        structlog.get_logger("test.operational").error("process.unexpected_error", exc_info=e)

    record = _lines(tmp_path)[0]
    assert record["exception_type"] == "ValueError"
    assert record["exception_message"] == "algo quebrou fundo na pilha"
    assert "_inner" in record["traceback"]
    assert "ValueError" in record["traceback"]


def test_log_exception_helper_also_captures_the_stack(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    try:
        raise KeyError("faltou")
    except KeyError:
        structlog.get_logger("test.operational").exception("discord_bot.job_crashed")

    record = _lines(tmp_path)[0]
    assert record["exception_type"] == "KeyError"
    assert "KeyError" in record["traceback"]


# -- 11/12: rotação e retenção ---------------------------------------------------------


def test_rotation_splits_the_file_once_it_exceeds_the_size_limit(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=2_000, backup_count=3)
    for i in range(200):
        _emit("cold_build.building", job_id=f"job-{i}", filler="x" * 50)

    rotated = sorted(log_dir_for(tmp_path).glob(f"{LOG_FILENAME}.*"))
    assert rotated, "nada rotacionou: o arquivo cresceria sem limite"
    assert (log_dir_for(tmp_path) / LOG_FILENAME).exists()


def test_retention_caps_the_number_of_files_on_disk(tmp_path: Path) -> None:
    """Sem teto, um bot rodando continuamente enche o disco."""
    backup_count = 2
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=1_000, backup_count=backup_count)
    for i in range(500):
        _emit("cold_build.building", job_id=f"job-{i}", filler="y" * 50)

    files = list(log_dir_for(tmp_path).glob(f"{LOG_FILENAME}*"))
    assert len(files) == backup_count + 1  # o ativo mais os backups retidos


def test_rotated_files_stay_parseable(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=2_000, backup_count=3)
    for i in range(200):
        _emit("job.started", job_id=f"job-{i}")

    for path in log_dir_for(tmp_path).glob(f"{LOG_FILENAME}*"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["event"]


# -- 13: reinício ----------------------------------------------------------------------


def test_a_restart_starts_a_new_session_without_corrupting_the_file(tmp_path: Path) -> None:
    """Duas execuções escrevem no MESMO arquivo. O `session_id` é o que separa
    um soak do anterior — sem ele, dois runs viram um borrão.
    """
    configure_logging()
    first = enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started", command="serve")
    _emit("process.stopped", command="serve")

    second = enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started", command="serve")

    assert first != second
    records = _lines(tmp_path)
    assert [r["session_id"] for r in records] == [first, first, second]
    assert current_session_id() == second


def test_re_enabling_does_not_duplicate_every_line(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started")

    assert len(_lines(tmp_path)) == 1


# -- 15: segredos ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "discord_token",
        "wcl_client_secret",
        "blizzard_client_secret",
        "access_token",
        "Authorization",
        "api_key",
        "refresh_token",
        "password",
    ],
)
def test_secret_shaped_fields_never_reach_the_file(tmp_path: Path, field: str) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started", **{field: "MEU-SEGREDO-REAL"})

    raw = (log_dir_for(tmp_path) / LOG_FILENAME).read_text(encoding="utf-8")
    assert "MEU-SEGREDO-REAL" not in raw
    assert _lines(tmp_path)[0][field] == REDACTED


def test_redaction_leaves_ordinary_fields_untouched(tmp_path: Path) -> None:
    """A redação é por nome de chave; não pode comer contexto operacional."""
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit(
        "job.started",
        job_id="job-1",
        cohort_id="f91dffaf13ddc899",
        report_code="FhYZDLMbwBVAx4KX",
        player="Fiskowl",
        channel_id="1529559571968954489",
    )

    record = _lines(tmp_path)[0]
    assert record["job_id"] == "job-1"
    assert record["report_code"] == "FhYZDLMbwBVAx4KX"
    assert record["player"] == "Fiskowl"


def test_console_output_is_also_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started", discord_token="MEU-SEGREDO-REAL")

    assert "MEU-SEGREDO-REAL" not in capsys.readouterr().err


# -- 18: console e arquivo coexistem ---------------------------------------------------


def test_console_logging_keeps_working_alongside_the_file_sink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("discord_bot.ready", user="GITGUD")

    assert "discord_bot.ready" in capsys.readouterr().err
    assert _lines(tmp_path)[0]["event"] == "discord_bot.ready"


def test_reconfiguring_the_console_does_not_blind_the_file_sink(tmp_path: Path) -> None:
    """Trocar o modo do console não pode desligar a evidência do soak."""
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    configure_logging(level="DEBUG", json_output=True)
    _emit("process.started")

    assert _lines(tmp_path)[0]["event"] == "process.started"


def test_disabling_the_sink_stops_writing(tmp_path: Path) -> None:
    configure_logging()
    enable_file_logging(tmp_path, max_bytes=BIG, backup_count=3)
    _emit("process.started")
    before = len(_lines(tmp_path))
    disable_file_logging()
    _emit("process.stopped")

    text = (log_dir_for(tmp_path) / LOG_FILENAME).read_text(encoding="utf-8")
    assert len([line for line in text.splitlines() if line.strip()]) == before
    assert current_session_id() is None


# -- fiação real do CLI ----------------------------------------------------------------


def test_cli_start_writes_process_started_with_the_session_id(tmp_path: Path) -> None:
    """A ponta que importa no soak: subir o processo já deixa a primeira linha
    e devolve o id pelo qual o run inteiro será filtrado depois.
    """
    import botgitgud.cli as cli_module

    configure_logging()
    settings = SimpleNamespace(data_dir=tmp_path, log_max_bytes=BIG, log_backup_count=3)

    session_id = cli_module._start_operational_log(settings, command="serve")  # type: ignore[arg-type]

    record = _lines(tmp_path)[0]
    assert record["event"] == "process.started"
    assert record["command"] == "serve"
    assert record["session_id"] == session_id
    assert record["log_file"].endswith(LOG_FILENAME)


# -- QA.0: prova que o mecanismo de proteção (não o guard antigo) funciona --
#
# A proteção real é `tests/conftest.py`'s `_protect_real_data_logs` (fixture
# de sessão, autouse) — ela roda sobre o `data/logs` REAL e por isso não
# pode ser exercitada aqui sem risco de falso positivo/negativo dependendo
# do que já existe na máquina. O que É testável aqui, contra um alvo
# sintético (`tmp_path`), é o mecanismo que essa fixture usa:
# `dir_snapshot.snapshot_directory`/`diff_snapshots`. Se este mecanismo tem
# um furo, a fixture de sessão herda o furo silenciosamente — daí a
# necessidade de uma guarda para a guarda (mesmo padrão de
# test_cadence.py's `test_the_reviewed_trinket_exception_selects_by_slot...`).


def test_directory_that_never_existed_stays_a_pass(tmp_path: Path) -> None:
    from dir_snapshot import diff_snapshots, snapshot_directory

    absent = tmp_path / "never_created"
    before = snapshot_directory(absent)
    after = snapshot_directory(absent)
    assert before.exists is False
    assert diff_snapshots(before, after) == []


def test_directory_created_after_not_existing_is_flagged(tmp_path: Path) -> None:
    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    before = snapshot_directory(target)  # ainda não existe
    target.mkdir()
    (target / "new.jsonl").write_text("hello\n", encoding="utf-8")
    after = snapshot_directory(target)

    violations = diff_snapshots(before, after)
    assert violations, "criar o diretório do nada deveria ser sinalizado"


def test_unchanged_directory_is_a_pass(tmp_path: Path) -> None:
    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    target.mkdir()
    (target / "botgitgud.jsonl").write_bytes(b'{"event": "real"}\n')
    (target / "supervisor.jsonl").write_bytes(b'{"event": "real2"}\n')

    before = snapshot_directory(target)
    after = snapshot_directory(target)  # nada mudou entre as duas leituras
    assert diff_snapshots(before, after) == []


def test_content_change_is_detected_even_with_identical_size(tmp_path: Path) -> None:
    """O caso que uma checagem só-de-tamanho deixaria passar: mesmo número
    de bytes, conteúdo diferente.
    """
    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    target.mkdir()
    log_file = target / "botgitgud.jsonl"
    log_file.write_bytes(b"AAAA")

    before = snapshot_directory(target)
    log_file.write_bytes(b"BBBB")  # mesmo tamanho, conteúdo diferente
    after = snapshot_directory(target)

    violations = diff_snapshots(before, after)
    assert any("conteúdo alterado" in v for v in violations)


def test_content_change_is_detected_even_when_mtime_does_not_move(tmp_path: Path) -> None:
    """A garantia central do QA.0: `os.utime` reescreve o mtime de volta ao
    valor original depois de mutar o conteúdo — se o mecanismo dependesse
    de mtime, isto passaria silenciosamente. Ele não depende.
    """
    import os

    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    target.mkdir()
    log_file = target / "botgitgud.jsonl"
    log_file.write_bytes(b'{"event": "original"}\n')
    original_stat = log_file.stat()

    before = snapshot_directory(target)
    log_file.write_bytes(b'{"event": "MUTATED"}\n')
    os.utime(log_file, (original_stat.st_atime, original_stat.st_mtime))
    after = snapshot_directory(target)

    violations = diff_snapshots(before, after)
    assert any("conteúdo alterado" in v for v in violations), (
        "mtime idêntico não deve mascarar uma mutação real de conteúdo"
    )


def test_file_removal_is_detected(tmp_path: Path) -> None:
    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    target.mkdir()
    (target / "botgitgud.jsonl").write_bytes(b"real evidence")

    before = snapshot_directory(target)
    (target / "botgitgud.jsonl").unlink()
    after = snapshot_directory(target)

    violations = diff_snapshots(before, after)
    assert any("removidos" in v for v in violations)


def test_new_file_addition_is_detected(tmp_path: Path) -> None:
    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    target.mkdir()
    (target / "botgitgud.jsonl").write_bytes(b"real evidence")

    before = snapshot_directory(target)
    (target / "sneaky_test_output.jsonl").write_bytes(b"a test forgot tmp_path")
    after = snapshot_directory(target)

    violations = diff_snapshots(before, after)
    assert any("criados" in v for v in violations)


def test_whole_directory_deletion_is_detected(tmp_path: Path) -> None:
    import shutil

    from dir_snapshot import diff_snapshots, snapshot_directory

    target = tmp_path / "logs"
    target.mkdir()
    (target / "botgitgud.jsonl").write_bytes(b"real evidence")

    before = snapshot_directory(target)
    shutil.rmtree(target)
    after = snapshot_directory(target)

    violations = diff_snapshots(before, after)
    assert any("apagado" in v for v in violations)
