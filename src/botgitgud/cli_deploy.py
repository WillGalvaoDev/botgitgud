"""CL.8 — subcomandos de deploy: preflight, backup e restore.

Existem como CLI (e não só como funções) porque é o shell do deploy que
precisa chamá-los: `deploy/preflight.sh` e `deploy/backup.sh` são cascas
finas sobre estes comandos, exatamente como `scripts/bot-supervisor.ps1` é
uma casca fina sobre `cli supervise`. Assim a lógica testada em pytest é a
MESMA que roda na VM — não uma reimplementação em bash que ninguém
exercita.

Nenhum comando aqui fala com Discord, WCL, Blizzard ou qualquer rede.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from botgitgud.ops.deploy import (
    BACKUP_EXCLUSION_REASONS,
    BackupError,
    create_backup,
    list_backups,
    restore_backup,
    validate_env_file,
)
from botgitgud.ops.preflight import CheckStatus, exit_code_for, run_preflight
from botgitgud.ops.publication import run_publication_safety

REPO_ROOT = Path(__file__).resolve().parents[2]

_STATUS_LABEL = {
    CheckStatus.OK: "ok  ",
    CheckStatus.WARNING: "warn",
    CheckStatus.FAILED: "FAIL",
}


def _cmd_deploy_preflight(args: argparse.Namespace) -> int:
    results = run_preflight(
        data_dir=args.data_dir,
        env_path=args.env_file,
        repo_root=args.repo_root,
    )
    for result in results:
        sys.stdout.write(f"[{_STATUS_LABEL[result.status]}] {result.name}: {result.detail}\n")
    code = exit_code_for(results)
    sys.stdout.write("preflight=" + ("failed" if code else "passed") + "\n")
    return code


def _cmd_deploy_validate_env(args: argparse.Namespace) -> int:
    """Separado do preflight porque o `install-systemd.sh` precisa desta
    resposta sozinha para decidir se pode dar `start` — sem exigir que a
    venv, os diretórios e a porta já estejam prontos.
    """
    report = validate_env_file(args.env_file)
    for issue in report.issues:
        sys.stdout.write(f"[{issue.severity.value}] {issue.variable}: {issue.message}\n")
    sys.stdout.write("env=" + ("ok" if report.ok else "invalid") + "\n")
    return 0 if report.ok else 1


def _cmd_deploy_backup(args: argparse.Namespace) -> int:
    try:
        result = create_backup(args.data_dir, args.dest)
    except BackupError as exc:
        sys.stderr.write(f"erro: {exc}\n")
        return 1
    sys.stdout.write(f"archive={result.archive}\n")
    sys.stdout.write(f"archive_bytes={result.archive.stat().st_size}\n")
    for entry in result.included:
        sys.stdout.write(f"included={entry}\n")
    for entry in result.skipped:
        sys.stdout.write(f"skipped={entry} reason=nao_existe\n")
    for entry, reason in sorted(BACKUP_EXCLUSION_REASONS.items()):
        sys.stdout.write(f"excluded={entry} reason={reason}\n")
    return 0


def _cmd_deploy_restore(args: argparse.Namespace) -> int:
    try:
        restored = restore_backup(args.archive, args.data_dir, overwrite=args.overwrite)
    except BackupError as exc:
        sys.stderr.write(f"erro: {exc}\n")
        return 1
    sys.stdout.write(f"restored_entries={len(restored)}\n")
    sys.stdout.write(f"data_dir={args.data_dir}\n")
    return 0


def _cmd_deploy_list_backups(args: argparse.Namespace) -> int:
    for path in list_backups(args.dest):
        sys.stdout.write(f"backup={path} bytes={path.stat().st_size}\n")
    return 0


def _cmd_publication_check(args: argparse.Namespace) -> int:
    """GH.0 — recusa publicar com `.env`, chave privada, warehouse, backup
    ou dado de runtime tracked. NÃO substitui secret scanning dedicado (ver
    `ops/publication.py`).

    A enumeração dos arquivos é feita aqui, com `git ls-files`, para que a
    política em `ops/publication.py` continue pura e testável sem um
    repositório git de verdade.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=args.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(f"erro: não foi possível listar arquivos tracked: {exc}\n")
        return 1

    tracked = [line for line in completed.stdout.splitlines() if line.strip()]
    results = run_publication_safety(args.repo_root, tracked)
    for result in results:
        sys.stdout.write(f"[{_STATUS_LABEL[result.status]}] {result.name}: {result.detail}\n")
    code = exit_code_for(results)
    sys.stdout.write(f"tracked_files={len(tracked)}\n")
    sys.stdout.write("publication_check=" + ("failed" if code else "passed") + "\n")
    sys.stdout.write("nota=nao substitui secret scanning dedicado (gitleaks/trufflehog)\n")
    return code


def add_deploy_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[name-defined]
) -> None:
    preflight = sub.add_parser(
        "deploy-preflight", help="Verificações locais offline antes de subir o serviço."
    )
    preflight.add_argument("--data-dir", type=Path, default=Path("data"))
    preflight.add_argument("--env-file", type=Path, default=Path(".env"))
    preflight.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    preflight.set_defaults(func=_cmd_deploy_preflight)

    validate = sub.add_parser(
        "deploy-validate-env", help="Valida o .env de produção sem imprimir nenhum valor."
    )
    validate.add_argument("--env-file", type=Path, default=Path(".env"))
    validate.set_defaults(func=_cmd_deploy_validate_env)

    backup = sub.add_parser("deploy-backup", help="Empacota o estado durável em um .tar.gz local.")
    backup.add_argument("--data-dir", type=Path, default=Path("data"))
    backup.add_argument("--dest", type=Path, default=Path("backups"))
    backup.set_defaults(func=_cmd_deploy_backup)

    restore = sub.add_parser("deploy-restore", help="Restaura um backup para o data_dir.")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--data-dir", type=Path, default=Path("data"))
    restore.add_argument(
        "--overwrite",
        action="store_true",
        help="Substitui arquivos existentes (por padrão o restore recusa).",
    )
    restore.set_defaults(func=_cmd_deploy_restore)

    listing = sub.add_parser("deploy-list-backups", help="Lista backups, mais recente primeiro.")
    listing.add_argument("--dest", type=Path, default=Path("backups"))
    listing.set_defaults(func=_cmd_deploy_list_backups)

    publication = sub.add_parser(
        "publication-check",
        help="Recusa publicar com .env/chave/warehouse/backup/dado de runtime tracked (GH.0).",
    )
    publication.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    publication.set_defaults(func=_cmd_publication_check)
