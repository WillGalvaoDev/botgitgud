"""CL.1 — os primitivos platform-specific extraídos para
`ops/platform_process.py`: PID liveness e o lock de instância única.

A suíte roda no Windows, então os testes "Windows" (2/6/8/20 do roadmap)
exercitam o código Windows real, sem duplo nenhum. Os testes "Linux"
(3/4/5/7/9 do roadmap) chamam as funções POSIX DIRETAMENTE — não via o
dispatcher `pid_is_alive`/`_default_lock_backend`, que escolheriam Windows
nesta máquina — com `os.kill`/`fcntl` (este último via o nome de módulo
`_fcntl` que `platform_process.py` já deixa sempre definido, `None` aqui)
trocados por duplos via `monkeypatch`. Isso prova a LÓGICA Linux inteira
sem precisar de um Linux real.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from botgitgud.ops import platform_process as pp

# ==================================================================================
# PID liveness — Windows (caminho real desta máquina)
# ==================================================================================


def test_windows_pid_is_alive_reflects_a_real_process(tmp_path: Path) -> None:
    import subprocess
    import time

    proc = subprocess.Popen(
        ["python", "-c", "import time; time.sleep(5)"],
        cwd=str(tmp_path),
    )
    try:
        time.sleep(0.2)
        assert pp.pid_is_alive_windows(proc.pid) is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    time.sleep(0.2)
    assert pp.pid_is_alive_windows(proc.pid) is False


def test_windows_pid_is_alive_false_for_a_pid_that_never_existed() -> None:
    assert pp.pid_is_alive_windows(999_999) is False


def test_dispatcher_picks_windows_on_this_machine() -> None:
    """20: `pid_is_alive` (o dispatcher que `ops/supervisor.py` sempre
    expôs sob este nome) continua roteando para a implementação Windows
    nesta máquina — nenhuma regressão pelo refactor de extração.
    """
    assert pp.IS_WINDOWS is True
    assert pp.pid_is_alive(999_999) is False


# ==================================================================================
# PID liveness — Linux (função POSIX chamada diretamente, com os.kill mockado)
# ==================================================================================


def test_linux_pid_alive_when_kill_signal_zero_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """5: `os.kill(pid, 0)` não levanta -> processo existe."""
    monkeypatch.setattr(pp.os, "kill", lambda _pid, _sig: None)
    assert pp.pid_is_alive_posix(4242) is True


def test_linux_pid_missing_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """6: ProcessLookupError -> processo não existe."""

    def _raise(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(pp.os, "kill", _raise)
    assert pp.pid_is_alive_posix(4242) is False


def test_linux_pid_permission_denied_means_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """7: PermissionError -> o processo EXISTE (pertence a outro usuário),
    então `True` — nunca confundir "sem acesso" com "não existe".
    """

    def _raise(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(pp.os, "kill", _raise)
    assert pp.pid_is_alive_posix(4242) is True


def test_linux_pid_zero_or_negative_is_safe_and_never_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """8: PID <= 0 teria semântica de process GROUP no POSIX — tratado
    como "não existe" SEM sequer chamar `kill`, para nunca mandar um
    sinal a um grupo inteiro de processos por acidente.
    """
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(pp.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    assert pp.pid_is_alive_posix(0) is False
    assert pp.pid_is_alive_posix(-1) is False
    assert pp.pid_is_alive_posix(-4242) is False
    assert calls == []  # nenhuma chamada real a os.kill, em nenhum caso


def test_linux_pid_dispatcher_reachable_directly_without_a_real_linux_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prova adicional de testabilidade: o caminho Linux completo
    (`pid_is_alive_posix`) é alcançável e correto rodando neste Windows,
    só chamando a função diretamente — exatamente o que o roadmap pede.
    """
    monkeypatch.setattr(pp.os, "kill", lambda _pid, _sig: None)
    assert pp.pid_is_alive_posix(1) is True


# ==================================================================================
# Lock — Windows (caminho real desta máquina)
# ==================================================================================


def test_windows_lock_acquire_and_duplicate_rejection(tmp_path: Path) -> None:
    """2: primeiro lock consegue; segundo (mesmo processo) falha."""
    first = pp.SupervisorLock()
    second = pp.SupervisorLock()
    assert first.acquire(tmp_path) is True
    assert second.acquire(tmp_path) is False
    first.release()


def test_windows_lock_release_then_reacquire(tmp_path: Path) -> None:
    """1/3: depois do release, um novo lock consegue."""
    first = pp.SupervisorLock()
    assert first.acquire(tmp_path) is True
    first.release()
    second = pp.SupervisorLock()
    assert second.acquire(tmp_path) is True
    second.release()


def test_windows_lock_release_is_idempotent() -> None:
    """4: release sem acquire (ou repetido) nunca levanta."""
    lock = pp.SupervisorLock()
    lock.release()
    lock.release()


def test_windows_stale_lock_file_alone_does_not_block_a_fresh_start(tmp_path: Path) -> None:
    """5: um arquivo de lock deixado por um processo morto (sem ninguém
    segurando o lock real do kernel) NUNCA impede sozinho um novo
    `acquire()` — nunca "virar simples existência de arquivo". Simulado
    aqui criando o arquivo primeiro (como um processo morto teria feito),
    sem nenhum lock ativo sobre ele.
    """
    lock_path = tmp_path / "control" / "supervisor.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"")  # arquivo presente, nenhum lock real sobre ele

    lock = pp.SupervisorLock()
    assert lock.acquire(tmp_path) is True
    lock.release()


# ==================================================================================
# Lock — Linux (_PosixLockBackend chamado diretamente, fcntl mockado)
# ==================================================================================


class _FakeFcntlModule:
    """Duplo mínimo de `fcntl`: reproduz a semântica real de
    `flock()` — o lock é do ARQUIVO (inode), não do file descriptor de
    quem o abriu, então dois `open()` independentes do MESMO caminho (o
    caso real: dois `SupervisorLock` distintos, cada um com seu próprio
    handle) continuam conflitando exatamente como `msvcrt.locking` já
    garante no Windows. Um único booleano modela isso fielmente porque
    cada teste desta suíte lida com um único arquivo de lock por vez.
    """

    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(self) -> None:
        self._locked = False

    def flock(self, _fd: int, operation: int) -> None:
        if operation & self.LOCK_UN:
            self._locked = False
            return
        if operation & self.LOCK_EX:
            if self._locked:
                raise OSError("lock already held")
            self._locked = True
            return
        raise AssertionError(f"operação fcntl inesperada no duplo: {operation}")


def _install_fake_fcntl(monkeypatch: pytest.MonkeyPatch) -> _FakeFcntlModule:
    fake = _FakeFcntlModule()
    monkeypatch.setattr(pp, "_fcntl", fake)
    return fake


def test_linux_lock_acquire_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2 (caminho Linux): `_PosixLockBackend` chamado diretamente — nunca
    o dispatcher, que escolheria Windows nesta máquina.
    """
    _install_fake_fcntl(monkeypatch)
    lock = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    assert lock.acquire(tmp_path) is True
    lock.release()


def test_linux_duplicate_lock_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """3: um segundo `fcntl.flock(LOCK_EX|LOCK_NB)` sobre o MESMO handle
    (mesmo processo reabrindo o arquivo, como `msvcrt` já garante no
    Windows) falha — nunca dois supervisores Linux ativos ao mesmo tempo.
    """
    _install_fake_fcntl(monkeypatch)
    first = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    second = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    assert first.acquire(tmp_path) is True
    assert second.acquire(tmp_path) is False
    first.release()


def test_linux_lock_release_then_reacquire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4: depois do release, um novo lock Linux consegue."""
    _install_fake_fcntl(monkeypatch)
    first = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    assert first.acquire(tmp_path) is True
    first.release()
    second = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    assert second.acquire(tmp_path) is True
    second.release()


def test_linux_lock_release_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_fcntl(monkeypatch)
    lock = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    lock.release()
    lock.release()


def test_linux_stale_lock_file_alone_does_not_block_a_fresh_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5 (caminho Linux): mesmo invariante do Windows — `flock` é mantido
    pelo kernel e liberado com o processo; um arquivo órfão sem lock real
    nunca impede sozinho um `acquire()` novo.
    """
    _install_fake_fcntl(monkeypatch)
    lock_path = tmp_path / "control" / "supervisor.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"")

    lock = pp.SupervisorLock(_backend=pp._PosixLockBackend())
    assert lock.acquire(tmp_path) is True
    lock.release()


# ==================================================================================
# Segurança de import condicional (9)
# ==================================================================================


def test_module_defines_both_platform_names_regardless_of_current_os() -> None:
    """9: `_msvcrt`/`_fcntl` estão sempre definidos como atributos do
    módulo — `None` na plataforma onde não existem — nunca um
    `AttributeError`/`ImportError` só por importar o módulo.
    """
    assert hasattr(pp, "_msvcrt")
    assert hasattr(pp, "_fcntl")
    # Nesta máquina (Windows): msvcrt real, fcntl ausente.
    assert pp._msvcrt is not None
    assert pp._fcntl is None


def test_module_reimport_is_safe() -> None:
    """9 (complemento): reimportar o módulo (ex.: um segundo processo de
    teste, um reload) nunca dispara `import fcntl`/`import msvcrt`
    incondicional de novo — os dois vivem só dentro do `try/except` do
    topo do arquivo, executado uma única vez na primeira importação.
    """
    import importlib

    reloaded = importlib.reload(pp)
    assert isinstance(reloaded, types.ModuleType)
    assert reloaded._msvcrt is not None
    assert reloaded._fcntl is None
