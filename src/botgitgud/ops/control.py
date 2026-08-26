"""B6 — filesystem control channel between the supervisor process, the bot
process it supervises, and the operator's stop/start scripts.

Three files, one directory, all under `data_dir/control` (sibling to
`data_dir/logs`, already outside the tracked tree):

- `stop.request` — presence means "the operator wants the bot down". The
  supervisor checks it before every restart decision; the bot process polls it
  and calls `bot.close()` when it appears. Written by `stop-bot-service.ps1`,
  cleared only by `start-bot-service.ps1` — never by the processes that read
  it, so a lingering marker can't retrigger itself and a race between "the bot
  just saw it" and "the supervisor is deciding whether to restart" always
  resolves the same way for both readers.
- `supervisor.lock` — held via an OS-level byte-range lock (`msvcrt.locking`)
  for the supervisor's whole lifetime. Never touched by anything else; its
  content is irrelevant, only the lock matters.
- `bot.pid` — the PID of the currently-supervised bot process, written right
  after each spawn and removed once that PID is confirmed dead. It is how a
  separate `stop-bot-service.ps1` invocation (a different OS process from the
  supervisor) finds out what to wait for/escalate against, and how a fresh
  supervisor start recognizes a stale PID left over from an unclean previous
  shutdown.
"""

from __future__ import annotations

from pathlib import Path

CONTROL_DIRNAME = "control"
STOP_REQUEST_FILENAME = "stop.request"
LOCK_FILENAME = "supervisor.lock"
PID_FILENAME = "bot.pid"


def control_dir_for(data_dir: Path) -> Path:
    return Path(data_dir) / CONTROL_DIRNAME


def stop_request_path_for(data_dir: Path) -> Path:
    return control_dir_for(data_dir) / STOP_REQUEST_FILENAME


def lock_path_for(data_dir: Path) -> Path:
    return control_dir_for(data_dir) / LOCK_FILENAME


def pid_path_for(data_dir: Path) -> Path:
    return control_dir_for(data_dir) / PID_FILENAME


def stop_requested(data_dir: Path) -> bool:
    return stop_request_path_for(data_dir).exists()


def request_stop(data_dir: Path) -> None:
    """Idempotent: creating an already-existing marker is a no-op, never an
    error — a second `stop-bot-service.ps1` run while one is already pending
    must not fail.
    """
    path = stop_request_path_for(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def clear_stop_request(data_dir: Path) -> None:
    """Only `start-bot-service.ps1` calls this. Missing file is not an error:
    starting fresh (no prior stop) is the common case.
    """
    stop_request_path_for(data_dir).unlink(missing_ok=True)


def write_pid(data_dir: Path, pid: int) -> None:
    path = pid_path_for(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # tmp + replace, the same atomic pattern as ops_snapshot.py / report_store.py:
    # a reader (stop-bot-service.ps1, running as a separate process) must never
    # observe a half-written PID.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(pid), encoding="utf-8")
    tmp.replace(path)


def read_pid(data_dir: Path) -> int | None:
    path = pid_path_for(data_dir)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def clear_pid(data_dir: Path) -> None:
    pid_path_for(data_dir).unlink(missing_ok=True)
