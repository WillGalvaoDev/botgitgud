"""CL.1 — os primitivos platform-specific extraídos para
`ops/platform_process.py`: PID liveness e o lock de instância única.

Este arquivo roda em Windows E em Linux (o CI é uma matriz), então cada
teste declara explicitamente de qual contrato ele fala:

* **Windows-only** (`requires_windows`): exercitam API nativa que só existe
  no Windows — `ctypes.windll`, `msvcrt`. Em Linux aparecem como `skipped`
  com razão explícita, nunca como falso verde.
* **Plataforma atual** (seção "Lock"): usam o backend REAL que o dispatcher
  escolhe onde o teste está rodando — `msvcrt` no Windows, `fcntl` no
  Linux. O contrato ("no máximo um supervisor", stale file não bloqueia)
  é o mesmo nos dois, e agora é provado nativamente nos dois.
* **Lógica POSIX a partir de qualquer host** (3/4/5/7/9 do roadmap): chamam
  as funções POSIX DIRETAMENTE — não via o dispatcher, que escolheria
  Windows numa máquina Windows — com `os.kill`/`fcntl` trocados por duplos
  via `monkeypatch` (`_fcntl` é um nome que `platform_process.py` deixa
  sempre definido justamente para isso). Continuam valendo em Linux: são
  sobre a lógica, não sobre o kernel.
* **Simétricos** (dispatcher, import condicional): afirmam o invariante dos
  DOIS lados — em cada plataforma, o backend daquela plataforma é o
  escolhido e o da outra é `None`. Não são skipados em lugar nenhum.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from botgitgud.ops import platform_process as pp

requires_windows = pytest.mark.skipif(
    not pp.IS_WINDOWS,
    reason="exercita API nativa do Windows (ctypes.windll / msvcrt), inexistente em POSIX",
)


# ==================================================================================
# PID liveness — Windows (API nativa; só executa no Windows)
# ==================================================================================


@requires_windows
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


@requires_windows
def test_windows_pid_is_alive_false_for_a_pid_that_never_existed() -> None:
    assert pp.pid_is_alive_windows(999_999) is False


# ==================================================================================
# Dispatchers — contrato simétrico, verificado em QUALQUER plataforma
# ==================================================================================


def test_pid_dispatcher_routes_to_the_current_platform_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """20: `pid_is_alive` (o dispatcher que `ops/supervisor.py` sempre expôs
    sob este nome) roteia para a implementação DA PLATAFORMA ATUAL —
    Windows no Windows, POSIX em qualquer POSIX.

    A versão anterior deste teste afirmava `IS_WINDOWS is True`, isto é,
    "esta máquina é Windows". Isso não era o contrato do dispatcher; era uma
    propriedade do host de desenvolvimento, e passou a ser falsa quando o CI
    virou matriz. Aqui as duas implementações são substituídas por sentinelas
    e o teste prova qual delas foi de fato chamada — o que vale nos dois SOs
    sem afrouxar nada.
    """
    called: list[str] = []

    def _windows(_pid: int) -> bool:
        called.append("windows")
        return False

    def _posix(_pid: int) -> bool:
        called.append("posix")
        return False

    monkeypatch.setattr(pp, "pid_is_alive_windows", _windows)
    monkeypatch.setattr(pp, "pid_is_alive_posix", _posix)

    assert pp.pid_is_alive(999_999) is False
    assert called == (["windows"] if pp.IS_WINDOWS else ["posix"])


def test_pid_dispatcher_reports_a_pid_that_never_existed_as_dead() -> None:
    """O mesmo comportamento observável que o teste original checava, agora
    sem duplo nenhum e sem depender de qual SO está rodando: um PID que nunca
    existiu é reportado como morto pela implementação REAL da plataforma.
    """
    assert pp.pid_is_alive(999_999) is False


def test_lock_backend_dispatcher_picks_the_current_platform_backend() -> None:
    """A outra metade do mesmo contrato: `_default_lock_backend` escolhe o
    backend nativo da plataforma atual. Sem isto, um erro de despacho no lock
    só apareceria como comportamento errado em produção.
    """
    backend = pp._default_lock_backend()
    expected = pp._WindowsLockBackend if pp.IS_WINDOWS else pp._PosixLockBackend
    assert isinstance(backend, expected)


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
    (`pid_is_alive_posix`) é alcançável e correto mesmo rodando num host
    Windows, só chamando a função diretamente — exatamente o que o roadmap
    pede, e o que garante que a lógica POSIX não dependia de um Linux real
    para ser exercitada.
    """
    monkeypatch.setattr(pp.os, "kill", lambda _pid, _sig: None)
    assert pp.pid_is_alive_posix(1) is True


# ==================================================================================
# Lock — backend REAL da plataforma atual (msvcrt no Windows, fcntl no Linux)
# ==================================================================================
#
# Estes quatro usam `SupervisorLock()` com o backend default, que é escolhido
# por plataforma. O nome antigo dizia "windows", mas isso só era verdade no
# host de desenvolvimento: no runner Linux eles exercitam `_PosixLockBackend`
# — passariam verdes testando outra coisa que o nome prometia. Renomeados para
# dizer o que de fato provam. As assertions não mudaram: o contrato é o mesmo
# nos dois SOs, e agora é verificado nativamente nos dois.


def test_lock_acquire_and_duplicate_rejection(tmp_path: Path) -> None:
    """2: primeiro lock consegue; segundo (mesmo processo) falha."""
    first = pp.SupervisorLock()
    second = pp.SupervisorLock()
    assert first.acquire(tmp_path) is True
    assert second.acquire(tmp_path) is False
    first.release()


def test_lock_release_then_reacquire(tmp_path: Path) -> None:
    """1/3: depois do release, um novo lock consegue."""
    first = pp.SupervisorLock()
    assert first.acquire(tmp_path) is True
    first.release()
    second = pp.SupervisorLock()
    assert second.acquire(tmp_path) is True
    second.release()


def test_lock_release_is_idempotent() -> None:
    """4: release sem acquire (ou repetido) nunca levanta."""
    lock = pp.SupervisorLock()
    lock.release()
    lock.release()


def test_stale_lock_file_alone_does_not_block_a_fresh_start(tmp_path: Path) -> None:
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


def _assert_only_current_platform_module_is_loaded(msvcrt_attr: object, fcntl_attr: object) -> None:
    """O invariante simétrico, num lugar só: exatamente o módulo nativo da
    plataforma atual está carregado, e o da outra plataforma é `None`.
    """
    if pp.IS_WINDOWS:
        assert msvcrt_attr is not None, "no Windows, `msvcrt` tem de estar carregado"
        assert fcntl_attr is None, "no Windows, `fcntl` não existe e tem de ser None"
    else:
        assert fcntl_attr is not None, "em POSIX, `fcntl` tem de estar carregado"
        assert msvcrt_attr is None, "em POSIX, `msvcrt` não existe e tem de ser None"


def test_module_defines_both_platform_names_regardless_of_current_os() -> None:
    """9: `_msvcrt`/`_fcntl` estão sempre definidos como atributos do
    módulo — `None` na plataforma onde não existem — nunca um
    `AttributeError`/`ImportError` só por importar o módulo.

    O nome deste teste sempre prometeu "regardless of current OS", mas as
    assertions afirmavam o lado Windows (`_msvcrt is not None`) — verdade
    apenas no host de desenvolvimento. Agora o invariante é afirmado nos DOIS
    sentidos, o que é mais forte, não mais frouxo: em cada plataforma, o
    módulo daquela plataforma está carregado E o da outra é explicitamente
    `None` (nunca ausente, nunca um import que explode).
    """
    assert hasattr(pp, "_msvcrt")
    assert hasattr(pp, "_fcntl")
    _assert_only_current_platform_module_is_loaded(pp._msvcrt, pp._fcntl)


def test_module_reimport_is_safe() -> None:
    """9 (complemento): reimportar o módulo (ex.: um segundo processo de
    teste, um reload) nunca dispara `import fcntl`/`import msvcrt`
    incondicional de novo — os dois vivem só dentro do `try/except` do
    topo do arquivo, executado uma única vez na primeira importação.

    O reload é o que prova a segurança: se algum dos imports fosse
    incondicional, ele levantaria `ImportError` aqui, na plataforma onde o
    módulo não existe. Vale nas duas plataformas.
    """
    import importlib

    reloaded = importlib.reload(pp)
    assert isinstance(reloaded, types.ModuleType)
    _assert_only_current_platform_module_is_loaded(reloaded._msvcrt, reloaded._fcntl)
