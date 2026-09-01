"""CL.4 — resumo compacto do relatório para o Discord, com teto duro de
`MAX_DISCORD_REPORT_SUMMARY` caracteres.

Produto futuro (CL.5+): `!analisar` -> UMA mensagem Discord compacta +
link para o relatório HTML completo. Este módulo constrói SÓ o texto
dessa mensagem — nunca envia nada, nunca conhece `discord.py`,
`report_links`, HTTP, WCL ou o filesystem. `render_discord_summary`
recebe um `ReportContract` (RP.0, já a fonte semântica única de RESULTADO/
SETUP/EXECUÇÃO/TOP 3/CONFIANÇA) e uma `report_url: str | None` opcional —
puro por construção, sem I/O.

**O HTML continua sendo o artefato canônico completo.** O Discord nunca
tenta caber o relatório inteiro; ele mostra identidade, resultado
principal, confiança da amostra, Top 3 (execution-only, herdado do
contrato — nunca uma segunda regra de causalidade aqui) e resumos curtos
de Setup/Execução, terminando num link para o resto.

**Orçamento por campo, não recorte cego do texto final.** Em vez de
`message[:1800]` (que cortaria markdown, o link, ou um caractere Unicode
no meio), cada campo potencialmente vindo de dado (`char_name`,
`boss_name`, `class_name`, `spec`, cada título do Top 3) tem um teto
FIXO e pequeno, sanitizado e truncado com `clamp_text` ANTES de entrar em
qualquer marcação Markdown — nunca depois. Os tetos foram escolhidos para
que a SOMA do pior caso simultâneo (todo campo no seu máximo) fique bem
abaixo do limite — `test_worst_case_all_fields_long` prova isso
empiricamente, não só por engenharia. O bloco do link é reservado
PRIMEIRO e nunca truncado: se `report_url` sozinho não couber dentro de
`_MAX_URL_LEN`, o renderer cai no mesmo fallback honesto que usa quando
não há link nenhum — nunca gera markdown de link quebrado.

**Sanitização de Markdown/mentions.** Todo campo textual vindo de dado
passa por `_sanitize_field` antes de ser interpolado: escapa a sintaxe
Markdown do Discord (asteriscos, underscore, crase, til, pipe, colchetes,
parênteses) com backslash — preservando o texto literal do usuário em
vez de removê-lo — e quebra o padrão que o
parser do Discord reconhece para menções (`@everyone`, `@here`,
`<@id>`, `<@!id>`, `<@&id>`, `<#id>`) inserindo um zero-width space
logo após o caractere-gatilho, invisível ao olho mas suficiente para que
o Discord nunca reconheça o token como uma menção real. CL.5, quando
integrar a entrega, ainda DEVE passar `allowed_mentions=discord.
AllowedMentions.none()` (ou equivalente) em `channel.send` — a
neutralização textual aqui é defesa em profundidade, não substitui essa
configuração no lado da entrega.
"""

from __future__ import annotations

import re

from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import ObservationCode, Publicability
from botgitgud.report.contract import ReportContract
from botgitgud.report.text import _fmt_dps, _fmt_percentile

MAX_DISCORD_REPORT_SUMMARY = 1800

_ELLIPSIS = "…"
_ZWSP = "\u200b"  # zero-width space, escrito por escape
# (nunca como caractere literal no arquivo-fonte).

# Tetos por campo — pequenos e fixos de propósito (ver docstring do
# módulo: a soma do pior caso simultâneo é verificada em teste). Nunca
# dependem de "quanto sobrou" — cada campo sempre recebe o MESMO teto,
# então o orçamento total é previsível e provável por inspeção, não só
# por execução.
_MAX_NAME_LEN = 40
_MAX_BOSS_LEN = 60
_MAX_CLASS_SPEC_LEN = 24
_MAX_TITLE_LEN = 60
# Um token real (`secrets.token_urlsafe(32)`) tem 43 chars; um domínio
# DuckDNS razoável mais `/r/` fica bem abaixo de 100. 300 é generoso o
# bastante para qualquer host real e ainda uma barreira contra um
# `report_url` absurdo/corrompido — nunca uma URL truncada no meio.
_MAX_URL_LEN = 300

_FALLBACK_LINK_TEXT = "🔗 Relatório completo temporariamente indisponível."

_MENTION_EVERYONE_HERE = re.compile(r"@(everyone|here)")
_MENTION_TAG = re.compile(r"<([@#][!&]?)")
_MARKDOWN_ESCAPE_CHARS = ("\\", "*", "_", "`", "~", "|", "[", "]", "(", ")")


def clamp_text(text: str, max_chars: int) -> str:
    """Trunca por CODE POINT — nunca corta um par substituto, nunca deixa
    uma string mais longa que `max_chars`. `max_chars <= 0` -> string
    vazia; `max_chars == 1` -> só a reticência. Não garante preservar
    clusters de grafema complexos (uma sequência de emoji com ZWJ/
    modificador de tom de pele poderia, em teoria, perder o glifo final
    se o corte cair bem no meio) — o projeto não tem uma dependência de
    segmentação de texto, e os campos que este módulo trunca (nomes,
    títulos) não são esse tipo de conteúdo na prática.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return _ELLIPSIS
    return text[: max_chars - 1] + _ELLIPSIS


def _sanitize_field(text: str) -> str:
    """Neutraliza Markdown do Discord e menções em texto vindo de dado
    (nome de jogador, boss, título de finding) — nunca confiar que esses
    campos são texto plano seguro.

    Ordem importa: menções primeiro (o padrão é `@`/`<` seguido de texto
    específico — inserir o backslash de escape de `[`/etc. ANTES não
    afetaria isso, mas fazer menções depois de escapar `\\` duplicaria
    qualquer barra já inserida). Escapar `\\` é sempre o PRIMEIRO caractere
    da lista de Markdown, para nunca escapar os backslashes que este
    próprio sanitizador acabou de inserir.
    """
    text = _MENTION_EVERYONE_HERE.sub(f"@{_ZWSP}\\1", text)
    text = _MENTION_TAG.sub(f"<{_ZWSP}\\1", text)
    for ch in _MARKDOWN_ESCAPE_CHARS:
        text = text.replace(ch, "\\" + ch)
    return text


def _safe_field(text: str, max_chars: int) -> str:
    """Sanitiza e SÓ DEPOIS trunca — nunca a ordem inversa: truncar antes
    de sanitizar poderia deixar a string sanitizada (com os backslashes/
    zero-width spaces que a sanitização acrescenta) mais longa que
    `max_chars`, furando o orçamento do campo por uma margem pequena mas
    real.
    """
    return clamp_text(_sanitize_field(text), max_chars)


def _looks_like_a_safe_url(url: str) -> bool:
    """Validação leve, não um parser de URL completo: só garante que
    `report_url` não pode quebrar a sintaxe `[texto](url)` do Markdown
    (espaço, `)`, `<`, `>`, crase ou quebra de linha fariam isso) e que
    tem cara de HTTP(S). CL.4 nunca conhece o host real — isso é só uma
    barreira contra um valor corrompido/hostil, nunca uma alegação de que
    a URL é alcançável.
    """
    if not url.startswith(("http://", "https://")):
        return False
    return not any(c in url for c in " \t\n\r)<>`")


def _link_block(report_url: str | None) -> str:
    if report_url is None:
        return _FALLBACK_LINK_TEXT
    if len(report_url) > _MAX_URL_LEN or not _looks_like_a_safe_url(report_url):
        # A URL sozinha não cabe (ou não tem forma segura) — cai no MESMO
        # fallback honesto de "sem link", nunca um Markdown de link
        # quebrado, e nunca um link truncado no meio.
        return _FALLBACK_LINK_TEXT
    return f"🔗 [Ver relatório completo]({report_url})"


def _confidence_phrase(contract: ReportContract) -> str:
    if contract.confianca.cohort_warnings:
        return "confiança baixa (amostra pequena)"
    return "confiança normal"


def _cohort_line(contract: ReportContract) -> str:
    confianca = contract.confianca
    n = confianca.matched_cohort_members
    if n is None:
        n = confianca.reference_pool_members
    cohort_text = f"{n} logs" if n is not None else "amostra indisponível"
    return f"**Coorte** {cohort_text} · {_confidence_phrase(contract)}"


def _setup_summary(setup: SetupAnalysis | None) -> str:
    """Nunca `estimated_gain_pct`, nunca linguagem causal, nunca um score
    global — só a contagem de observações publicáveis (a mesma regra de
    `Publicability.HIDDEN` que `report/setup_text.py`/`report/
    html_report.py` já usam) e, quando aplicável, quantas delas divergem
    do padrão comum. `SetupFinding` não tem — estruturalmente não PODE
    ter — um campo de ganho estimado (ver `analysis/setup_finding.py`);
    nada aqui reintroduz isso por fora.
    """
    if setup is None:
        return "sem dados"
    visible = [f for f in setup.findings if f.publicability is not Publicability.HIDDEN]
    if not visible:
        return "sem dados suficientes nesta amostra"
    diverging = sum(
        1
        for f in visible
        if f.observation
        in (ObservationCode.DIFFERS_FROM_COMMON_PATTERN, ObservationCode.LOW_PREVALENCE)
    )
    noun = "observação" if len(visible) == 1 else "observações"
    if diverging:
        return f"{len(visible)} {noun} ({diverging} divergem do padrão comum)"
    return f"{len(visible)} {noun}"


def _execution_summary(contract: ReportContract) -> str:
    """ "Achado" de Execução = uma comparação por habilidade
    (`ExecutionSection.comparisons`, um `SpellComparison` por spell
    elegível) — a mesma unidade que o relatório completo já detalha seção
    por seção; nunca repete os Top 3 (que já têm sua própria linha).
    """
    n = len(contract.execucao.comparisons)
    noun = "achado" if n == 1 else "achados"
    return f"{n} {noun}"


def _top_actions_block(contract: ReportContract) -> list[str]:
    if not contract.top_actions:
        return ["🎯 **Top 3**", "Nenhum achado com ganho quantificável nesta análise."]
    lines = ["🎯 **Top 3**"]
    for i, finding in enumerate(contract.top_actions[:3], start=1):
        title = _safe_field(finding.title, _MAX_TITLE_LEN)
        gain = (
            f" (+{finding.estimated_gain_pct:.1f}pp)"
            if finding.estimated_gain_pct is not None and finding.estimated_gain_pct >= 0
            else (
                f" ({finding.estimated_gain_pct:.1f}pp)"
                if finding.estimated_gain_pct is not None
                else ""
            )
        )
        lines.append(f"{i}. **{title}**{gain}")
    return lines


def render_discord_summary(contract: ReportContract, *, report_url: str | None = None) -> str:
    """Constrói a mensagem compacta inteira. Determinístico: a MESMA
    entrada sempre produz a MESMA saída (nenhum relógio, nenhum I/O,
    nenhuma aleatoriedade). Garantia dura, provada por teste de
    propriedade: `len(resultado) <= MAX_DISCORD_REPORT_SUMMARY` para
    QUALQUER `ReportContract`/`report_url`.
    """
    header = contract.resultado
    name = _safe_field(header.char_name, _MAX_NAME_LEN)
    boss = _safe_field(header.boss_name, _MAX_BOSS_LEN)
    class_name = _safe_field(header.class_name, _MAX_CLASS_SPEC_LEN)
    spec = _safe_field(header.spec, _MAX_CLASS_SPEC_LEN)

    lines = [
        f"**{name}** — {spec} {class_name} · {boss}",
        f"**DPS** {_fmt_dps(header.player_dps)} · **percentil** "
        f"{_fmt_percentile(header.player_percentile)}",
        _cohort_line(contract),
        "",
        *_top_actions_block(contract),
        "",
        f"📋 Setup: {_setup_summary(contract.setup)}",
        f"⚔️ Execução: {_execution_summary(contract)}",
        "",
        _link_block(report_url),
    ]
    message = "\n".join(lines)

    # Última defesa (item 8 da ordem de prioridade): os tetos por campo já
    # deveriam garantir isto por construção — este clamp final nunca
    # deveria disparar na prática (verificado por teste de propriedade),
    # mas existe como cinto-e-suspensório que NUNCA corta o bloco do link
    # (sempre o último, sempre preservado por inteiro).
    if len(message) > MAX_DISCORD_REPORT_SUMMARY:
        link = _link_block(report_url)
        body = message[: -(len(link) + 1)] if message.endswith(link) else message
        budget_for_body = MAX_DISCORD_REPORT_SUMMARY - len(link) - 1
        message = clamp_text(body, max(budget_for_body, 0)) + "\n" + link

    return message
