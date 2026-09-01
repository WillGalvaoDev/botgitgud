"""CL.9B — activation readiness: offline gates that a future VM, once
provisioned, is coherently configured to go from "prepared host" (CL.8) +
"Caddy template ready" (CL.9A) to an actual public HTTPS endpoint. See
`docs/cl9b-activation-runbook.md` for the full manual sequence this module
gates against — this file only implements the checks explicitly listed as
safe to run offline there.

**Composition, never duplication.** Every existing validator keeps owning
its own rule:

- `bot/report_url.py::validate_report_public_base_url` (CL.5) — URL shape.
- `ops/deploy.py::validate_env_file`/`run_preflight` (CL.8) — host/env/
  systemd/loopback-bind readiness.
- `ops/caddy_config.py::validate_caddy_domain`/`caddyfile_contract_violations`
  (CL.9A) — domain shape and the Caddyfile's own contract.

This module adds exactly one NEW category of rule that didn't exist
before: **coherence between the Caddy domain and `REPORT_PUBLIC_BASE_URL`**
(they must name the same host), plus two production-activation-specific
tightenings that CL.5's own validator deliberately does NOT enforce (it
also has to accept `http://127.0.0.1:<port>` for local tests): HTTPS is
mandatory, and no custom port is allowed.

**Never mutates anything.** Every function here reads local files (when
given a path) and returns a verdict — no subprocess, no socket connect
beyond what `ops/preflight.py::check_report_server_bind` already does
(bind-and-immediately-close on loopback), no DNS resolution, no HTTP
request, no `systemctl`, no firewall command. `docs/cl9b-activation-runbook.md`
is the place all of those *mutable* steps live, as an operator's manual
sequence — deliberately not automated by this module.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from botgitgud.bot.report_url import ReportPublicBaseUrlError, validate_report_public_base_url
from botgitgud.ops.caddy_config import (
    CaddyDomainError,
    caddyfile_contract_violations,
    validate_caddy_domain,
)
from botgitgud.ops.preflight import CheckResult, CheckStatus, run_preflight

# Mesma lista que `deploy/bootstrap-linux.sh` verifica em shell — duplicada
# de propósito, não por descuido: o script verifica isto ANTES de uma venv
# existir (não pode chamar Python ainda); esta função verifica de novo,
# mais tarde, como parte da ativação, sobre o host já bootstrapped. As duas
# checagens protegem o mesmo invariante em dois estágios diferentes do
# fluxo — nunca a mesma checagem rodando duas vezes por engano.
SOURCE_STAGING_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "src/botgitgud/__init__.py",
    "src/botgitgud/cli.py",
    "deploy/systemd/botgitgud.service",
)


class ActivationConfigError(ValueError):
    """`REPORT_PUBLIC_BASE_URL` incoerente com o domínio do Caddy, ou não
    atende às exigências adicionais de uma ativação de produção (HTTPS
    obrigatório, sem porta customizada). Nunca uma reimplementação das
    regras já cobertas por `validate_report_public_base_url`/
    `validate_caddy_domain` — só a checagem de COERÊNCIA entre os dois e
    as duas restrições extras que só fazem sentido neste contexto (um
    `http://127.0.0.1:PORT` continua válido para `validate_report_public_base_url`
    porque a CL.5 precisa disso para testes locais; uma ativação de
    produção de verdade não).
    """


def check_source_staging(repo_root: Path) -> CheckResult:
    root = Path(repo_root)
    missing = [marker for marker in SOURCE_STAGING_MARKERS if not (root / marker).exists()]
    if missing:
        return CheckResult(
            "source_staging", CheckStatus.FAILED, "marcadores ausentes: " + ", ".join(missing)
        )
    return CheckResult(
        "source_staging",
        CheckStatus.OK,
        f"{len(SOURCE_STAGING_MARKERS)} marcadores presentes em {root}",
    )


def validate_domain_matches_public_base_url(domain: str, base_url: str) -> str:
    """Levanta `ActivationConfigError` se o domínio do Caddy e
    `REPORT_PUBLIC_BASE_URL` não concordam, ou se a URL não atende às
    exigências extras de uma ativação de produção. Devolve a URL
    normalizada (sem trailing slash) quando tudo confere.

    Recusa exatamente o erro operacional que motivou este ticket:

        Caddy:  foo.duckdns.org
        .env:   REPORT_PUBLIC_BASE_URL=https://bar.duckdns.org

    — os dois passam individualmente pelas suas próprias validações
    (ambos são domínios/URLs sintaticamente válidos), mas juntos
    produzem um bot cujos links nunca resolvem para o Caddy real.
    """
    validated_domain = validate_caddy_domain(domain)
    normalized_url = validate_report_public_base_url(base_url)

    parts = urlsplit(normalized_url)
    if parts.scheme != "https":
        raise ActivationConfigError(
            f"REPORT_PUBLIC_BASE_URL usa esquema {parts.scheme!r} — ativação de produção "
            "exige https (http só é aceito pela CL.5 para testes locais em loopback, "
            "nunca para o domínio público real)"
        )
    if ":" in parts.netloc:
        raise ActivationConfigError(
            f"REPORT_PUBLIC_BASE_URL inclui uma porta explícita ({parts.netloc!r}) — "
            "ativação de produção espera HTTPS na porta 443 padrão, gerenciada pelo Caddy"
        )
    if parts.netloc.lower() != validated_domain.lower():
        raise ActivationConfigError(
            f"REPORT_PUBLIC_BASE_URL ({parts.netloc!r}) não corresponde ao domínio "
            f"configurado no Caddy ({validated_domain!r}) — corrija um dos dois antes de "
            "ativar; um link emitido para o domínio errado nunca resolve"
        )
    return normalized_url


def check_domain_matches_public_base_url(domain: str, base_url: str) -> CheckResult:
    try:
        normalized = validate_domain_matches_public_base_url(domain, base_url)
    except (ActivationConfigError, CaddyDomainError, ReportPublicBaseUrlError) as exc:
        return CheckResult("domain_base_url_coherence", CheckStatus.FAILED, str(exc))
    return CheckResult(
        "domain_base_url_coherence",
        CheckStatus.OK,
        f"domínio e URL pública coerentes: {normalized}",
    )


def check_caddyfile_contract(caddyfile_path: Path) -> CheckResult:
    path = Path(caddyfile_path)
    if not path.is_file():
        return CheckResult("caddyfile_contract", CheckStatus.FAILED, f"não encontrado: {path}")
    violations = caddyfile_contract_violations(path.read_text(encoding="utf-8"))
    if violations:
        return CheckResult("caddyfile_contract", CheckStatus.FAILED, "; ".join(violations))
    return CheckResult("caddyfile_contract", CheckStatus.OK, f"{path} atende ao contrato CL.9A")


def run_activation_readiness(
    *,
    data_dir: Path,
    env_path: Path,
    repo_root: Path,
    domain: str,
    caddyfile_path: Path,
    base_url: str,
    report_server_host: str = "127.0.0.1",
    report_server_port: int = 8080,
) -> list[CheckResult]:
    """Agrega TODOS os gates de ativação offline num único relatório.

    Reusa `run_preflight(..., require_public_base_url=True)` (CL.8) —
    arquitetura, Python, venv, imports, diretórios persistentes, escrita,
    unit systemd, `.env` (incluindo `REPORT_SERVER_HOST` em loopback) e
    bind local — e acrescenta só o que é NOVO na CL.9B: staging do
    source, validade do domínio, contrato do Caddyfile renderizado, e
    coerência domínio↔URL pública.

    `base_url` é passado explicitamente (não lido de volta do `.env`)
    para que o mesmo valor já validado por `run_preflight` seja reusado
    aqui sem reabrir/reparsear o arquivo uma segunda vez — o chamador
    (CLI) é quem faz esse parse uma única vez.
    """
    results = list(
        run_preflight(
            data_dir=data_dir,
            env_path=env_path,
            repo_root=repo_root,
            report_server_host=report_server_host,
            report_server_port=report_server_port,
            require_public_base_url=True,
        )
    )
    results.append(check_source_staging(repo_root))

    try:
        validate_caddy_domain(domain)
    except CaddyDomainError as exc:
        results.append(CheckResult("domain_syntax", CheckStatus.FAILED, str(exc)))
    else:
        results.append(CheckResult("domain_syntax", CheckStatus.OK, domain))

    results.append(check_caddyfile_contract(caddyfile_path))
    results.append(check_domain_matches_public_base_url(domain, base_url))
    return results
