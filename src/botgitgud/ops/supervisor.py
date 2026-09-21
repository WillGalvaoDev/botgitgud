"""B6 — supervision EXTERNAL to the bot process.

`bot/discord_bot.py::WorkerSupervisor` (B-something-earlier) answers "is there
a live consumer of the job queue *inside this process*?" — it cannot help if
the whole Python process dies. This module answers the question one level up:
"is `python -m botgitgud.cli serve` itself alive, and if not, why, and should
it come back?"

Design, portable across Windows and Linux (CL.1) and matched to this
project's existing conventions:

- **spawn**: `[sys.executable, "-m", "botgitgud.cli", "serve"]` with an
  explicit `cwd` — never relies on PATH or on the supervisor's own working
  directory happening to be right. `creationflags` only ever carries the
  Windows-only `CREATE_NO_WINDOW` flag when it actually exists (`getattr`
  fallback to `0`); `subprocess.Popen` rejects a non-zero `creationflags` on
  POSIX, so this stays `0`-and-therefore-silently-accepted there — verified
  against CPython's own `subprocess.py`, not assumed.
- **PID liveness / singleton lock**: the two genuinely platform-specific
  primitives (`os.kill(pid, 0)` does NOT detect death on Windows, so PID
  liveness uses `ctypes`/`OpenProcess` there instead; the exclusive lock is
  `msvcrt.locking` on Windows, `fcntl.flock` on POSIX) live in
  `ops/platform_process.py`, imported here — see that module's docstring for
  why they're split out (import safety on both platforms) and how tests
  exercise the POSIX path without a real POSIX machine.
- **clean stop**: no OS signal REQUIRED. `ops/control.py`'s `stop.request`
  file is the channel — the supervisor writes it (or a signal handler does,
  see below), the bot process polls it and calls `bot.close()` from inside
  its own event loop (graceful, from where DuckDB and the websocket are
  actually owned), and the supervisor just waits for the PID to disappear.
  `ChildHandle.terminate()` exists only as the escalation path if that grace
  period expires — never the normal path.
- **signals (CL.1)**: `install_signal_handlers` converts a delivered
  `SIGTERM`/`SIGINT` into the SAME `stop.request` file — never a second stop
  mechanism, never a direct `child.terminate()`. This exists for systemd
  (`systemctl stop` sends `SIGTERM` by default) and for an operator's Ctrl+C
  on either platform; the existing polling loop below picks it up on its very
  next iteration through the exact same code path `stop-bot-service.ps1`
  already exercises today, unchanged.
- **restart policy**: any exit while `stop.request` is absent is treated as
  unexpected (this process is meant to run forever; even an exit(0) nobody
  asked for is an anomaly for a long-running service) and triggers a restart,
  with exponential backoff and a storm breaker so a crash-loop can't spin the
  CPU or restart hundreds of times in a few minutes.
- **duplicate protection**: the OS-level lock (see above) stops a second
  supervisor from ever starting, and a PID-liveness check against
  `control/bot.pid` stops a restart from ever launching a second child even
  if two supervisors somehow raced past the lock.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import structlog

from botgitgud.ops.control import (
    clear_pid,
    control_dir_for,
    read_pid,
    request_stop,
    stop_requested,
    write_pid,
)
from botgitgud.ops.platform_process import SupervisorLock, pid_is_alive

log = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


def child_command(python_executable: str | None = None) -> list[str]:
    """`sys.executable` (not a hardcoded path, not PATH lookup): whatever
    interpreter is running the supervisor IS the venv interpreter, since the
    supervisor itself is only ever launched via the venv's own `python`
    (`.venv\\Scripts\\python.exe` on Windows, `.venv/bin/python` on Linux).
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


# -- signals (CL.1) -------------------------------------------------------------------


def _stop_request_signal_handler(data_dir: Path) -> Callable[[int, object], None]:
    """The handler `install_signal_handlers` registers — split out as its
    own factory so a test can call it directly with a fabricated signal
    number, never depending on real OS signal delivery (which differs too
    much between platforms to be safe inside a test: `os.kill(pid,
    SIGTERM)` on Windows calls `TerminateProcess` directly, bypassing any
    registered Python handler entirely, so a test that tried to prove this
    via a real delivered signal would just kill itself instead of
    exercising the handler).
    """

    def _handle(signum: int, _frame: object) -> None:
        log.info("supervisor.signal_received", signum=signum)
        request_stop(data_dir)

    return _handle


def install_signal_handlers(data_dir: Path) -> None:
    """CL.1: converts a delivered `SIGTERM`/`SIGINT` into the SAME
    `stop.request` file the operator's own scripts already write — never a
    second stop mechanism, never a direct `child.terminate()` from a
    handler (signal handlers should do as little as possible; a
    filesystem write is safe, `_stop_child_gracefully` is not — it sleeps
    and polls, and running that inside a signal handler would be fragile
    at best). `run_supervisor_loop`'s existing polling (already tested,
    unchanged) picks the file up on its very next iteration and follows
    the exact graceful path it always has.

    Exists for systemd (`systemctl stop` sends `SIGTERM` by default — the
    unit itself is CL.6, not built here) and for an operator's Ctrl+C on
    either platform. Registering unconditionally is safe on Windows too:
    `signal.SIGTERM`/`signal.SIGINT` are valid names there, nothing in
    practice sends a real `SIGTERM` to this process outside of it, and the
    existing Windows path (`stop-bot-service.ps1` -> `stop.request`)
    stays byte-for-byte the same either way.
    """
    handler = _stop_request_signal_handler(data_dir)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


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
        install_signal_handlers(data_dir)
        recover_stale_pid(data_dir)
        run_supervisor_loop(data_dir, resolved, spawn=spawn_bot_child)
    finally:
        lock.release()
    return 0
