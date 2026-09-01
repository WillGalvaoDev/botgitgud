"""CL.9A — renderização/validação do Caddyfile de produção. Puro, sem I/O,
mesma disciplina de `bot/report_url.py`: nenhuma rede, nenhum DNS, nunca
aceita um domínio inventado.

**Por que o domínio nunca tem default operacional**: `deploy/caddy/
Caddyfile.template` traz `DOMAIN_PLACEHOLDER` no lugar do host. Não existe
um `report_public_base_url`-style fallback aqui — CL.9A só prepara o
template; o domínio real só existe depois de CL.9B (DNS) apontar para o
IP público da VM. `render_caddyfile` recusa até o próprio placeholder
sendo passado como domínio "real" por engano.

**Contrato do template** (verificado por `tests/unit/test_caddy_deploy.py`,
não só por este módulo): reverse_proxy exclusivamente para
`127.0.0.1:8080`, `/healthz` nunca roteado (deny-by-default cobre o
resto), nenhum bloco `log` (access log padrão do Caddy inclui a URI
inteira — o capability token iria para o disco).
"""

from __future__ import annotations

import re

DOMAIN_PLACEHOLDER = "__BOTGITGUD_DOMAIN__"

# RFC 1035-ish, suficiente para recusar lixo óbvio sem reimplementar um
# parser de DNS completo — a autoridade sobre "este domínio realmente
# resolve" é o próprio ACME do Caddy no primeiro request real, não este
# validador offline.
_DOMAIN_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")


class CaddyDomainError(ValueError):
    """Domínio ausente, é o placeholder, ou tem formato claramente inválido."""


def validate_caddy_domain(domain: str | None) -> str:
    """Levanta `CaddyDomainError` cedo — nunca deixa um domínio ruim virar
    um Caddyfile que `caddy validate` só rejeitaria depois, ou pior, que
    passasse por validação sintática e falhasse silenciosamente no ACME.
    """
    if not domain or not domain.strip():
        raise CaddyDomainError(
            "domínio não configurado — CL.9A exige configuração explícita, "
            "nunca um domínio de produção inventado"
        )
    candidate = domain.strip()
    if candidate == DOMAIN_PLACEHOLDER:
        raise CaddyDomainError(
            f"domínio ainda é o placeholder do template ({DOMAIN_PLACEHOLDER!r}) "
            "— substitua pelo domínio real"
        )
    if "://" in candidate:
        raise CaddyDomainError("domínio não deve conter esquema (http://, https://)")
    if any(ch.isspace() for ch in candidate):
        raise CaddyDomainError("domínio não pode conter espaço")
    if "/" in candidate or "?" in candidate or "#" in candidate:
        raise CaddyDomainError("domínio não deve conter path/query/fragment")
    if ":" in candidate:
        raise CaddyDomainError(
            "domínio não deve incluir porta — o Caddy gerencia 80/443 automaticamente"
        )
    if not _DOMAIN_RE.match(candidate):
        raise CaddyDomainError(f"domínio com formato inválido: {candidate!r}")
    return candidate


def caddyfile_contract_violations(text: str) -> tuple[str, ...]:
    """CL.9B — o mesmo contrato que `tests/unit/test_caddy_deploy.py` prova
    estaticamente sobre o TEMPLATE, generalizado para uma função reusável
    que também roda sobre um Caddyfile já RENDERIZADO (domínio real, sem
    `DOMAIN_PLACEHOLDER`) — a activation readiness (`ops/activation.py`)
    chama isto antes de considerar o Caddy pronto para ativar.

    Puro: só string parsing sobre o texto recebido, nenhum I/O, nenhuma
    chamada ao binário `caddy` — isso continua sendo `caddy validate`
    (`deploy/install-caddy.sh`), uma camada complementar que este módulo
    nunca substitui. Devolve uma tupla de violações; vazia significa "sem
    problemas encontrados por esta checagem" (não uma prova formal de que
    o arquivo é válido para o Caddy real).
    """
    violations: list[str] = []
    directives = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if DOMAIN_PLACEHOLDER in text:
        violations.append(
            f"placeholder {DOMAIN_PLACEHOLDER!r} ainda presente — Caddyfile não foi renderizado"
        )

    proxy_lines = [d for d in directives if d.startswith("reverse_proxy")]
    if len(proxy_lines) != 1:
        violations.append(
            f"esperada exatamente 1 diretiva reverse_proxy, encontradas {len(proxy_lines)}"
        )
    elif proxy_lines[0] != "reverse_proxy @report_link 127.0.0.1:8080":
        violations.append(
            f"reverse_proxy não aponta exclusivamente para 127.0.0.1:8080: {proxy_lines[0]!r}"
        )

    for directive in directives:
        if "0.0.0.0" in directive:
            violations.append(f"diretiva expõe 0.0.0.0: {directive!r}")
        if "healthz" in directive.lower():
            violations.append(f"/healthz roteado por uma diretiva: {directive!r}")
        if directive.startswith("log") or "log {" in directive:
            violations.append(f"bloco de access log presente: {directive!r}")
        if directive.startswith("redir"):
            violations.append(f"diretiva de redirect presente: {directive!r}")

    if "respond 404" not in directives:
        violations.append("deny-by-default (respond 404) ausente")

    return tuple(violations)


def render_caddyfile(template: str, domain: str) -> str:
    """Substitui `DOMAIN_PLACEHOLDER` pelo domínio validado.

    Exige EXATAMENTE uma ocorrência do marcador — nem zero (o arquivo foi
    editado e perdeu o ponto de substituição, gerando um Caddyfile com o
    domínio nunca aplicado) nem duas ou mais (`str.replace` substituiria
    todas, inclusive uma citação em comentário/prosa, corrompendo o
    texto — bug real encontrado nesta rodada: o template antigo citava o
    marcador duas vezes, e o domínio vazava para dentro de uma frase
    explicativa depois da renderização).
    """
    validated = validate_caddy_domain(domain)
    occurrences = template.count(DOMAIN_PLACEHOLDER)
    if occurrences == 0:
        raise CaddyDomainError(
            f"template não contém o marcador {DOMAIN_PLACEHOLDER!r} — geração abortada"
        )
    if occurrences > 1:
        raise CaddyDomainError(
            f"template contém {occurrences} ocorrências de {DOMAIN_PLACEHOLDER!r} — "
            "esperada exatamente 1; uma substituição em massa corromperia comentários "
            "que citam o marcador"
        )
    return template.replace(DOMAIN_PLACEHOLDER, validated)
