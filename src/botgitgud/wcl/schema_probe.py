"""T0.1 — Sondagem do schema WCL v2 contra a API real.

Confirma, por introspecção GraphQL (e, para campos que retornam o escalar
`JSON` e portanto não são introspectáveis, por referência à verificação ao
vivo já registrada em docs/schema_confirmado.md), todo campo que o documento
de implementação assume existir.

Ver docs/architecture.md D-2: este script NUNCA sobrescreve docs/schema_confirmado.md
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

from botgitgud.wcl.schema_probe_fields import FIELD_TABLE, FieldCheck

Verdict = Literal["ok", "missing", "renamed", "json_scalar_live_verified"]

API_URL = "https://www.warcraftlogs.com/api/v2/client"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"


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
        "`docs/schema_confirmado.md` (ver `docs/architecture.md` D-2).",
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
