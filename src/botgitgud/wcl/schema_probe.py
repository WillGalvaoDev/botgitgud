"""T0.1 — Sondagem do schema WCL v2 contra a API real.

Confirma, por introspecção GraphQL (e, para campos que retornam o escalar
`JSON` e portanto não são introspectáveis, por referência à verificação ao
vivo já registrada em docs/schema_confirmado.md), todo campo que o documento
de implementação assume existir.

Ver docs/desvios.md D-2: este script NUNCA sobrescreve docs/schema_confirmado.md
(documento curado, referenciado por número de seção a partir de tarefas
posteriores). A saída mecânica deste script vai para docs/schema_probe_output.md.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv

Verdict = Literal["ok", "missing", "renamed", "json_scalar_live_verified"]

API_URL = "https://www.warcraftlogs.com/api/v2/client"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"


@dataclass(frozen=True, slots=True)
class FieldCheck:
    """One row of the T0.1 confirmation table."""

    path: str
    purpose: str
    depends_on: str
    # For introspectable object types: (type_name, [expected_field_or_arg_names]).
    # None when the field returns the JSON scalar and must be verified live instead.
    introspect: tuple[str, list[str]] | None = None
    # For JSON-scalar fields already verified by hand (docs/schema_confirmado.md).
    live_verified_note: str | None = None


FIELD_TABLE: list[FieldCheck] = [
    FieldCheck(
        path="rateLimitData { limitPerHour, pointsSpentThisHour, pointsResetIn }",
        purpose="orçamento de API",
        depends_on="T0.3, T1.8",
        introspect=("RateLimitData", ["limitPerHour", "pointsSpentThisHour", "pointsResetIn"]),
    ),
    FieldCheck(
        path=(
            "reportData.report.fights { id, encounterID, name, startTime, endTime, "
            "kill, difficulty, size, phaseTransitions }"
        ),
        purpose="metadados da luta",
        depends_on="T0.6, T2.4",
        introspect=(
            "ReportFight",
            [
                "id",
                "encounterID",
                "name",
                "startTime",
                "endTime",
                "kill",
                "difficulty",
                "size",
                "phaseTransitions",
            ],
        ),
    ),
    FieldCheck(
        path="reportData.report.table(dataType: Summary) -> playerDetails, combatantInfo",
        purpose="spec, ilvl, talentos",
        depends_on="T2.1",
        live_verified_note=(
            "table() retorna o escalar JSON (não introspectável). Confirmado ao vivo em "
            "schema_confirmado.md §4: playerDetails.{dps,healers,tanks}[].combatantInfo existe; "
            "combatantInfo.talents vem VAZIO, usar combatantInfo.talentTree."
        ),
    ),
    FieldCheck(
        path="reportData.report.table(dataType: DamageDone)",
        purpose="dano por habilidade",
        depends_on="T3.2",
        live_verified_note=(
            "Confirmado ao vivo em schema_confirmado.md §5: entry.total já inclui pets; "
            "entry.abilities vem TRUNCADO (5 de 29 no log de referência) — não usar para "
            "decomposição por habilidade, agregar eventos brutos com masterData.actors.petOwner."
        ),
    ),
    FieldCheck(
        path="reportData.report.table(dataType: Buffs / Debuffs)",
        purpose="uptimes",
        depends_on="T3.1",
        live_verified_note=(
            "Confirmado ao vivo em schema_confirmado.md §6/§11: mesmo formato para Buffs e "
            "Debuffs (auras[]: guid, name, type, abilityIcon, totalUptime, totalUses, bands)."
        ),
    ),
    FieldCheck(
        path="reportData.report.events(dataType: Casts / Resources) { data, nextPageTimestamp }",
        purpose="timeline e recursos",
        depends_on="T0.6, T3.1",
        introspect=("ReportEventPaginator", ["data", "nextPageTimestamp"]),
    ),
    FieldCheck(
        path=(
            "worldData.encounter.characterRankings(className, specName, metric, page, "
            "difficulty, partition, bracket)"
        ),
        purpose="coorte",
        depends_on="T2.1",
        introspect=(
            "__arg__:Encounter.characterRankings",
            ["className", "specName", "metric", "page", "difficulty", "partition", "bracket"],
        ),
    ),
    FieldCheck(
        path=(
            "ranking fields: name, duration, percentile, amount, report{code,fightID,startTime}, "
            "bracketData, talents, gear, server, guild, faction"
        ),
        purpose="matching de coorte",
        depends_on="T2.1",
        live_verified_note=(
            "characterRankings retorna JSON escalar (não introspectável). Confirmado ao vivo em "
            "schema_confirmado.md §8: presentes {amount, bracketData, class, duration, faction, "
            "guild, hardModeLevel, name, report, server, spec, startTime}. AUSENTES: "
            "'percentile' e 'talents'/'gear' NÃO existem — legacy/bot.py:361 fabrica 99.0 sempre; "
            "o parse real vem de characterData.character.encounterRankings.ranks[].rankPercent "
            "(schema_confirmado.md §9)."
        ),
    ),
]


def _get_token() -> str:
    load_dotenv()
    client_id = os.getenv("WCL_CLIENT_ID")
    client_secret = os.getenv("WCL_CLIENT_SECRET")
    if not client_id or not client_secret:
        msg = "WCL_CLIENT_ID / WCL_CLIENT_SECRET ausentes no ambiente (.env)"
        raise RuntimeError(msg)
    res = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def _graphql(token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    res = httpx.post(
        API_URL,
        json={"query": query, "variables": variables or {}},
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=60,
    )
    res.raise_for_status()
    return res.json()


def _introspect_type_fields(token: str, type_name: str) -> set[str] | None:
    """Field names of an OBJECT type via __type introspection. None if type not found."""
    query = "query($t:String!){__type(name:$t){name fields{name}}}"
    data = _graphql(token, query, {"t": type_name}).get("data") or {}
    t = data.get("__type")
    if not t:
        return None
    return {f["name"] for f in (t.get("fields") or [])}


def _introspect_field_args(token: str, parent_type: str, field_name: str) -> set[str] | None:
    """Argument names of a field on `parent_type`. None if the field is not found."""
    query = "query($t:String!){__type(name:$t){fields{name args{name}}}}"
    data = _graphql(token, query, {"t": parent_type}).get("data") or {}
    t = data.get("__type") or {}
    for f in t.get("fields") or []:
        if f["name"] == field_name:
            return {a["name"] for a in (f.get("args") or [])}
    return None


@dataclass(slots=True)
class CheckResult:
    check: FieldCheck
    verdict: Verdict
    detail: str = ""
    missing_items: list[str] = field(default_factory=list)


def probe(token: str | None = None) -> list[CheckResult]:
    """Run every check in FIELD_TABLE against the live API and return verdicts."""
    tok = token or _get_token()
    results: list[CheckResult] = []

    for check in FIELD_TABLE:
        if check.introspect is None:
            results.append(
                CheckResult(
                    check=check,
                    verdict="json_scalar_live_verified",
                    detail=check.live_verified_note or "",
                )
            )
            continue

        type_name, expected = check.introspect
        if type_name.startswith("__arg__:"):
            parent, _, fname = type_name.removeprefix("__arg__:").partition(".")
            found = _introspect_field_args(tok, parent, fname)
            display_name = f"argumentos de {parent}.{fname}"
        else:
            found = _introspect_type_fields(tok, type_name)
            display_name = f"campos de {type_name}"

        if found is None:
            results.append(
                CheckResult(
                    check=check, verdict="missing", detail=f"tipo '{type_name}' não encontrado"
                )
            )
            continue

        missing = [e for e in expected if e not in found]
        if missing:
            results.append(
                CheckResult(
                    check=check,
                    verdict="missing",
                    detail=f"ausentes em {display_name}: {missing}",
                    missing_items=missing,
                )
            )
        else:
            results.append(
                CheckResult(check=check, verdict="ok", detail=f"todos presentes ({display_name})")
            )

    return results


def render_markdown(results: list[CheckResult]) -> str:
    lines = [
        "# Saída mecânica de `schema_probe.py`",
        "",
        "> Gerado automaticamente. Reexecute com "
        "`python -m botgitgud.wcl.schema_probe` para atualizar.",
        "> Este arquivo é sempre seguro de sobrescrever — a fonte de verdade curada é "
        "`docs/schema_confirmado.md` (ver `docs/desvios.md` D-2).",
        "",
        "| Campo | Uso | Depende de | Veredito | Detalhe |",
        "|---|---|---|---|---|",
    ]
    icon = {
        "ok": "✅",
        "missing": "❌",
        "renamed": "⚠️",
        "json_scalar_live_verified": "✅ (verificado ao vivo)",
    }
    for r in results:
        c = r.check
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{c.path}` | {c.purpose} | {c.depends_on} | {icon[r.verdict]} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    results = probe()
    md = render_markdown(results)

    out_path = Path(__file__).resolve().parents[3] / "docs" / "schema_probe_output.md"
    out_path.write_text(md, encoding="utf-8")

    n_missing = sum(1 for r in results if r.verdict == "missing")
    lines: list[str] = []
    for r in results:
        mark = {
            "ok": "OK",
            "missing": "MISSING",
            "renamed": "RENAMED",
            "json_scalar_live_verified": "LIVE",
        }[r.verdict]
        lines.append(f"[{mark:8s}] {r.check.path}")
        if r.verdict == "missing":
            lines.append(f"           -> {r.detail}")

    lines.append(f"\n{len(results)} campos verificados, {n_missing} ausentes.")
    lines.append(f"Relatório escrito em {out_path}")
    # T1.1 (achado 4.9): nenhuma chamada de impressão bruta em src/ — esta é
    # a saída deliberada de um utilitário de CLI (não logging de pipeline),
    # então escreve direto em stdout em vez de rotear por structlog (que
    # produziria linhas estruturadas menos legíveis para este propósito).
    sys.stdout.write("\n".join(lines) + "\n")
    return 1 if n_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
