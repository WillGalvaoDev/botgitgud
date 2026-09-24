"""CL.8 — preparação offline do deploy Linux: layout persistente,
validação de `.env` e backup/restore do estado durável.

**Por que a lógica está aqui e não no shell**: é a mesma decisão já tomada
em B6/CL.6 (`docs/operations.md`) — a política mora em Python,
testável com pytest, e o shell é só a casca que resolve interpretador e
privilégio. Regras de inclusão/exclusão de backup duplicadas entre um
`tar --exclude` e um teste divergem silenciosamente na primeira edição;
aqui existe UMA fonte de verdade, exercitada offline pela suíte.

**Allowlist, nunca denylist**, para o backup: o arquivo carrega só o que
`BACKUP_ENTRIES` nomeia explicitamente. Uma denylist erra por omissão — um
diretório novo sob `data/` entraria no backup sem ninguém decidir isso, e
é assim que `.env`, venv ou credencial vazam para um artefato que depois
circula. Com allowlist, o modo de falha é "faltou algo no backup"
(detectável, reversível), nunca "vazou algo no backup".

**`control/` inteiro fica fora.** O ticket exige excluir
`control/stop.request`, mas `bot.pid` e `supervisor.lock` são igualmente
efêmeros e ativamente perigosos se restaurados: um PID reciclado ou um
lock de um processo que não existe mais confundiria a supervisão no boot
seguinte. Estado de controle é do processo vivo, nunca do arquivo de
backup.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath

# -- layout persistente ------------------------------------------------------

# Diretórios que o bootstrap cria e que o preflight exige existir. Espelham
# exatamente o que o código já resolve em runtime: ingest/store.py (`raw/`),
# logging_setup.py (`logs/`), ops/control.py (`control/`) e
# bot/analysis_runs.py (`ops/analysis-runs`).
PERSISTENT_DIRNAMES: tuple[str, ...] = (
    "raw",
    "logs",
    "control",
    "ops",
    "ops/analysis-runs",
)

# Entradas incluídas no backup, relativas a `data_dir`. Allowlist — ver
# docstring do módulo. `warehouse.duckdb` é arquivo, o resto é diretório.
BACKUP_ENTRIES: tuple[str, ...] = (
    "warehouse.duckdb",
    "raw",
    "reports",
    "ops",
    "logs",
)

# Nomeadas só para o relatório do backup dizer POR QUE algo ficou de fora —
# a exclusão em si é consequência da allowlist acima, não desta lista.
BACKUP_EXCLUSION_REASONS: dict[str, str] = {
    "control": "estado de controle efêmero (stop.request/bot.pid/supervisor.lock)",
    "ops-snapshot.json": "republicado pelo bot em segundos; nunca é fonte de verdade",
    "spells.json": "cache de runtime, resemeado a partir do repositório",
}

ARCHIVE_ROOT = "data"
ARCHIVE_PREFIX = "botgitgud-backup-"
ARCHIVE_SUFFIX = ".tar.gz"


def persistent_dirs(data_dir: Path) -> list[Path]:
    """Caminhos absolutos (na ordem de criação) dos diretórios duráveis."""
    return [Path(data_dir) / name for name in PERSISTENT_DIRNAMES]


# -- validação de .env -------------------------------------------------------


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class EnvIssue:
    """Nunca carrega o VALOR da variável — só o nome e o diagnóstico.

    É o que torna seguro imprimir o resultado do preflight num terminal
    compartilhado, num log ou num relatório de CI: o objeto simplesmente
    não tem onde guardar um segredo.
    """

    severity: Severity
    variable: str
    message: str


@dataclass(frozen=True, slots=True)
class EnvReport:
    issues: tuple[EnvIssue, ...]

    @property
    def errors(self) -> tuple[EnvIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[EnvIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """Warnings do ambiente não reprovam o deploy."""
        return not self.errors


REQUIRED_ENV_VARS: tuple[str, ...] = (
    "DISCORD_TOKEN",
    "WCL_CLIENT_ID",
    "WCL_CLIENT_SECRET",
    "BLIZZARD_CLIENT_ID",
    "BLIZZARD_CLIENT_SECRET",
)

_PLACEHOLDER_MARKERS = (
    "changeme",
    "change-me",
    "your-",
    "your_",
    "replace-me",
    "replaceme",
    "todo",
    "xxxx",
    "<",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def parse_env_file(path: Path) -> dict[str, str]:
    """Parser deliberadamente pequeno: `KEY=VALUE`, `#` comenta, aspas
    externas são removidas.

    Não usa `Settings()` de propósito — validar um arquivo de deploy não
    pode exigir que ele já esteja completo (é justamente o que se quer
    descobrir), e `Settings()` levantaria `ValidationError` antes de
    conseguir relatar QUAIS variáveis faltam.
    """
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
            cleaned = cleaned[1:-1]
        values[key.strip()] = cleaned
    return values


def validate_env_values(values: dict[str, str]) -> EnvReport:
    """Validate production credentials and paths without I/O or networking."""
    issues: list[EnvIssue] = []

    for name in REQUIRED_ENV_VARS:
        value = values.get(name, "").strip()
        if not value:
            issues.append(
                EnvIssue(Severity.ERROR, name, "obrigatória em produção e está ausente/vazia")
            )
        elif _looks_like_placeholder(value):
            issues.append(
                EnvIssue(
                    Severity.ERROR,
                    name,
                    "ainda contém um valor de template — substitua pela credencial real",
                )
            )

    data_dir = values.get("DATA_DIR", "").strip()
    # PurePosixPath, não Path: este valor descreve um caminho no host LINUX de
    # destino, e `Path("/opt/x").is_absolute()` é False no Windows (exige letra
    # de unidade). Usar a semântica da plataforma que roda a validação faria o
    # mesmo .env ser aprovado na VM e reprovado na máquina de desenvolvimento.
    if data_dir and not PurePosixPath(data_dir).is_absolute():
        issues.append(
            EnvIssue(
                Severity.WARNING,
                "DATA_DIR",
                "relativa — resolve contra o cwd do processo; prefira caminho absoluto sob systemd",
            )
        )

    return EnvReport(tuple(issues))


def validate_env_file(path: Path) -> EnvReport:
    if not path.is_file():
        return EnvReport(
            (EnvIssue(Severity.ERROR, str(path), "arquivo .env não existe"),),
        )
    return validate_env_values(parse_env_file(path))


# -- backup / restore --------------------------------------------------------


class BackupError(RuntimeError):
    """Falha ao gerar ou restaurar o arquivo de estado durável."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    archive: Path
    included: tuple[str, ...]
    skipped: tuple[str, ...]


def _archive_name(now: datetime) -> str:
    return f"{ARCHIVE_PREFIX}{now.strftime('%Y%m%d-%H%M%S')}{ARCHIVE_SUFFIX}"


def create_backup(
    data_dir: Path,
    dest_dir: Path,
    *,
    now: datetime | None = None,
) -> BackupResult:
    """Empacota o estado durável em um `.tar.gz` local.

    Sem S3/Object Storage/serviço pago: um arquivo no disco é suficiente
    nesta etapa e é o único destino que não introduz dependência externa
    nem custo. A ordem das entradas é determinística (`BACKUP_ENTRIES` na
    ordem declarada, e `sorted()` dentro de cada diretório) para que dois
    backups do mesmo estado produzam a mesma estrutura — o mtime de cada
    arquivo é preservado de propósito, porque restaurar fidelidade importa
    mais aqui do que um hash idêntico byte a byte.
    """
    source = Path(data_dir)
    if not source.is_dir():
        raise BackupError(f"data_dir não existe: {source}")

    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / _archive_name(now or datetime.now(UTC))
    if archive_path.exists():
        raise BackupError(f"arquivo de backup já existe: {archive_path}")

    included: list[str] = []
    skipped: list[str] = []
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            for entry in BACKUP_ENTRIES:
                path = source / entry
                if not path.exists():
                    skipped.append(entry)
                    continue
                tar.add(
                    path,
                    arcname=f"{ARCHIVE_ROOT}/{entry}",
                    recursive=True,
                    filter=_deterministic_order_hint,
                )
                included.append(entry)
    except OSError as exc:
        archive_path.unlink(missing_ok=True)
        raise BackupError(f"falha ao gravar backup em {archive_path}: {exc}") from exc

    return BackupResult(archive_path, tuple(included), tuple(skipped))


def _deterministic_order_hint(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """`tarfile.add(recursive=True)` já percorre `os.scandir` ordenado por
    `sorted()` internamente desde 3.7; este filtro existe para normalizar
    dono/grupo, que variam entre máquinas e não devem ser restaurados como
    UID numérico de outro host (o restore roda como o usuário do serviço).
    """
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


# Tipos de membro aceitos: arquivo regular e diretório, nada mais. Symlink e
# hardlink podem apontar para fora do destino (ou para /etc/passwd) e são o
# vetor clássico de escape em restore; device/FIFO não têm razão nenhuma de
# existir num backup de dados deste projeto.
_ALLOWED_MEMBER_TYPES = frozenset({tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE})

# Segundo componente permitido em `data/<X>/...` — a MESMA allowlist que o
# backup escreve. Um arquivo legítimo nunca traz outra coisa.
_ALLOWED_TOP_LEVEL = frozenset(BACKUP_ENTRIES)


def member_rejection_reason(name: str, member_type: bytes) -> str | None:
    """Devolve o motivo da recusa, ou `None` se o membro é aceitável.

    Rodado sobre TODOS os membros ANTES de extrair qualquer um — um arquivo
    malicioso é rejeitado inteiro, nunca pela metade.

    Não confia em `filter="data"` como única defesa. O filtro da stdlib é
    bom e continua aplicado (defesa em profundidade), mas "o backup
    normalmente é produzido por este mesmo programa" não é garantia de
    nada: o arquivo é um artefato que circula, pode ser substituído, e é
    exatamente esse o modelo de ameaça de um restore.
    """
    if not name or name in (".", "./"):
        return "nome vazio"

    # Barra invertida nunca aparece num nome de membro tar legítimo (o
    # formato usa `/`), e no Windows ela É separador: `data/..\..\evil`
    # passaria por uma checagem de componentes POSIX e escaparia na
    # extração. Recusa direta.
    if "\\" in name:
        return "contém barra invertida"

    if name.startswith("/"):
        return "caminho absoluto"
    # Caminho absoluto no estilo Windows (`C:\...`, `C:/...`).
    if len(name) >= 2 and name[1] == ":":
        return "caminho absoluto com letra de unidade"

    # PurePosixPath, não Path: nomes de membro tar são sempre POSIX,
    # independente do SO que está restaurando.
    parts = PurePosixPath(name).parts
    if ".." in parts:
        return "contém '..'"
    if not parts or parts[0] != ARCHIVE_ROOT:
        return f"fora do prefixo {ARCHIVE_ROOT!r}"
    if len(parts) > 1 and parts[1] not in _ALLOWED_TOP_LEVEL:
        return f"fora da allowlist de backup: {parts[1]!r}"

    if member_type not in _ALLOWED_MEMBER_TYPES:
        if member_type == tarfile.SYMTYPE:
            return "é um symlink"
        if member_type == tarfile.LNKTYPE:
            return "é um hardlink"
        return "não é arquivo regular nem diretório"

    return None


def _is_safe_member(member: tarfile.TarInfo) -> bool:
    return member_rejection_reason(member.name, member.type) is None


def restore_backup(archive: Path, data_dir: Path, *, overwrite: bool = False) -> tuple[str, ...]:
    """Restaura para `data_dir`, recusando por padrão sobrescrever estado
    existente.

    `overwrite=False` é o default porque o erro operacional caro aqui é
    restaurar por cima de um warehouse vivo — recuperável só com outro
    backup. Recusar exige um gesto extra do operador, e esse gesto é o
    ponto.
    """
    archive_path = Path(archive)
    if not archive_path.is_file():
        raise BackupError(f"arquivo de backup não encontrado: {archive_path}")

    target = Path(data_dir)
    restored: list[str] = []
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            # Validação COMPLETA antes de extrair o primeiro byte: um
            # arquivo com um membro inseguro é recusado inteiro, nunca
            # deixando metade do conteúdo no disco.
            for member in members:
                reason = member_rejection_reason(member.name, member.type)
                if reason is not None:
                    raise BackupError(
                        "membro inseguro no arquivo de backup, restore abortado "
                        f"({reason}): {member.name!r}"
                    )
            if not overwrite:
                for member in members:
                    relative = PurePosixPath(member.name).relative_to(ARCHIVE_ROOT)
                    if not relative.parts:
                        continue
                    existing = target / relative
                    if existing.exists() and existing.is_file():
                        raise BackupError(
                            f"destino já contém {relative} — use overwrite para substituir"
                        )
            target.mkdir(parents=True, exist_ok=True)
            for member in members:
                relative = PurePosixPath(member.name).relative_to(ARCHIVE_ROOT)
                if not relative.parts:
                    continue
                member.name = str(relative)
                # `filter="data"` continua aplicado como segunda camada,
                # sobre membros que já passaram pela validação explícita.
                tar.extract(member, path=target, filter="data")
                restored.append(str(relative))
    except tarfile.TarError as exc:
        raise BackupError(f"arquivo de backup inválido: {exc}") from exc

    return tuple(restored)


def list_backups(dest_dir: Path) -> list[Path]:
    """Mais recente primeiro — o nome carrega o timestamp, então ordenar
    por nome é ordenar por tempo sem depender do mtime do filesystem.
    """
    directory = Path(dest_dir)
    if not directory.is_dir():
        return []
    found = [
        p
        for p in directory.iterdir()
        if p.is_file() and p.name.startswith(ARCHIVE_PREFIX) and p.name.endswith(ARCHIVE_SUFFIX)
    ]
    return sorted(found, key=lambda p: p.name, reverse=True)
