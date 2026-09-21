"""CL.8 — preflight de deploy: verificações locais, offline, antes de
subir o serviço.

Cada checagem devolve um `CheckResult` em vez de levantar exceção: o valor
operacional está em ver TODAS as falhas de uma vez ("faltam 3 diretórios e
o .env não tem DISCORD_TOKEN"), não em parar na primeira. O exit code do
comando agrega o pior severity encontrado.

**Nada aqui toca a rede.**

**Arquitetura e versão de Python são WARNING, não ERROR**, quando divergem
do alvo: este mesmo preflight roda na máquina de desenvolvimento Windows
(onde a suíte o exercita) e na VM ARM64. Reprovar por `machine() !=
aarch64` tornaria o comando inútil justamente onde ele é testado.
"""

from __future__ import annotations

import importlib
import platform
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from botgitgud.ops.deploy import Severity, persistent_dirs, validate_env_file

# Alvo operacional acordado no CL.6/CL.7: Ubuntu 24.04 ARM64, Python 3.12/3.13.
TARGET_MACHINES = ("aarch64", "arm64")
MINIMUM_PYTHON = (3, 11)
PREFERRED_PYTHON = ((3, 12), (3, 13))

# Módulos que provam que o pacote está instalado e importável na venv.
SMOKE_IMPORTS = (
    "botgitgud",
    "botgitgud.cli",
    "botgitgud.config",
    "botgitgud.ops.supervisor",
)

UNIT_RELATIVE_PATH = Path("deploy") / "systemd" / "botgitgud.service"


class CheckStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


def _ok(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.OK, detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.WARNING, detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.FAILED, detail)


def check_architecture() -> CheckResult:
    machine = platform.machine().lower()
    if machine in TARGET_MACHINES:
        return _ok("architecture", f"{machine} (alvo ARM64)")
    return _warn(
        "architecture", f"{machine} — alvo de produção é ARM64 ({'/'.join(TARGET_MACHINES)})"
    )


def check_python_version() -> CheckResult:
    version = sys.version_info[:2]
    label = f"{version[0]}.{version[1]}"
    if version < MINIMUM_PYTHON:
        minimum = f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}"
        return _fail("python_version", f"{label} abaixo do mínimo suportado ({minimum}+)")
    if version in PREFERRED_PYTHON:
        return _ok("python_version", label)
    preferred = "/".join(f"{major}.{minor}" for major, minor in PREFERRED_PYTHON)
    return _warn("python_version", f"{label} — alvo de produção é {preferred}")


def check_virtualenv() -> CheckResult:
    """`sys.prefix != sys.base_prefix` é o teste canônico de "estou dentro
    de uma venv" (PEP 405), não a existência de um diretório `.venv`: o
    systemd chama o interpretador da venv por caminho absoluto, e é ESSE
    interpretador que precisa estar isolado.
    """
    if sys.prefix != sys.base_prefix:
        return _ok("virtualenv", f"ativa em {sys.prefix}")
    return _fail(
        "virtualenv",
        "interpretador não está em uma venv — o serviço deve usar /opt/botgitgud/.venv/bin/python",
    )


def check_imports() -> CheckResult:
    missing: list[str] = []
    for module in SMOKE_IMPORTS:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    if missing:
        return _fail("imports", "falha ao importar: " + ", ".join(missing))
    return _ok("imports", f"{len(SMOKE_IMPORTS)} módulos importados")


def check_persistent_dirs(data_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    root = Path(data_dir)
    if not root.is_dir():
        return [_fail("data_dir", f"não existe: {root}")]
    results.append(_ok("data_dir", str(root)))

    missing = [str(path) for path in persistent_dirs(root) if not path.is_dir()]
    if missing:
        results.append(_fail("persistent_dirs", "ausentes: " + ", ".join(missing)))
    else:
        results.append(_ok("persistent_dirs", f"{len(persistent_dirs(root))} diretórios presentes"))
    return results


def check_writable(data_dir: Path) -> CheckResult:
    """Permissão real, não `os.access`: só uma escrita de verdade prova que
    o usuário do serviço consegue gravar (ACL, montagem read-only e
    `ProtectSystem=strict` do systemd não aparecem no bit de modo).
    """
    probe = Path(data_dir) / ".preflight-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _fail("writable", f"sem permissão de escrita em {data_dir}: {exc}")
    return _ok("writable", f"escrita confirmada em {data_dir}")


def check_systemd_unit(repo_root: Path) -> CheckResult:
    unit = Path(repo_root) / UNIT_RELATIVE_PATH
    if not unit.is_file():
        return _fail("systemd_unit", f"unit não encontrada em {unit}")
    text = unit.read_text(encoding="utf-8")
    if "botgitgud.cli supervise" not in text:
        return _fail("systemd_unit", "ExecStart não aponta para o supervisor")
    return _ok("systemd_unit", str(unit))


def check_env(env_path: Path) -> list[CheckResult]:
    report = validate_env_file(Path(env_path))
    results: list[CheckResult] = []
    for issue in report.issues:
        detail = f"{issue.variable}: {issue.message}"
        if issue.severity is Severity.ERROR:
            results.append(_fail("env", detail))
        else:
            results.append(_warn("env", detail))
    if report.ok and not report.warnings:
        results.append(_ok("env", f"{env_path} válido"))
    elif report.ok:
        results.append(_ok("env", f"{env_path} sem erros bloqueantes"))
    return results


def run_preflight(
    *,
    data_dir: Path,
    env_path: Path,
    repo_root: Path,
) -> list[CheckResult]:
    results: list[CheckResult] = [
        check_architecture(),
        check_python_version(),
        check_virtualenv(),
        check_imports(),
    ]
    results.extend(check_persistent_dirs(data_dir))
    if Path(data_dir).is_dir():
        results.append(check_writable(data_dir))
    results.append(check_systemd_unit(repo_root))
    results.extend(check_env(env_path))
    return results


def worst_status(results: list[CheckResult]) -> CheckStatus:
    if any(r.status is CheckStatus.FAILED for r in results):
        return CheckStatus.FAILED
    if any(r.status is CheckStatus.WARNING for r in results):
        return CheckStatus.WARNING
    return CheckStatus.OK


def exit_code_for(results: list[CheckResult]) -> int:
    """Only failed checks reject the operation."""
    return 1 if worst_status(results) is CheckStatus.FAILED else 0
