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
import sys
from pathlib import Path

from botgitgud.ops.caddy_config import CaddyDomainError, render_caddyfile
from botgitgud.ops.deploy import (
    BACKUP_EXCLUSION_REASONS,
    BackupError,
    create_backup,
    list_backups,
    restore_backup,
    validate_env_file,
)
from botgitgud.ops.preflight import CheckStatus, exit_code_for, run_preflight

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
        report_server_host=args.report_server_host,
        report_server_port=args.report_server_port,
        require_public_base_url=args.production,
    )
    for result in results:
        sys.stdout.write(f"[{_STATUS_LABEL[result.status]}] {result.name}: {result.detail}\n")
    code = exit_code_for(results)
    mode = "production" if args.production else "host-preparation"
    sys.stdout.write(f"preflight_mode={mode}\n")
    sys.stdout.write("preflight=" + ("failed" if code else "passed") + "\n")
    return code


def _cmd_deploy_validate_env(args: argparse.Namespace) -> int:
    """Separado do preflight porque o `install-systemd.sh` precisa desta
    resposta sozinha para decidir se pode dar `start` — sem exigir que a
    venv, os diretórios e a porta já estejam prontos.
    """
    report = validate_env_file(args.env_file, require_public_base_url=args.production)
    for issue in report.issues:
        sys.stdout.write(f"[{issue.severity.value}] {issue.variable}: {issue.message}\n")
    mode = "production" if args.production else "host-preparation"
    sys.stdout.write(f"env_mode={mode}\n")
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


def _cmd_deploy_render_caddyfile(args: argparse.Namespace) -> int:
    """`deploy/install-caddy.sh` chama isto — o domínio nunca é substituído
    à mão no template versionado. Domínio não é segredo (é um nome DNS
    público por definição), então não há nada a redigir na saída.
    """
    template_text = args.template.read_text(encoding="utf-8")
    try:
        rendered = render_caddyfile(template_text, args.domain)
    except CaddyDomainError as exc:
        sys.stderr.write(f"erro: {exc}\n")
        return 1
    if args.out is None:
        sys.stdout.write(rendered)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"rendered={args.out}\n")
    return 0


def add_deploy_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[name-defined]
) -> None:
    preflight = sub.add_parser(
        "deploy-preflight", help="Verificações locais offline antes de subir o serviço."
    )
    preflight.add_argument("--data-dir", type=Path, default=Path("data"))
    preflight.add_argument("--env-file", type=Path, default=Path(".env"))
    preflight.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    preflight.add_argument("--report-server-host", default="127.0.0.1")
    preflight.add_argument("--report-server-port", type=int, default=8080)
    preflight.add_argument(
        "--production",
        action="store_true",
        help="Gate de START DE PRODUÇÃO: exige REPORT_PUBLIC_BASE_URL válida "
        "(bloqueado até a CL.9 definir o domínio). Sem esta flag, valida apenas "
        "a preparação do host.",
    )
    preflight.set_defaults(func=_cmd_deploy_preflight)

    validate = sub.add_parser(
        "deploy-validate-env", help="Valida o .env de produção sem imprimir nenhum valor."
    )
    validate.add_argument("--env-file", type=Path, default=Path(".env"))
    validate.add_argument(
        "--production",
        action="store_true",
        help="Exige REPORT_PUBLIC_BASE_URL válida (gate de start de produção).",
    )
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

    caddy = sub.add_parser(
        "deploy-render-caddyfile",
        help="Substitui o placeholder de domínio no template do Caddy (CL.9A).",
    )
    caddy.add_argument("--domain", required=True, help="Domínio real — nunca um valor inventado.")
    caddy.add_argument(
        "--template", type=Path, default=REPO_ROOT / "deploy" / "caddy" / "Caddyfile.template"
    )
    caddy.add_argument(
        "--out", type=Path, default=None, help="Arquivo de saída; omitido imprime em stdout."
    )
    caddy.set_defaults(func=_cmd_deploy_render_caddyfile)
