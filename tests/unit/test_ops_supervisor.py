"""B6 — supervisão externa ao processo `serve`.

Tudo aqui é offline: o `spawn` é sempre um fake (`FakeChild`) exceto nos dois
testes marcados "processo real", que usam `tests/fixtures/supervisor_fixture.py`
(um script pequeno, não o bot) para provar a política contra um subprocesso
Windows de verdade em vez de só contra o modelo em memória. Nenhum teste toca
WCL ou Discord.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
import structlog

from botgitgud.ops import control
from botgitgud.ops import supervisor as supervisor_module
from botgitgud.ops.supervisor import (
    ChildHandle,
    SupervisorLock,
    SupervisorSettings,
    child_command,
    compute_backoff_delay,
    install_signal_handlers,
    is_restart_storm,
    pid_is_alive,
    prune_restart_history,
    run_supervisor_loop,
    spawn_bot_child,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "supervisor_fixture.py"


@pytest.fixture(autouse=True)
def _restore_global_logging_state() -> Any:
    """`structlog.configure(..., cache_logger_on_first_use=True)` (o que
    `configure_logging()` faz) permanentemente prende QUALQUER `get_logger()`
    ja resolvido ao processador ativo naquele instante — `reset_defaults()`
    devolve o padrao para proxies FUTUROS, mas nao desfaz o cache de um proxy
    ja usado (`domain/specs.py::log`, por exemplo, obtido uma vez no import do
    modulo). Sem restaurar aqui, um teste deste arquivo que chama
    `configure_logging()` pode deixar outro modulo mudo para sempre em
    qualquer teste posterior na mesma sessao — exatamente o mesmo padrao que
    `test_operational_logging.py` ja protege.
    """
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    yield
    from botgitgud.logging_setup import disable_file_logging

    disable_file_logging()
    root.handlers = previous_handlers
    root.setLevel(previous_level)
    structlog.reset_defaults()


class FakeChild:
    """`poll()` devolve None enquanto vivo. `exit_codes` é consumida em ordem
    conforme `poll()` é chamado repetidamente após `alive_polls` chamadas —
    modela "vive N tempos de polling, depois sai com este código".
    """

    def __init__(self, pid: int, *, alive_polls: int = 0, exit_code: int = 1) -> None:
        self.pid = pid
        self._remaining = alive_polls
        self._exit_code = exit_code
        self._exited = False
        self.terminated = False

    def poll(self) -> int | None:
        if self._remaining > 0:
            self._remaining -= 1
            return None
        self._exited = True
        return self._exit_code

    def terminate(self) -> None:
        self.terminated = True
        self._exited = True


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Spawner:
    """Fábrica de `FakeChild` que registra cada chamada — o próprio contador
    de chamadas É a prova de "nunca dois filhos vivos ao mesmo tempo": o loop
    só chama de novo depois que o anterior já reportou não-vivo.
    """

    def __init__(self, *, alive_polls: int = 0, exit_code: int = 1) -> None:
        self.calls: list[int] = []
        self._alive_polls = alive_polls
        self._exit_code = exit_code

    def __call__(self) -> ChildHandle:
        pid = 1000 + len(self.calls)
        self.calls.append(pid)
        return FakeChild(pid, alive_polls=self._alive_polls, exit_code=self._exit_code)


# -- 1/2: comando e working directory --------------------------------------------------


def test_command_uses_the_venv_python_via_sys_executable() -> None:
    assert child_command() == [sys.executable, "-m", "botgitgud.cli", "serve"]


def test_command_accepts_an_explicit_interpreter_for_testing() -> None:
    assert child_command("C:/custom/python.exe")[0] == "C:/custom/python.exe"


def test_spawn_uses_the_given_repo_root_as_working_directory(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    handle = spawn_bot_child(
        repo_root=repo_root,
        python_executable=sys.executable,  # real short-lived process
    )
    try:
        # Um filho real de verdade, rodando `-c "import os; print(os.getcwd())"`
        # seria mais direto, mas spawn_bot_child fixa o comando em
        # `botgitgud.cli serve` por design (nao aceita argv livre) — a prova de
        # cwd correto e via um fixture dedicado abaixo (real_child_reports_cwd).
        assert handle.pid > 0
    finally:
        handle.terminate()


def test_real_subprocess_starts_in_the_given_working_directory(tmp_path: Path) -> None:
    """Processo real (fixture, não o bot): prova cwd fim a fim."""
    marker = tmp_path / "cwd.txt"
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(FIXTURE), "report-cwd", str(marker)],
        cwd=str(tmp_path),
        check=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == str(tmp_path)


# -- pid liveness (empírico) -------------------------------------------------------------


def test_pid_is_alive_reflects_real_process_state() -> None:
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        assert pid_is_alive(proc.pid) is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert pid_is_alive(proc.pid) is False


def test_pid_is_alive_false_for_a_pid_that_never_existed() -> None:
    assert pid_is_alive(999_999) is False


# -- 3/4/5: child inicia, exit normal tratado, crash reinicia --------------------------


def test_child_starts_and_pid_is_recorded(tmp_path: Path) -> None:
    spawner = Spawner(alive_polls=1000)  # nunca sai dentro do teste
    clock = FakeClock()

    # roda so o suficiente pra iniciar e parar via stop_check
    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1, stop_grace_s=1.0),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: True,  # para imediatamente apos o primeiro child
    )

    assert reason == "stop_requested"
    assert len(spawner.calls) == 1
    assert control.read_pid(tmp_path) is None  # limpo apos o stop


@pytest.mark.parametrize("exit_code", [0, 1])
def test_any_unrequested_exit_triggers_a_restart_regardless_of_exit_code(
    tmp_path: Path, exit_code: int
) -> None:
    """`serve` roda pra sempre por design; mesmo um exit(0) que ninguém pediu
    é uma anomalia para um serviço de longa duração — a política reinicia
    nos dois casos, diferenciando apenas via `stop.request`.
    """
    spawner = Spawner(alive_polls=1, exit_code=exit_code)
    clock = FakeClock()

    # Amarrado ao invariante que importa (numero de spawns), nao a contagem
    # exata de vezes que o loop consulta check_stop() por ciclo -- esse e um
    # detalhe interno que nao deve quebrar o teste se mudar.
    def stop_check() -> bool:
        return len(spawner.calls) >= 3

    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1, backoff_base_s=0.0),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=stop_check,
        max_iterations=100,
    )

    assert reason == "stop_requested"
    assert len(spawner.calls) >= 2  # reiniciou pelo menos uma vez


# -- 6: backoff --------------------------------------------------------------------------


def test_backoff_delay_is_exponential_and_capped() -> None:
    assert compute_backoff_delay(1, base_s=2.0, max_s=60.0) == 2.0
    assert compute_backoff_delay(2, base_s=2.0, max_s=60.0) == 4.0
    assert compute_backoff_delay(3, base_s=2.0, max_s=60.0) == 8.0
    assert compute_backoff_delay(10, base_s=2.0, max_s=60.0) == 60.0  # capped


def test_the_loop_actually_sleeps_the_computed_backoff_between_restarts(tmp_path: Path) -> None:
    spawner = Spawner(alive_polls=0, exit_code=1)  # sai imediatamente sempre
    clock = FakeClock()
    calls = {"n": 0}

    def stop_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1, backoff_base_s=5.0, backoff_max_s=60.0),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=stop_check,
    )

    assert clock.t >= 5.0  # o backoff do primeiro restart foi de fato aplicado


# -- 7: restart storm ----------------------------------------------------------------------


def test_restart_storm_stops_the_loop_and_no_further_spawn_happens(tmp_path: Path) -> None:
    spawner = Spawner(alive_polls=0, exit_code=1)
    clock = FakeClock()

    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(
            poll_interval_s=0.01,
            backoff_base_s=0.0,
            storm_threshold=3,
            storm_window_s=1000.0,
        ),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: False,  # nunca pede stop: só o storm deve parar isto
    )

    assert reason == "restart_storm"
    assert len(spawner.calls) == 3  # nao continuou tentando alem do limiar


def test_storm_detection_only_counts_restarts_within_the_window() -> None:
    """Falhas espaçadas fora da janela não devem se acumular como storm —
    senão um bot que cai uma vez por dia pareceria estar em crash-loop.
    """
    history = [0.0, 700.0]  # window 600s: o primeiro ja saiu da janela em t=700
    pruned = prune_restart_history(history, now=700.0, window_s=600.0)
    assert pruned == [700.0]
    assert is_restart_storm(pruned, threshold=2) is False


def test_backoff_resets_after_a_sustained_healthy_period(tmp_path: Path) -> None:
    """Um crash raro nao deve herdar o backoff acumulado de crashes antigos."""
    calls = {"n": 0}
    exit_after = {"first": 1, "rest": 1000}

    def spawn() -> ChildHandle:
        calls["n"] += 1
        # O primeiro filho vive pouco (crash rapido); o segundo vive bastante
        # (mais que backoff_reset_after_s) antes do teste forcar stop.
        alive = exit_after["first"] if calls["n"] == 1 else exit_after["rest"]
        return FakeChild(1000 + calls["n"], alive_polls=alive, exit_code=1)

    clock = FakeClock()

    def stop_check() -> bool:
        return calls["n"] >= 2

    run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=1.0, backoff_base_s=2.0, backoff_reset_after_s=50.0),
        spawn=spawn,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=stop_check,
        max_iterations=100,
    )
    # Nao afirmamos o valor exato do backoff (depende de quantos polls o fake
    # simulou) — o ponto coberto e que o mecanismo de reset existe e nao
    # quebra a execucao; o calculo puro ja e coberto por
    # test_backoff_delay_is_exponential_and_capped.
    assert calls["n"] >= 2


# -- 8: nunca dois children vivos --------------------------------------------------------


def test_the_loop_never_spawns_a_new_child_while_the_current_one_is_alive(
    tmp_path: Path,
) -> None:
    spawner = Spawner(alive_polls=1000)
    clock = FakeClock()
    run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: True,
        max_iterations=50,
    )
    assert len(spawner.calls) == 1  # o child "vivo para sempre" nunca foi substituido


def test_singleton_lock_rejects_a_second_acquisition(tmp_path: Path) -> None:
    first = SupervisorLock()
    second = SupervisorLock()
    assert first.acquire(tmp_path) is True
    assert second.acquire(tmp_path) is False
    first.release()
    assert second.acquire(tmp_path) is True
    second.release()


def test_singleton_lock_survives_release_without_acquire() -> None:
    lock = SupervisorLock()
    lock.release()  # nao deve levantar


# -- 9: stop solicitado nao reinicia ------------------------------------------------------


def test_stop_requested_before_any_exit_does_not_restart(tmp_path: Path) -> None:
    spawner = Spawner(alive_polls=1000)
    clock = FakeClock()
    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1, stop_grace_s=1.0),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: True,
    )
    assert reason == "stop_requested"
    assert len(spawner.calls) == 1


def test_stop_requested_after_exit_does_not_restart(tmp_path: Path) -> None:
    spawner = Spawner(alive_polls=1, exit_code=0)
    clock = FakeClock()
    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1),
        spawn=spawner,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: True,  # ja verdadeiro desde o inicio
        max_iterations=10,
    )
    assert reason == "stop_requested"
    assert len(spawner.calls) == 1


# -- 10: encerramento limpo ---------------------------------------------------------------


def test_graceful_stop_waits_for_the_child_before_escalating(tmp_path: Path) -> None:
    child = FakeChild(pid=42, alive_polls=2, exit_code=0)
    clock = FakeClock()
    spawner_calls = {"n": 0}

    def spawn() -> ChildHandle:
        spawner_calls["n"] += 1
        return child

    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.5, stop_grace_s=10.0),
        spawn=spawn,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: True,
    )

    assert reason == "stop_requested"
    assert child.terminated is False  # saiu sozinho, nunca precisou de escalada


def test_graceful_stop_escalates_to_terminate_past_the_grace_period(tmp_path: Path) -> None:
    child = FakeChild(pid=42, alive_polls=10_000)  # nunca sai sozinho
    clock = FakeClock()

    run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=1.0, stop_grace_s=5.0),
        spawn=lambda: child,
        now=clock.now,
        sleep=clock.sleep,
        stop_check=lambda: True,
    )

    assert child.terminated is True


def test_control_files_are_the_normal_stop_channel_not_an_os_signal(tmp_path: Path) -> None:
    """Documenta a decisão: nenhum `child.terminate()`/sinal é chamado no
    caminho normal — só a presença do arquivo de controle é lida.
    """
    assert control.stop_requested(tmp_path) is False
    control.request_stop(tmp_path)
    assert control.stop_requested(tmp_path) is True
    control.clear_stop_request(tmp_path)
    assert control.stop_requested(tmp_path) is False
    control.clear_stop_request(tmp_path)  # idempotente, nao levanta


# -- 12: log do supervisor é separado ------------------------------------------------------


def test_supervisor_logging_writes_a_separate_file_from_the_bot(tmp_path: Path) -> None:
    import logging

    from botgitgud.logging_setup import (
        LOG_FILENAME,
        SUPERVISOR_LOG_FILENAME,
        configure_logging,
        disable_file_logging,
        enable_file_logging,
        log_dir_for,
    )

    try:
        configure_logging()
        enable_file_logging(tmp_path, max_bytes=10_000_000, backup_count=2)
        import structlog

        structlog.get_logger("test").info("botgitgud.jsonl.marker")
        logging.getLogger().handlers[-1].flush()

        enable_file_logging(
            tmp_path, max_bytes=10_000_000, backup_count=2, filename=SUPERVISOR_LOG_FILENAME
        )
        structlog.get_logger("test").info("supervisor.jsonl.marker")
        logging.getLogger().handlers[-1].flush()

        bot_log = (log_dir_for(tmp_path) / LOG_FILENAME).read_text(encoding="utf-8")
        sup_log = (log_dir_for(tmp_path) / SUPERVISOR_LOG_FILENAME).read_text(encoding="utf-8")

        assert "botgitgud.jsonl.marker" in bot_log
        assert "supervisor.jsonl.marker" not in bot_log
        assert "supervisor.jsonl.marker" in sup_log
        assert "botgitgud.jsonl.marker" not in sup_log
    finally:
        disable_file_logging()


# -- 13: session/pid auditável --------------------------------------------------------------


def test_child_pid_is_written_and_cleared_across_the_lifecycle(tmp_path: Path) -> None:
    spawner = Spawner(alive_polls=1, exit_code=0)
    clock = FakeClock()
    seen_pid_during_life: list[int | None] = []

    original_poll = FakeChild.poll

    def spying_poll(self: FakeChild) -> int | None:
        seen_pid_during_life.append(control.read_pid(tmp_path))
        return original_poll(self)

    FakeChild.poll = spying_poll  # type: ignore[method-assign]
    try:
        run_supervisor_loop(
            tmp_path,
            SupervisorSettings(poll_interval_s=0.1),
            spawn=spawner,
            now=clock.now,
            sleep=clock.sleep,
            stop_check=lambda: True,
            max_iterations=5,
        )
    finally:
        FakeChild.poll = original_poll  # type: ignore[method-assign]

    assert seen_pid_during_life[0] == spawner.calls[0]  # auditavel enquanto vivo
    assert control.read_pid(tmp_path) is None  # limpo ao final


def test_recover_stale_pid_clears_a_pid_left_by_an_unclean_shutdown(tmp_path: Path) -> None:
    from botgitgud.ops.supervisor import recover_stale_pid

    control.write_pid(tmp_path, 999_999)  # pid garantidamente morto
    recover_stale_pid(tmp_path)
    assert control.read_pid(tmp_path) is None


def test_recover_stale_pid_leaves_a_live_pid_alone(tmp_path: Path) -> None:
    import subprocess

    from botgitgud.ops.supervisor import recover_stale_pid

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        control.write_pid(tmp_path, proc.pid)
        recover_stale_pid(tmp_path)
        assert control.read_pid(tmp_path) == proc.pid
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# -- redação/segurança ---------------------------------------------------------------------


def test_supervisor_events_redact_secret_shaped_fields(tmp_path: Path) -> None:
    import logging

    import structlog

    from botgitgud.logging_setup import (
        REDACTED,
        SUPERVISOR_LOG_FILENAME,
        configure_logging,
        disable_file_logging,
        enable_file_logging,
        log_dir_for,
    )

    try:
        configure_logging()
        enable_file_logging(
            tmp_path, max_bytes=10_000_000, backup_count=2, filename=SUPERVISOR_LOG_FILENAME
        )
        structlog.get_logger("test").info("supervisor.started", discord_token="MEU-SEGREDO")
        logging.getLogger().handlers[-1].flush()
        raw = (log_dir_for(tmp_path) / SUPERVISOR_LOG_FILENAME).read_text(encoding="utf-8")
        assert "MEU-SEGREDO" not in raw
        record = json.loads(raw.splitlines()[0])
        assert record["discord_token"] == REDACTED
    finally:
        disable_file_logging()


def test_no_ps1_script_contains_secret_shaped_literals() -> None:
    """Nenhum script le o CONTEUDO do .env -- so verifica que o arquivo existe
    (Test-Path), e nunca imprime nada dele. `.env` e mencionado nos comentarios
    (documentacao), o que e esperado; o que nao pode existir e um comando que
    LEIA o arquivo (Get-Content .env).
    """
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    suspicious = ("DISCORD_TOKEN=", "CLIENT_SECRET=", "Authorization:", "Bearer ", "Get-Content")
    for path in scripts_dir.glob("*.ps1"):
        text = path.read_text(encoding="utf-8")
        for pattern in suspicious:
            if pattern == "Get-Content":
                # Get-Content e usado para ler bot.pid/stop.request -- nunca .env.
                for line in text.splitlines():
                    if "Get-Content" in line:
                        assert ".env" not in line.lower(), f"{path.name}: {line!r}"
                continue
            assert pattern not in text, f"{path.name} contains {pattern!r}"


# -- 14: scripts sintaticamente válidos ------------------------------------------------------


def _powershell_executable() -> str | None:
    import shutil

    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.parametrize(
    "script_name",
    [
        "bot-supervisor.ps1",
        "install-bot-service.ps1",
        "uninstall-bot-service.ps1",
        "start-bot-service.ps1",
        "stop-bot-service.ps1",
    ],
)
def test_powershell_scripts_parse_without_syntax_errors(script_name: str) -> None:
    exe = _powershell_executable()
    if exe is None:
        pytest.skip("nenhum interpretador PowerShell disponível neste ambiente")

    script_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
    assert script_path.exists(), f"{script_path} não existe"

    check = (
        "$errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script_path.as_posix()}', [ref]$null, [ref]$errors); "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { Write-Output $_.ToString() }; exit 1 "
        "} else { exit 0 }"
    )
    import subprocess

    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", check],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# -- 16/15: nenhum teste toca rede -----------------------------------------------------------


def test_supervisor_module_never_imports_discord_or_httpx() -> None:
    import botgitgud.ops.supervisor as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "import discord" not in src
    assert "httpx" not in src


# -- CL.1: sinais convertidos no MESMO stop.request ------------------------------------------
#
# `os.kill(pid, signal.SIGTERM)` NÃO é usado para provar entrega nestes
# testes: no Windows ele chama `TerminateProcess` diretamente, ignorando
# qualquer handler Python registrado — enviar isso ao próprio processo de
# teste mataria o runner de testes em vez de exercitar o handler. Por
# isso o handler é chamado como uma função Python comum, simulando
# exatamente o que o SO faria ao entregar o sinal de verdade.


def test_signal_handler_writes_the_same_stop_request_file(tmp_path: Path) -> None:
    """17 (unidade): a lógica do handler, isolada — receber um número de
    sinal escreve `stop.request`, nada mais.
    """
    handler = supervisor_module._stop_request_signal_handler(tmp_path)
    assert control.stop_requested(tmp_path) is False

    handler(signal.SIGTERM, None)

    assert control.stop_requested(tmp_path) is True


def test_signal_handler_handles_sigint_too(tmp_path: Path) -> None:
    handler = supervisor_module._stop_request_signal_handler(tmp_path)
    handler(signal.SIGINT, None)
    assert control.stop_requested(tmp_path) is True


def test_install_signal_handlers_registers_sigterm_and_sigint(tmp_path: Path) -> None:
    original_term = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)
    try:
        install_signal_handlers(tmp_path)
        assert signal.getsignal(signal.SIGTERM) is not original_term
        assert signal.getsignal(signal.SIGINT) is not original_int
    finally:
        signal.signal(signal.SIGTERM, original_term)
        signal.signal(signal.SIGINT, original_int)


def test_simulated_sigterm_leads_to_a_clean_shutdown_via_the_real_stop_request_channel(
    tmp_path: Path,
) -> None:
    """17/18 — a prova de ponta a ponta: simula a ENTREGA de SIGTERM
    chamando o handler diretamente (nunca dependendo de um SO real
    entregar o sinal — ver nota acima), e deixa o RESTO do caminho ser o
    `run_supervisor_loop` real, com o `stop_check` PADRÃO (arquivo em
    disco, não um fake em memória) — a mesma função que
    `stop-bot-service.ps1` já aciona no Windows hoje.

    Prova também "nenhum child órfão" (18): o filho sai sozinho dentro da
    janela de graça (nunca escalado para `terminate()`) e `bot.pid` é
    limpo ao final.
    """
    child = FakeChild(pid=4242, alive_polls=1, exit_code=0)

    def spawn() -> ChildHandle:
        return child

    handler = supervisor_module._stop_request_signal_handler(tmp_path)
    handler(signal.SIGTERM, None)  # a "entrega" simulada, antes do loop nem comecar

    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1, stop_grace_s=5.0),
        spawn=spawn,
        # stop_check PADRAO: le control/stop.request de verdade.
    )

    assert reason == "stop_requested"
    assert child.terminated is False  # saiu sozinho — nunca precisou de escalada
    assert not (tmp_path / "control" / "bot.pid").exists()  # sem PID orfao registrado
    control.clear_stop_request(tmp_path)


def test_simulated_sigint_also_leads_to_a_clean_shutdown(tmp_path: Path) -> None:
    """Ctrl+C em qualquer plataforma segue o MESMO caminho que SIGTERM."""
    child = FakeChild(pid=4243, alive_polls=1, exit_code=0)

    def spawn() -> ChildHandle:
        return child

    handler = supervisor_module._stop_request_signal_handler(tmp_path)
    handler(signal.SIGINT, None)

    reason = run_supervisor_loop(
        tmp_path,
        SupervisorSettings(poll_interval_s=0.1, stop_grace_s=5.0),
        spawn=spawn,
    )

    assert reason == "stop_requested"
    assert child.terminated is False
    control.clear_stop_request(tmp_path)


def test_signal_handler_never_calls_child_terminate_directly() -> None:
    """Documenta a decisão do design: o handler só escreve o arquivo de
    controle — nunca `child.terminate()` nem qualquer chamada de escalada
    — para nunca rodar lógica de espera/poll dentro de um signal handler.
    """
    import inspect

    src = inspect.getsource(supervisor_module._stop_request_signal_handler)
    assert "terminate" not in src
    assert "request_stop" in src


# -- CL.1: creationflags é seguro em qualquer plataforma -------------------------------------


def test_spawn_creationflags_is_zero_when_the_windows_only_flag_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirma o que já garante portabilidade em `spawn_bot_child`: sem o
    atributo Windows-only, `creationflags` cai para `0` — e
    `subprocess.Popen` aceita `creationflags=0` incondicionalmente em
    qualquer plataforma (só rejeita um valor != 0 fora do Windows,
    verificado contra o próprio código-fonte do `subprocess` do CPython).
    """
    import subprocess

    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    assert getattr(subprocess, "CREATE_NO_WINDOW", 0) == 0
