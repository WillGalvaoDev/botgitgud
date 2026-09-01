"""GH.0 — checagem offline de segurança de publicação.

Roda sobre a lista de arquivos TRACKED (o que de fato seria publicado) e
recusa as categorias que nunca deveriam sair deste repositório: `.env`
real, chaves privadas, o warehouse DuckDB, backups, dados de runtime sob
`data/`, e arquivos grandes o bastante para virar problema no GitHub.

**Isto NÃO substitui secret scanning especializado.** É um guard-rail
contra o erro operacional comum (`git add .` levando junto o que o
`.gitignore` deveria ter coberto), não uma prova de ausência de segredo:
detecção por nome/extensão/marcador de conteúdo não encontra uma
credencial embutida no meio de um arquivo legítimo, e nenhuma heurística
aqui pretende isso. Ferramentas dedicadas (gitleaks, trufflehog, GitHub
secret scanning) continuam sendo a resposta certa para essa pergunta —
`run_publication_safety` só garante que os erros ÓBVIOS parem antes do
push.

Puro por construção: recebe a lista de caminhos e um leitor de arquivo,
nunca chama `git` nem a rede — quem enumera os arquivos é a camada CLI
(`cli_deploy.py`), do mesmo jeito que `ops/activation.py` recebe os
caminhos já resolvidos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from botgitgud.ops.preflight import CheckResult, CheckStatus

# Nomes/padrões que nunca podem estar tracked. Cada entrada é (regex,
# motivo) — o motivo entra no relatório para o operador saber POR QUE
# aquilo parou a publicação.
FORBIDDEN_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(^|/)\.env$", "arquivo .env real (credenciais)"),
    (r"(^|/)\.env\.(?!example$)[^/]+$", "variante de .env real (credenciais)"),
    (r"\.pem$", "certificado/chave PEM"),
    (r"\.key$", "arquivo de chave"),
    (r"(^|/)id_rsa($|\.)", "chave privada SSH"),
    (r"(^|/)id_ed25519($|\.)", "chave privada SSH"),
    (r"(^|/)credentials?(\.|$)", "arquivo de credenciais"),
    (r"(^|/)secrets?\.(json|ya?ml|toml|txt|ini)$", "arquivo de segredos"),
    (r"\.p12$|\.pfx$|\.ppk$|\.keystore$", "keystore/chave"),
    (r"^data/", "estado de runtime sob data/ (warehouse, raw, reports, logs, control)"),
    (r"\.duckdb(\.wal)?$", "banco DuckDB"),
    (r"\.tar\.gz$", "arquivo compactado (backup?)"),
    (r"^backups?/", "diretório de backup"),
    (r"\.jsonl$", "log JSONL de runtime"),
    (r"\.sqlite3?$|\.db$", "banco de dados"),
)

# Marcadores de conteúdo que indicam material de chave privada embutido.
# Deliberadamente restritos a blocos PEM/OpenSSH: são inequívocos, ao
# contrário de heurísticas de entropia, que produziriam falso positivo em
# cima das 632 cassettes de teste deste repositório.
PRIVATE_KEY_MARKERS: tuple[str, ...] = (
    "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "BEGIN DSA PRIVATE KEY",
    "BEGIN EC PRIVATE KEY",
    "BEGIN PGP PRIVATE KEY",
    "BEGIN PRIVATE KEY",
)

# Webhooks carregam a credencial na própria URL.
WEBHOOK_PATTERN = re.compile(r"discord(app)?\.com/api/webhooks/\d+/|hooks\.slack\.com/services/")

# Limites alinhados ao que o GitHub de fato impõe: 100 MB é bloqueio duro
# do lado dele, 50 MB é aviso. Ficamos abaixo dos dois de propósito.
WARN_FILE_BYTES = 5 * 1024 * 1024
FAIL_FILE_BYTES = 50 * 1024 * 1024

# O próprio detector e seus testes contêm os marcadores literais que ele
# procura — sem esta exceção, `publication-check` reprovaria a si mesmo no
# instante em que fosse commitado (verificado antes do commit, não em
# teoria). Mesma razão pela qual um linter não aplica suas próprias regras
# aos fixtures que as definem. A exceção é por CAMINHO EXATO e cobre só
# estes dois arquivos: qualquer outro arquivo com um bloco de chave
# continua sendo reprovado normalmente.
SELF_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "src/botgitgud/ops/publication.py",
        "tests/unit/test_publication_safety.py",
    }
)

_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".toml",
        ".txt",
        ".sh",
        ".ps1",
        ".json",
        ".yml",
        ".yaml",
        ".cfg",
        ".ini",
        ".service",
        ".template",
        ".example",
    }
)


def forbidden_reason(path: str) -> str | None:
    """Motivo pelo qual `path` nunca deveria estar tracked, ou `None`."""
    normalized = path.replace("\\", "/")
    for pattern, reason in FORBIDDEN_PATH_PATTERNS:
        if re.search(pattern, normalized):
            return reason
    return None


def check_forbidden_paths(tracked_paths: Iterable[str]) -> CheckResult:
    offenders = [(p, r) for p in tracked_paths if (r := forbidden_reason(p)) is not None]
    if offenders:
        detail = "; ".join(f"{p} ({r})" for p, r in offenders[:10])
        if len(offenders) > 10:
            detail += f"; ... +{len(offenders) - 10}"
        return CheckResult("forbidden_paths", CheckStatus.FAILED, detail)
    return CheckResult("forbidden_paths", CheckStatus.OK, "nenhum arquivo proibido tracked")


def check_file_sizes(repo_root: Path, tracked_paths: Iterable[str]) -> list[CheckResult]:
    root = Path(repo_root)
    warn: list[str] = []
    fail: list[str] = []
    for rel in tracked_paths:
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= FAIL_FILE_BYTES:
            fail.append(f"{rel} ({size / 1048576:.1f} MB)")
        elif size >= WARN_FILE_BYTES:
            warn.append(f"{rel} ({size / 1048576:.1f} MB)")

    results: list[CheckResult] = []
    if fail:
        results.append(CheckResult("large_files", CheckStatus.FAILED, "; ".join(sorted(fail)[:10])))
    elif warn:
        results.append(
            CheckResult(
                "large_files",
                CheckStatus.WARNING,
                f"{len(warn)} arquivo(s) acima de {WARN_FILE_BYTES // 1048576} MB: "
                + "; ".join(sorted(warn)[:5]),
            )
        )
    else:
        results.append(
            CheckResult(
                "large_files", CheckStatus.OK, f"nenhum arquivo >= {WARN_FILE_BYTES // 1048576} MB"
            )
        )
    return results


def check_secret_markers(repo_root: Path, tracked_paths: Iterable[str]) -> CheckResult:
    """Só marcadores INEQUÍVOCOS (bloco PEM, webhook com id) — ver docstring
    do módulo sobre por que isto não é secret scanning de verdade.
    """
    root = Path(repo_root)
    offenders: list[str] = []
    for rel in tracked_paths:
        if rel.replace("\\", "/") in SELF_EXCLUDED_PATHS:
            continue
        path = root / rel
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(marker in text for marker in PRIVATE_KEY_MARKERS):
            offenders.append(f"{rel} (bloco de chave privada)")
        elif WEBHOOK_PATTERN.search(text):
            offenders.append(f"{rel} (webhook com credencial na URL)")
    if offenders:
        return CheckResult("secret_markers", CheckStatus.FAILED, "; ".join(offenders[:10]))
    return CheckResult(
        "secret_markers",
        CheckStatus.OK,
        "nenhum marcador inequívoco encontrado (NÃO substitui secret scanning dedicado)",
    )


def run_publication_safety(repo_root: Path, tracked_paths: Iterable[str]) -> list[CheckResult]:
    paths = list(tracked_paths)
    results = [check_forbidden_paths(paths)]
    results.extend(check_file_sizes(repo_root, paths))
    results.append(check_secret_markers(repo_root, paths))
    return results
