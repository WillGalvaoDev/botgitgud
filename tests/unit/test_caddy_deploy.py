"""CL.9A — reverse-proxy HTTPS público (Caddy), preparação offline.

Nada aqui instala, roda ou fala com o binário `caddy` real, nem faz
qualquer chamada de rede — `caddy validate`/`apt-get install` só existem
dentro de `deploy/install-caddy.sh`, atrás de flags opt-in
(`--install-package`) nunca acionadas por teste. O que se verifica é: (1)
a lógica pura de `ops/caddy_config.py`, e (2) o contrato estático do
template versionado e do script, do mesmo jeito que
`test_systemd_unit.py`/`test_deploy_scripts.py` já fazem para CL.6/CL.8.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from botgitgud.bot.report_url import validate_report_public_base_url
from botgitgud.ops.caddy_config import (
    DOMAIN_PLACEHOLDER,
    CaddyDomainError,
    render_caddyfile,
    validate_caddy_domain,
)
from botgitgud.ops.deploy import validate_env_values

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
CADDY_DIR = DEPLOY_DIR / "caddy"
TEMPLATE_PATH = CADDY_DIR / "Caddyfile.template"
INSTALL_SCRIPT = DEPLOY_DIR / "install-caddy.sh"
DOC_PATH = REPO_ROOT / "docs" / "linux-deployment.md"

_VALID_ENV = {
    "DISCORD_TOKEN": "test-discord-value",
    "WCL_CLIENT_ID": "test-wcl-id",
    "WCL_CLIENT_SECRET": "test-wcl-secret",
    "BLIZZARD_CLIENT_ID": "test-bnet-id",
    "BLIZZARD_CLIENT_SECRET": "test-bnet-secret",
    "REPORT_SERVER_HOST": "127.0.0.1",
    "REPORT_SERVER_PORT": "8080",
}


def _template_text() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _script_text() -> str:
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def _script_code() -> str:
    """Só as linhas executáveis — mesmo motivo de `test_deploy_scripts.py`:
    o script instrui o operador dentro de mensagens (`die "..."`).
    """
    return "\n".join(
        line
        for line in _script_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _caddyfile_directives() -> list[str]:
    """Linhas de diretiva do template, sem comentários nem linhas vazias —
    para distinguir "o texto explica que /healthz é bloqueado" (comentário,
    ok) de "uma diretiva realmente roteia /healthz" (não deveria existir).
    """
    return [
        line.strip()
        for line in _template_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# -- ops/caddy_config.py: validação de domínio ---------------------------------


def test_empty_domain_is_rejected() -> None:
    with pytest.raises(CaddyDomainError, match="não configurado"):
        validate_caddy_domain("")
    with pytest.raises(CaddyDomainError, match="não configurado"):
        validate_caddy_domain(None)
    with pytest.raises(CaddyDomainError):
        validate_caddy_domain("   ")


def test_placeholder_itself_is_rejected_as_a_domain() -> None:
    """Passar o próprio marcador como "domínio real" por engano não pode
    silenciosamente gerar um Caddyfile que ainda é o template.
    """
    with pytest.raises(CaddyDomainError, match="placeholder"):
        validate_caddy_domain(DOMAIN_PLACEHOLDER)


@pytest.mark.parametrize(
    "bad",
    [
        "https://example.com",
        "http://example.com",
        "example.com/r/token",
        "example.com?x=1",
        "example.com#frag",
        "example.com:8080",
        "exa mple.com",
        "-example.com",
        "example-.com",
        "example",
        "",
    ],
)
def test_malformed_domain_is_rejected(bad: str) -> None:
    with pytest.raises(CaddyDomainError):
        validate_caddy_domain(bad)


@pytest.mark.parametrize(
    "good",
    [
        "example.com",
        "botgitgud.duckdns.org",
        "sub.example.co.uk",
        "a.b",
    ],
)
def test_well_formed_domain_is_accepted(good: str) -> None:
    assert validate_caddy_domain(good) == good


def test_domain_is_stripped_of_surrounding_whitespace() -> None:
    assert validate_caddy_domain("  example.com  ") == "example.com"


# -- render_caddyfile ------------------------------------------------------------


def test_render_requires_a_valid_domain() -> None:
    with pytest.raises(CaddyDomainError):
        render_caddyfile(_template_text(), "")


def test_render_substitutes_the_placeholder() -> None:
    rendered = render_caddyfile(_template_text(), "example.com")
    assert DOMAIN_PLACEHOLDER not in rendered
    assert "example.com {" in rendered


def test_render_never_corrupts_surrounding_prose() -> None:
    """Regressão do bug real encontrado nesta rodada: o template antigo
    citava o marcador duas vezes (uma no bloco do site, outra num
    comentário explicativo) — `str.replace` trocava as duas, e o domínio
    vazava para dentro da frase explicativa depois da renderização.
    """
    rendered = render_caddyfile(_template_text(), "example.com")
    # "example.com" aparece exatamente uma vez: só como cabeçalho do bloco.
    assert rendered.count("example.com") == 1


def test_render_rejects_a_template_with_the_marker_missing() -> None:
    with pytest.raises(CaddyDomainError, match="não contém"):
        render_caddyfile("no marker here {\n}\n", "example.com")


def test_render_rejects_a_template_with_the_marker_duplicated() -> None:
    duplicated = f"{DOMAIN_PLACEHOLDER} {{\n  # {DOMAIN_PLACEHOLDER}\n}}\n"
    with pytest.raises(CaddyDomainError, match="ocorrências"):
        render_caddyfile(duplicated, "example.com")


def test_shipped_template_has_exactly_one_marker_occurrence() -> None:
    """Propriedade estrutural do arquivo versionado — não só do caso de
    teste sintético acima.
    """
    assert _template_text().count(DOMAIN_PLACEHOLDER) == 1


# -- contrato do Caddyfile.template: upstream loopback --------------------------


def test_template_exists() -> None:
    assert TEMPLATE_PATH.is_file()


def test_upstream_is_exactly_loopback_8080() -> None:
    directives = [d for d in _caddyfile_directives() if d.startswith("reverse_proxy")]
    assert len(directives) == 1
    assert directives[0] == "reverse_proxy @report_link 127.0.0.1:8080"


def test_template_never_binds_0_0_0_0() -> None:
    """`0.0.0.0` só pode aparecer em COMENTÁRIO (o template avisa
    explicitamente contra isso) -- nenhuma DIRETIVA real pode citá-lo.
    """
    for directive in _caddyfile_directives():
        assert "0.0.0.0" not in directive, directive


def test_template_preserves_the_capability_link_path() -> None:
    assert "path /r/*" in _template_text()


# -- contrato: /healthz nunca público --------------------------------------------


def test_healthz_is_never_routed_by_a_directive() -> None:
    """`healthz` só pode aparecer em COMENTÁRIO (explicando que está
    bloqueado) — nenhuma diretiva de roteamento real pode citá-lo.
    """
    for directive in _caddyfile_directives():
        assert "healthz" not in directive.lower(), directive


def test_deny_by_default_covers_everything_not_matched() -> None:
    directives = _caddyfile_directives()
    assert "respond 404" in directives
    # A ordem importa: o deny-by-default vem DEPOIS do reverse_proxy da
    # rota permitida, nunca antes (senão bloquearia /r/* também).
    proxy_index = directives.index("reverse_proxy @report_link 127.0.0.1:8080")
    deny_index = directives.index("respond 404")
    assert proxy_index < deny_index


# -- contrato: sem access log, sem redirect, sem query alternativa --------------


def test_template_never_declares_an_access_log_block() -> None:
    """O formato padrão de access log do Caddy inclui a URI inteira — o
    capability token iria para o disco. Nenhuma diretiva `log` pode
    aparecer neste site.
    """
    for directive in _caddyfile_directives():
        assert not directive.startswith("log"), directive
        assert "log {" not in directive


def test_template_never_declares_a_redirect() -> None:
    for directive in _caddyfile_directives():
        assert not directive.startswith("redir"), directive


def test_template_never_rewrites_the_query_string() -> None:
    text = _template_text()
    assert "rewrite" not in text
    assert "uri query" not in text


def test_template_does_not_manipulate_headers() -> None:
    """Nenhum `header_up`/`header_down` — os security headers que o
    ReportServer já produz (X-Content-Type-Options, CSP, Referrer-Policy,
    Cache-Control) devem passar intactos, e nenhum novo é adicionado aqui.
    """
    text = _template_text()
    assert "header_up" not in text
    assert "header_down" not in text


# -- contrato: sem TLS local/self-signed, sem credencial -------------------------


def test_template_never_uses_a_local_or_self_signed_certificate() -> None:
    text = _template_text()
    assert "tls internal" not in text
    assert "tls /" not in text  # caminho de certificado manual


def test_template_contains_no_credential() -> None:
    text = _template_text()
    for marker in (
        "DISCORD_TOKEN",
        "WCL_CLIENT_SECRET",
        "BLIZZARD_CLIENT_SECRET",
        "REPORT_PUBLIC_BASE_URL=",
    ):
        assert marker not in text


def test_template_does_not_hardcode_a_fictional_public_domain() -> None:
    text = _template_text()
    for fake in ("duckdns.org", "example.com", "botgitgud.com", "yourdomain"):
        assert fake not in text.lower()


# -- deploy/install-caddy.sh ------------------------------------------------------


def test_install_script_exists() -> None:
    assert INSTALL_SCRIPT.is_file()


def test_install_script_has_a_shebang_and_fails_fast() -> None:
    text = _script_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text


def test_install_script_requires_root() -> None:
    assert "id -u" in _script_text()


def test_install_script_requires_an_explicit_domain() -> None:
    code = _script_code()
    assert '-z "${DOMAIN}"' in code
    assert "obrigatorio" in _script_text().lower()


def test_install_script_never_hardcodes_a_domain_default() -> None:
    assert 'DOMAIN=""' in _script_code()


def test_install_script_validates_before_installing() -> None:
    code = _script_code()
    render_at = code.find("deploy-render-caddyfile")
    validate_at = code.find("caddy validate")
    install_at = code.find("install -m 0644")
    assert render_at != -1 and validate_at != -1 and install_at != -1
    assert render_at < validate_at < install_at


def test_install_script_never_overwrites_differing_config_without_force() -> None:
    code = _script_code()
    assert "cmp -s" in code
    assert "FORCE" in code


def test_install_script_never_reloads_by_default() -> None:
    code = _script_code()
    assert "DO_RELOAD" in code
    reload_line = next(
        (line for line in code.splitlines() if line.strip().startswith("systemctl reload caddy")),
        None,
    )
    assert reload_line is not None
    # A invocação real fica depois do guard `if [ "${DO_RELOAD}" -eq 0 ]... exit 0`.
    exit_guard_index = code.find('"${DO_RELOAD}" -eq 0')
    reload_index = code.find("systemctl reload caddy")
    assert exit_guard_index != -1
    assert exit_guard_index < reload_index


def test_install_script_never_starts_the_bot_service() -> None:
    code = _script_code()
    assert "systemctl start botgitgud" not in code
    assert "systemctl enable botgitgud" not in code


def test_install_script_never_opens_a_firewall() -> None:
    text = _script_text().lower()
    for forbidden in ("ufw ", "iptables", "firewall-cmd"):
        assert forbidden not in text


def test_install_script_never_touches_ssh() -> None:
    text = _script_text().lower()
    for forbidden in ("sshd_config", "authorized_keys"):
        assert forbidden not in text


def test_install_script_package_install_is_opt_in_and_needs_network_only_there() -> None:
    """`apt-get install`/`curl` só podem aparecer DENTRO do ramo
    `--install-package` — nunca executados incondicionalmente, e nunca por
    teste algum aqui.
    """
    code = _script_code()
    optin_index = code.find('"${INSTALL_PACKAGE}" -eq 1')
    assert optin_index != -1
    before_optin = code[:optin_index]
    assert "apt-get install" not in before_optin
    assert "curl -1sLf" not in before_optin


def test_install_script_never_uses_a_windows_path() -> None:
    text = _script_text()
    assert "\\Scripts\\" not in text
    assert "C:\\" not in text


def test_install_script_has_no_unbounded_destructive_delete() -> None:
    text = _script_text()
    for dangerous in ("rm -rf /", "rm -rf ${REPO_DIR}", 'rm -rf "${REPO_DIR}"'):
        assert dangerous not in text


def test_install_script_contains_no_secret_literal() -> None:
    text = _script_text()
    for marker in ("DISCORD_TOKEN=", "WCL_CLIENT_SECRET=", "BLIZZARD_CLIENT_SECRET="):
        assert marker not in text


def test_install_script_never_binds_0_0_0_0() -> None:
    assert "0.0.0.0" not in _script_text()


# -- regressão: CL.5/CL.8 REPORT_PUBLIC_BASE_URL não foi enfraquecido -----------


def test_cl5_url_validator_is_untouched_by_cl9() -> None:
    assert validate_report_public_base_url("https://example.com") == "https://example.com"
    with pytest.raises(Exception):  # noqa: B017 - mesma exceção da CL.5, sem reimportar
        validate_report_public_base_url("javascript:alert(1)")


def test_cl8_production_gate_still_requires_the_url() -> None:
    report = validate_env_values(dict(_VALID_ENV, REPORT_PUBLIC_BASE_URL=""))
    assert report.ok  # host-preparation: warning, não erro

    strict = validate_env_values(
        dict(_VALID_ENV, REPORT_PUBLIC_BASE_URL=""), require_public_base_url=True
    )
    assert not strict.ok


def test_cl8_production_gate_accepts_a_real_caddy_backed_url() -> None:
    strict = validate_env_values(
        dict(_VALID_ENV, REPORT_PUBLIC_BASE_URL="https://real-domain.example"),
        require_public_base_url=True,
    )
    assert strict.ok


# -- documentação ------------------------------------------------------------


def test_doc_covers_the_cl9a_flow() -> None:
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    for marker in ("caddy", "cl.9a", "offline agora", "depende da vm", "depende de dns"):
        assert marker in text, marker


def test_doc_never_opens_8080_publicly() -> None:
    """ "8080" e "never public" precisam aparecer PERTO um do outro (a linha
    da tabela de firewall) -- "8080" sozinho aparece várias vezes no
    documento (arquitetura, exemplos de comando) sem significar nada sobre
    exposição pública.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert re.search(r"8080[^\n]{0,40}never public", text, re.IGNORECASE)


def test_doc_documents_future_firewall_ports_without_opening_them() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for port in ("22/tcp", "80/tcp", "443/tcp", "8080/tcp"):
        assert port in text
    lowered = text.lower()
    for forbidden in ("ufw allow", "iptables -a", "firewall-cmd --add"):
        assert forbidden not in lowered


def test_doc_does_not_invent_a_public_ip_or_call_duckdns() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "duckdns.org/update" not in text
    assert "api.duckdns.org" not in text


def test_doc_documents_report_public_base_url_integration() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "REPORT_PUBLIC_BASE_URL" in text
