"""RC.1 — persistência durável do relatório HTML.

Incidente real do smoke R1-01 (docs/rc-discord-delivery-resilience.md): uma
análise terminou com sucesso, o HTML foi renderizado só em memória, o envio ao
Discord falhou com 403/50013 e o produto da análise foi perdido junto com o
processo. `jobs.report_path` existia no schema desde a T1.8 e nunca era
preenchido.

Aqui o relatório vira arquivo antes de qualquer tentativa de entrega, com
escrita atômica (tmp + replace) para que um caminho registrado nunca aponte
para um arquivo escrito pela metade.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from pathlib import Path

REPORTS_DIRNAME = "reports"
INTERACTIVE_PREFIX = "interactive-"

# job_id vem de uuid4().hex, mas nunca confie nisso para montar caminho: um
# valor inesperado não pode escapar do diretório de relatórios.
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ReportPersistenceError(RuntimeError):
    """Falha ao gravar o artefato — erro de produção do artifact, nunca
    confundido com falha de entrega no Discord (RC.11/RC.12).
    """


def reports_dir(data_dir: Path) -> Path:
    return data_dir / REPORTS_DIRNAME


def report_path_for(data_dir: Path, job_id: str) -> Path:
    if not _SAFE_JOB_ID.match(job_id):
        raise ReportPersistenceError(f"job_id invalido para nome de arquivo: {job_id!r}")
    return reports_dir(data_dir) / f"{job_id}.html"


def persist_report(data_dir: Path, job_id: str, html: str) -> Path:
    """Grava o relatório e devolve o caminho. Levanta ReportPersistenceError
    em qualquer falha de filesystem — o chamador decide o que fazer, mas nunca
    deve seguir para a entrega com um artefato inexistente.
    """
    target = report_path_for(data_dir, job_id)
    tmp = target.with_suffix(".html.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(target)
    except OSError as e:
        with_suppressed_cleanup(tmp)
        raise ReportPersistenceError(f"falha ao persistir relatorio de {job_id}: {e}") from e
    return target


def with_suppressed_cleanup(tmp: Path) -> None:
    """Remove o temporário sem mascarar o erro original."""
    with contextlib.suppress(OSError):
        tmp.unlink(missing_ok=True)


def load_report(path: str | Path) -> str:
    """Lê um relatório já persistido (redelivery, RC.10). Zero rede, zero WCL."""
    target = Path(path)
    try:
        return target.read_text(encoding="utf-8")
    except OSError as e:
        raise ReportPersistenceError(f"relatorio nao pode ser lido em {target}: {e}") from e


def interactive_artifact_id(report_code: str, fight_id: int, player_name: str) -> str:
    """A.2 — identidade estavel para o relatorio do caminho interativo, que nao
    tem job_id.

    Deriva de `(report, fight, jogador)` por hash, entao: e deterministica (a
    mesma analise sobrescreve o proprio artefato em vez de acumular lixo), nao
    colide na pratica, e e path-safe **por construcao** — o nome final so tem
    hexadecimais, de modo que nome de jogador com barra, acento ou `..` nunca
    alcanca o filesystem.
    """
    raw = f"{report_code}:{fight_id}:{player_name}".encode()
    return INTERACTIVE_PREFIX + hashlib.sha256(raw).hexdigest()[:24]
