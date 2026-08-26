"""B6 — supervision EXTERNAL to the bot process.

`bot/discord_bot.py::WorkerSupervisor` (B-something-earlier) answers "is there
a live consumer of the job queue *inside this process*?" — it cannot help if
the whole Python process dies. This module answers the question one level up:
"is `python -m botgitgud.cli serve` itself alive, and if not, why, and should
it come back?"

Design, matched to Windows and to this project's existing conventions:

- **spawn**: `[sys.executable, "-m", "botgitgud.cli", "serve"]` with an
  explicit `cwd` — never relies on PATH or on the supervisor's own working
  directory happening to be right (verified empirically, see the two
  functions below: `os.kill(pid, 0)` does NOT detect death on Windows, so PID
  liveness uses `ctypes`/`OpenProcess` instead).
- **clean stop**: no OS signal at all. `ops/control.py`'s `stop.request` file
  is the channel — the supervisor writes it, the bot process polls it and
  calls `bot.close()` from inside its own event loop (graceful, from where
  DuckDB and the websocket are actually owned), and the supervisor just waits
  for the PID to disappear. `ChildHandle.terminate()` exists only as the
  escalation path if that grace period expires — never the normal path.
- **restart policy**: any exit while `stop.request` is absent is treated as
  unexpected (this process is meant to run forever; even an exit(0) nobody
  asked for is an anomaly for a long-running service) and triggers a restart,
  with exponential backoff and a storm breaker so a crash-loop can't spin the
  CPU or restart hundreds of times in a few minutes.
- **duplicate protection**: an OS-level byte-range lock
  (`msvcrt.locking`, verified empirically to reject a second acquisition —
  see docs/v1-process-supervision.md) stops a second supervisor from ever
  starting, and a PID-liveness check against `control/bot.pid` stops a
  restart from ever launching a second child even if two supervisors somehow
  raced past the lock.
"""

from __future__ import annotations

import contextlib
import ctypes
import msvcrt
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

import structlog

from botgitgud.ops.control import (
    clear_pid,
    control_dir_for,
    lock_path_for,
    read_pid,
    stop_requested,
    write_pid,
)

log = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def pid_is_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` does NOT raise for a dead PID on Windows (verified
    empirically) — it only works as a liveness probe on POSIX. `OpenProcess` +
    `GetExitCodeProcess` is the real check.
    """
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def child_command(python_executable: str | None = None) -> list[str]:
    """`sys.executable` (not a hardcoded path, not PATH lookup): whatever
    interpreter is running the supervisor IS the venv interpreter, since the
    supervisor itself is only ever launched via `.venv\\Scripts\\python.exe`.
    """
    return [python_executable or sys.executable, "-m", "botgitgud.cli", "serve"]


class ChildHandle(Protocol):
    """The slice of `subprocess.Popen` the loop actually needs — small enough
    that tests can fake it without spawning a real process.
    """

    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...


class SubprocessChildHandle:
    """Real implementation, wrapping `subprocess.Popen`."""

    def __init__(self, popen: subprocess.Popen[bytes]) -> None:
        self._popen = popen

    @property
    def pid(self) -> int:
        return self._popen.pid

    def poll(self) -> int | None:
        return self._popen.poll()

    def terminate(self) -> None:
        """Escalation only — `Popen.terminate()` is `TerminateProcess` on
        Windows: no exception in the child, no cleanup, no `process.stopped`
        line. Never the normal stop path (see module docstring).
        """
        self._popen.terminate()


def spawn_bot_child(
    *, repo_root: Path = REPO_ROOT, python_executable: str | None = None
) -> ChildHandle:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    popen = subprocess.Popen(
        child_command(python_executable),
        cwd=str(repo_root),
        creationflags=creationflags,
    )
    return SubprocessChildHandle(popen)


# -- pure policy: backoff and storm detection ----------------------------------------


def compute_backoff_delay(attempt: int, *, base_s: float, max_s: float) -> float:
    """Exponential, capped. `attempt` is 1-indexed (the first restart after a
    crash is attempt 1). Pure function: no clock, no I/O, trivially testable.
    """
    if attempt < 1:
        return 0.0
    return min(base_s * (2 ** (attempt - 1)), max_s)


def prune_restart_history(history: Sequence[float], *, now: float, window_s: float) -> list[float]:
    return [t for t in history if now - t <= window_s]


def is_restart_storm(history: Sequence[float], *, threshold: int) -> bool:
    return len(history) >= threshold


# -- singleton lock ---------------------------------------------------------------------


@dataclass
class SupervisorLock:
    """OS-level byte-range lock on `control/supervisor.lock`. Verified
    empirically: a second `locking()` call on the same byte range — even from
    the same process re-opening the file — raises `PermissionError`, which is
    exactly the "someone else already holds this" signal we want.
    """

    _handle: IO[bytes] | None = None

    def acquire(self, data_dir: Path) -> bool:
        path = lock_path_for(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        handle = path.open("r+b")
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        with contextlib.suppress(OSError):
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
        self._handle = None


# -- the loop -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupervisorSettings:
    poll_interval_s: float = 2.0
    backoff_base_s: float = 2.0
    backoff_max_s: float = 60.0
    # Um crash raro nao deve herdar o backoff acumulado de crashes antigos: se o
    # filho ficou de pe por mais que isto, o proximo restart volta ao attempt 1.
    backoff_reset_after_s: float = 300.0
    storm_threshold: int = 5
    storm_window_s: float = 600.0
    stop_grace_s: float = 30.0


@dataclass
class _LoopState:
    child: ChildHandle
    started_at: float
    attempt: int = 0
    restart_history: list[float] = field(default_factory=list)


def run_supervisor_loop(
    data_dir: Path,
    settings: SupervisorSettings,
    *,
    spawn: Callable[[], ChildHandle],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    stop_check: Callable[[], bool] | None = None,
    max_iterations: int | None = None,
) -> str:
    """Runs until the child is deliberately stopped or a restart storm is
    detected. Returns the stop reason (`"stop_requested"` /
    `"restart_storm"` / `"max_iterations"` — the last only exists for tests).

    `stop_check` defaults to polling `control/stop.request`; tests inject a
    plain callable so the whole loop runs without real file I/O for the pure
    policy scenarios, while a couple of tests exercise the real file-based
    default directly.
    """
    check_stop = stop_check or (lambda: stop_requested(data_dir))

    child = spawn()
    write_pid(data_dir, child.pid)
    log.info("child.started", child_pid=child.pid, attempt=0)
    state = _LoopState(child=child, started_at=now())

    iterations = 0
    while True:
        iterations += 1
        if max_iterations is not None and iterations > max_iterations:
            return "max_iterations"

        if check_stop():
            _stop_child_gracefully(data_dir, state.child, settings, now=now, sleep=sleep)
            log.info("supervisor.stopped", reason="stop_requested")
            return "stop_requested"

        exit_code = state.child.poll()
        if exit_code is None:
            sleep(settings.poll_interval_s)
            continue

        clear_pid(data_dir)
        uptime_s = now() - state.started_at
        log.info(
            "child.exited",
            child_pid=state.child.pid,
            exit_code=exit_code,
            uptime_s=round(uptime_s, 1),
        )

        if check_stop():
            log.info("supervisor.stopped", reason="stop_requested")
            return "stop_requested"

        if uptime_s >= settings.backoff_reset_after_s:
            state.attempt = 0

        state.attempt += 1
        history_so_far = prune_restart_history(
            state.restart_history, now=now(), window_s=settings.storm_window_s
        )
        state.restart_history = [*history_so_far, now()]
        if is_restart_storm(state.restart_history, threshold=settings.storm_threshold):
            log.warning(
                "restart_storm_detected",
                count=len(state.restart_history),
                window_s=settings.storm_window_s,
            )
            log.info("supervisor.stopped", reason="restart_storm")
            return "restart_storm"

        delay = compute_backoff_delay(
            state.attempt, base_s=settings.backoff_base_s, max_s=settings.backoff_max_s
        )
        log.info("child.restart_scheduled", attempt=state.attempt, delay_s=round(delay, 1))
        sleep(delay)

        if check_stop():
            log.info("supervisor.stopped", reason="stop_requested_during_backoff")
            return "stop_requested"

        new_child = spawn()
        write_pid(data_dir, new_child.pid)
        log.info("child.restarted", child_pid=new_child.pid, attempt=state.attempt)
        state.child = new_child
        state.started_at = now()


def _stop_child_gracefully(
    data_dir: Path,
    child: ChildHandle,
    settings: SupervisorSettings,
    *,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """`stop.request` was already written by the operator's script by the time
    the loop notices it — this just waits for the graceful exit and escalates
    to a hard kill only past the grace period, never as the first move.
    """
    deadline = now() + settings.stop_grace_s
    while child.poll() is None and now() < deadline:
        sleep(min(settings.poll_interval_s, max(deadline - now(), 0)))
    if child.poll() is None:
        log.warning("child.stop_grace_period_exceeded", child_pid=child.pid)
        child.terminate()
    clear_pid(data_dir)


def recover_stale_pid(data_dir: Path) -> None:
    """Boot-time symmetry with `JobQueue.recover_from_crash()`: a PID file left
    over from an unclean previous shutdown (machine power loss, forced kill)
    must not be mistaken for a live child by anything reading it before the
    loop writes a fresh one.
    """
    pid = read_pid(data_dir)
    if pid is not None and not pid_is_alive(pid):
        log.info("supervisor.stale_pid_cleared", child_pid=pid)
        clear_pid(data_dir)


def main(
    data_dir: Path,
    settings: SupervisorSettings | None = None,
    *,
    log_max_bytes: int = 10_000_000,
    log_backup_count: int = 5,
) -> int:
    """`cli.py`'s `_cmd_supervise` calls this after loading `Settings()` — kept
    thin and Settings-agnostic beyond logging config so the pure loop
    (`run_supervisor_loop`) stays easy to test.
    """
    from botgitgud.logging_setup import SUPERVISOR_LOG_FILENAME, enable_file_logging

    resolved = settings or SupervisorSettings()
    control_dir_for(data_dir).mkdir(parents=True, exist_ok=True)
    enable_file_logging(
        data_dir,
        max_bytes=log_max_bytes,
        backup_count=log_backup_count,
        filename=SUPERVISOR_LOG_FILENAME,
    )
    log.info("supervisor.started", data_dir=str(data_dir))

    lock = SupervisorLock()
    if not lock.acquire(data_dir):
        log.warning("supervisor.duplicate_detected")
        return 1

    try:
        recover_stale_pid(data_dir)
        run_supervisor_loop(data_dir, resolved, spawn=spawn_bot_child)
    finally:
        lock.release()
    return 0
