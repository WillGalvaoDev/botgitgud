"""CL.5 — validação conservadora de `report_public_base_url` e construção
da URL pública de uma capability (CL.2/CL.3): `<base>/r/<token>`.

Puro por construção: nenhum I/O, nenhum DNS, nenhuma rede — só
`urllib.parse`. Nunca loga o valor de retorno (a URL carrega a
capability secret embutida); quem chama decide o que logar (nunca a URL
inteira, ver `bot/delivery.py`).

Falha conservadoramente: `javascript:`/`data:`/`file:` e qualquer outro
esquema fora de http(s) são rejeitados, assim como query/fragment/
userinfo/path não-trivial — nenhuma dessas coisas faz sentido numa base
que só serve para prefixar `/r/<token>`, e aceitar uma delas
silenciosamente é exatamente o tipo de erro de configuração que este
módulo existe para pegar cedo (no boot do bot), nunca no meio de uma
entrega real.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED_SCHEMES = ("https", "http")


class ReportPublicBaseUrlError(ValueError):
    """`report_public_base_url` ausente ou inválida — nunca aceita
    silenciosamente, nunca cai para um domínio inventado.
    """


def validate_report_public_base_url(base_url: str | None) -> str:
    """Valida e normaliza `base_url` para `<scheme>://<netloc>`, sempre
    sem trailing slash — para que `build_report_url` nunca precise
    decidir se concatena com ou sem `/` (e nunca produza `//r/`).

    Aceita, por exemplo:
        https://botgitgud.duckdns.org
        https://botgitgud.duckdns.org/     (trailing slash normalizada)
        http://127.0.0.1:12345             (teste local)

    Rejeita: esquema fora de http/https (`javascript:`, `data:`,
    `file:`, ...), ausência de host, userinfo (`user:pass@`), query
    (`?...`), fragment (`#...`), e qualquer path além de vazio/`/`.
    """
    if not base_url:
        raise ReportPublicBaseUrlError(
            "report_public_base_url não configurada — entrega de link exige "
            "configuração explícita (nunca um domínio de produção inventado)"
        )
    parts = urlsplit(base_url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ReportPublicBaseUrlError(
            f"report_public_base_url com esquema não permitido: {parts.scheme!r} "
            f"(aceito apenas: {', '.join(_ALLOWED_SCHEMES)})"
        )
    if not parts.netloc:
        raise ReportPublicBaseUrlError("report_public_base_url sem host")
    if parts.username or parts.password:
        raise ReportPublicBaseUrlError("report_public_base_url não pode conter userinfo")
    if parts.query:
        raise ReportPublicBaseUrlError("report_public_base_url não pode conter query")
    if parts.fragment:
        raise ReportPublicBaseUrlError("report_public_base_url não pode conter fragment")
    if parts.path not in ("", "/"):
        raise ReportPublicBaseUrlError(
            f"report_public_base_url não pode conter path: {parts.path!r}"
        )
    return f"{parts.scheme}://{parts.netloc}"


def build_report_url(base_url: str, token: str) -> str:
    """`<base>/r/<token>` — determinístico, sem query, sem fragment, sem
    barra dupla (`validate_report_public_base_url` já normalizou `base_url`
    sem trailing slash). `token` entra por inteiro e sem modificação: um
    `secrets.token_urlsafe(...)` (bot/report_links.py) já só usa
    caracteres URL-safe, então nenhum encoding é necessário aqui — inserir
    um faria o token servido divergir do token persistido.
    """
    validated = validate_report_public_base_url(base_url)
    return f"{validated}/r/{token}"
