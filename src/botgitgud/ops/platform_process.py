"""CL.1 — os dois primitivos genuinamente platform-specific da supervisão
(PID liveness, lock de instância única), extraídos de `ops/supervisor.py`
para que ESSE módulo continue importável em qualquer SO. A política em
volta — single child, backoff exponencial, storm breaker, `stop.request`,
logs estruturados — não muda uma linha: CL.1 é portabilidade, não
redesign (ver `ops/supervisor.py`'s docstring de módulo).

**Segurança de import nos dois sentidos**: nem `import msvcrt` nem
`import fcntl` aparecem incondicionais no topo do módulo — cada um só é
tentado dentro de um `try/except ImportError`, então este módulo carrega
em Windows E em Linux. Os nomes (`_msvcrt`/`_fcntl`) ficam SEMPRE
definidos (`None` quando indisponível na plataforma atual), exatamente
para que os testes possam trocá-los por um duplo via `monkeypatch` e
exercitar o caminho Linux inteiro rodando no Windows — nunca precisando
de um Linux real para provar a lógica.

**Por que uma classe de lock por plataforma, não só um `if`**: o lock
Windows/Linux é injetável (`SupervisorLock(_backend=...)`), não só
despachado internamente por `os.name` — assim um teste no Windows pode
instanciar `_PosixLockBackend()` diretamente, com `_fcntl` trocado por um
duplo, e provar o comportamento Linux (`fcntl.flock(LOCK_EX|LOCK_NB)`)
sem depender de qual SO está rodando a suíte. O PID liveness não precisa
do mesmo truque: `os.kill` já existe (como atributo real, ainda que com
semântica diferente) em toda plataforma que o CPython suporta, então
`pid_is_alive_posix`/`pid_is_alive_windows` já são funções autônomas,
diretamente chamáveis e mockáveis (via `monkeypatch.setattr(os, "kill",
...)`) sem nenhuma indireção extra.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

from botgitgud.ops.control import lock_path_for

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - só acontece fora do Windows
    _msvcrt = None  # type: ignore[assignment]

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - só acontece no Windows
    _fcntl = None  # type: ignore[assignment]

IS_WINDOWS = os.name == "nt"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


# -- PID liveness --------------------------------------------------------------------


def pid_is_alive_windows(pid: int) -> bool:
    """`os.kill(pid, 0)` NÃO detecta morte no Windows (verificado
    empiricamente) — só funciona como sonda de liveness no POSIX.
    `OpenProcess` + `GetExitCodeProcess` é a checagem real.
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


def pid_is_alive_posix(pid: int) -> bool:
    """`os.kill(pid, 0)` não envia sinal nenhum — só pergunta ao kernel se
    o PID existe e é visível a este processo.

    `ProcessLookupError`: o PID não existe -> `False`.
    `PermissionError`: o PID existe, mas pertence a outro usuário (ou
    outro contexto de permissão) -> ainda `True` — o processo EXISTE,
    só não podemos inspecioná-lo, e é exatamente essa a pergunta que
    `pid_is_alive` responde (existe/não existe, nunca "temos acesso a
    ele").
    `pid <= 0` teria semântica de process GROUP no POSIX (0 = meu
    próprio grupo, negativo = grupo específico) — nunca "esse PID
    específico existe", que é a única pergunta que este código faz.
    Tratado como "não existe" sem sequer chamar `kill`, para nunca
    mandar um sinal acidental a um grupo inteiro de processos.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_is_alive(pid: int) -> bool:
    """Despacha para a implementação real da plataforma ATUAL — o mesmo
    contrato que `ops/supervisor.py` sempre expôs sob este nome.
    """
    return pid_is_alive_windows(pid) if IS_WINDOWS else pid_is_alive_posix(pid)


# -- singleton lock --------------------------------------------------------------------


class _LockBackend(Protocol):
    """A fatia mínima que `SupervisorLock` precisa — pequena o bastante
    para um teste trocar por um duplo sem reimplementar nada além disto.
    """

    def lock(self, handle: IO[bytes]) -> None: ...
    def unlock(self, handle: IO[bytes]) -> None: ...


class _WindowsLockBackend:
    """Verificado empiricamente (docs/v1-process-supervision.md): uma
    segunda `locking()` no mesmo byte range — mesmo reaberto pelo MESMO
    processo — levanta `OSError`, exatamente o sinal de "alguém já
    segura isto" que `SupervisorLock.acquire` precisa.
    """

    # Os `type: ignore[attr-defined]` abaixo são o espelho exato dos que
    # `_PosixLockBackend` já carrega: o typeshed condiciona os atributos de
    # `msvcrt` a `sys.platform == "win32"`, então type-checar em Linux (o CI)
    # não os enxerga — do mesmo jeito que type-checar em Windows não enxerga
    # os de `fcntl`. Sem isto o módulo só passa no pyright de UMA plataforma,
    # e este arquivo existe justamente para ser correto nas duas.
    def lock(self, handle: IO[bytes]) -> None:
        assert _msvcrt is not None  # nunca None quando este backend é usado de verdade
        _msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(),
            _msvcrt.LK_NBLCK,  # type: ignore[attr-defined]
            1,
        )

    def unlock(self, handle: IO[bytes]) -> None:
        assert _msvcrt is not None
        _msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(),
            _msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
            1,
        )


class _PosixLockBackend:
    """`fcntl.flock(LOCK_EX | LOCK_NB)` — o equivalente POSIX do mesmo
    invariante: exclusivo, não-bloqueante (falha imediatamente em vez de
    esperar), e — como `msvcrt.locking` — liberado automaticamente pelo
    kernel quando o processo morre (incluindo `SIGKILL`/queda de energia),
    então um lock nunca fica "preso" por um processo que já não existe
    mais, em nenhuma das duas plataformas.
    """

    def lock(self, handle: IO[bytes]) -> None:
        assert _fcntl is not None
        _fcntl.flock(  # type: ignore[attr-defined]
            handle.fileno(),
            _fcntl.LOCK_EX | _fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )

    def unlock(self, handle: IO[bytes]) -> None:
        assert _fcntl is not None
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _default_lock_backend() -> _LockBackend:
    return _WindowsLockBackend() if IS_WINDOWS else _PosixLockBackend()


@dataclass
class SupervisorLock:
    """OS-level lock sobre `control/supervisor.lock` — o mesmo arquivo,
    a mesma semântica ("no máximo um supervisor ativo"), em qualquer SO.

    NUNCA vira "só existência de arquivo": um `supervisor.lock` deixado
    por um processo morto (queda de energia, kill -9, crash) não impede
    um novo `acquire()`, porque o lock real é mantido pelo KERNEL e
    liberado junto com o processo — o arquivo por si só não significa
    nada, só o lock que (talvez) esteja sobre ele.
    """

    _backend: _LockBackend = field(default_factory=_default_lock_backend)
    _handle: IO[bytes] | None = None

    def acquire(self, data_dir: Path) -> bool:
        path = lock_path_for(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        handle = path.open("r+b")
        try:
            self._backend.lock(handle)
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
            self._backend.unlock(handle)
        handle.close()
        self._handle = None
