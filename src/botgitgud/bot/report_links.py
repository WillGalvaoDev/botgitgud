"""CL.2 — camada persistente de capability links para relatórios HTML.

A auditoria pré-CL.2 provou um defeito real: `report_store.py`'s
`interactive_artifact_id` é `sha256(report_code:fight_id:player_name)[:24]`
— um hash de três valores PÚBLICOS. Quem vê o link do WCL e o nome do
jogador no canal computa o `artifact_id` em milissegundos. Se uma URL
futura fosse `/r/<artifact_id>`, os relatórios ficariam enumeráveis por
quem sabe o que foi analisado. Este módulo separa as duas identidades:

    ARTIFACT ID   — identidade interna do arquivo (report_store.py já
                    garante que é path-safe; nunca o segredo).
    CAPABILITY TOKEN — `secrets.token_urlsafe(32)` (256 bits), independente
                    de qualquer informação pública, é a ÚNICA credencial:
                    quem possui o link abre o relatório. Sem autenticação
                    de usuário em v1 — é uma capability, não uma ACL.

CL.2 implementa SOMENTE token + persistência + resolução + expiração.
Nenhum HTTP, nenhum hostname, nenhum `/r/` — isso é CL.3+. `resolve()`
devolve um status estruturado (`FOUND`/`NOT_FOUND`/`EXPIRED`) para que uma
camada HTTP futura possa mapear 1:1 para 404/410 sem reinventar a
distinção aqui.

Segue o MESMO padrão de extensão que `bot/jobs.py` (`JobQueue`) e
`analysis/benchmark_build_progress.py` (`BenchmarkBuildProgressStore`) já
estabelecem: uma classe fina em cima de `Store` (`ingest/store.py`), DDL
próprio criado no `__init__` via `CREATE TABLE IF NOT EXISTS` + um hook
`MIGRATE_*` vazio para colunas futuras — nunca um framework de migração
novo, nunca uma segunda conexão DuckDB. Toda escrita/leitura passa pelo
lock de `Store` (um por processo); `_lock` desta classe (um `threading.
Lock` próprio, como `JobQueue` já tem o seu) protege a ÚNICA decisão de
múltiplas instruções deste módulo — "reusar um token válido ou emitir um
novo" — contra duas emissões concorrentes do MESMO artifact produzirem
duas capabilities diferentes; `Store`'s lock sozinho só serializa
instruções individuais, não essa decisão.

Convenção temporal: UTC-mas-naive (`now_utc_naive`, `bot/job_models.py`),
a MESMA de toda tabela do warehouse — `TIMESTAMP`, nunca `TIMESTAMPTZ`
(seria a primeira tabela do projeto a divergir, e exigiria `pytz`, fora
da lista de dependências).
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

import duckdb
import structlog

from botgitgud.bot.job_models import now_utc_naive
from botgitgud.bot.report_store import ReportPersistenceError, report_path_for
from botgitgud.ingest.store import Store

log = structlog.get_logger(__name__)

CREATE_REPORT_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS report_links (
    token VARCHAR PRIMARY KEY,
    artifact_id VARCHAR,
    created_at TIMESTAMP,
    expires_at TIMESTAMP
)
"""
# Nenhuma migração ainda — mesmo hook point vazio que EB.3/EB.4/EB.5 já
# usam (MIGRATE_ENCOUNTER_BENCHMARKS_TABLE, MIGRATE_BENCHMARK_BUILD_
# PROGRESS_TABLE), para uma coluna futura nunca precisar de um segundo
# mecanismo. `CREATE TABLE IF NOT EXISTS` sozinho já é additive/backward-
# safe: um warehouse existente sem `report_links` ganha a tabela vazia,
# nunca perde nada do que já tinha.
MIGRATE_REPORT_LINKS_TABLE: tuple[str, ...] = ()

# 30 dias — único lugar que define o TTL padrão. Nunca espalhar
# `timedelta(days=30)` pelo código; um chamador que precisar de outro
# valor passa `ttl_days=` explicitamente.
DEFAULT_REPORT_LINK_TTL_DAYS: float = 30.0

# Colisão de secrets.token_urlsafe(32) é astronomicamente improvável (256
# bits de entropia) — este teto nunca deveria ser alcançado na prática.
# Existe como defesa em profundidade, não como uma política de retry
# séria: um pequeno número finito, nunca um loop sem fim, e um erro
# EXPLÍCITO (nunca um token duplicado/corrompido) se ele algum dia for
# esgotado.
_MAX_TOKEN_GENERATION_ATTEMPTS = 5


def _default_token_factory() -> str:
    return secrets.token_urlsafe(32)


class ReportLinkError(RuntimeError):
    """Base de todo erro deste módulo. NUNCA inclui o token completo na
    mensagem — só `artifact_id`/um fingerprint truncado, quando fizer
    sentido (ver `_fingerprint`).
    """


class ReportArtifactNotFoundError(ReportPersistenceError):
    """`artifact_id` tem formato válido, mas o arquivo `.html`
    correspondente não existe em disco — distinto de um `artifact_id`
    mal formado (que `report_path_for` já rejeita com
    `ReportPersistenceError`), para que os dois modos de falha sejam
    testáveis e tratáveis separadamente.
    """


class ReportLinkTokenExhaustedError(ReportLinkError):
    """`_MAX_TOKEN_GENERATION_ATTEMPTS` colisões consecutivas — nunca
    corrompe silenciosamente, nunca reusa um token de outro artifact.
    """


class ReportLinkStatus(StrEnum):
    """Resolução nunca colapsa "nunca existiu" e "existiu e expirou" no
    mesmo resultado — uma camada HTTP futura (CL.3+) mapeia isto 1:1 para
    404 (NOT_FOUND) / 410 (EXPIRED), decisão que já pertence a esta
    camada, não à HTTP.
    """

    FOUND = "found"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ReportLink:
    """Representação tipada de uma linha de `report_links`. `token` nunca
    deve ser logado por inteiro — ver `_fingerprint`.
    """

    token: str
    artifact_id: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ReportLinkResolution:
    status: ReportLinkStatus
    link: ReportLink | None = None


def _fingerprint(token: str) -> str:
    """Correlação segura para logs/erros — os primeiros 8 caracteres de
    um token de 43 caracteres não recuperam a capability, mas bastam para
    correlacionar duas linhas de log da MESMA emissão/resolução.
    """
    return token[:8]


def _row_to_link(row: tuple[object, ...]) -> ReportLink:
    token, artifact_id, created_at, expires_at = row
    assert isinstance(token, str)
    assert isinstance(artifact_id, str)
    assert isinstance(created_at, datetime)
    assert isinstance(expires_at, datetime)
    return ReportLink(
        token=token, artifact_id=artifact_id, created_at=created_at, expires_at=expires_at
    )


class ReportLinkStore:
    """Filesystem-agnóstica de propósito — só sabe sobre `artifact_id`
    como uma string opaca, nunca sobre `data_dir`/caminho de arquivo. A
    checagem de que o `.html` correspondente existe é responsabilidade de
    `issue_report_link` (função livre abaixo), a camada que coordena esta
    classe com `report_store.py` — mantém esta classe testável e reusável
    sem precisar de um filesystem real.
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._store.execute(CREATE_REPORT_LINKS_TABLE)
        for statement in MIGRATE_REPORT_LINKS_TABLE:
            self._store.execute(statement)

    def _valid_for_artifact(self, artifact_id: str, *, now: datetime) -> ReportLink | None:
        rows = self._store.execute_returning(
            """
            SELECT token, artifact_id, created_at, expires_at FROM report_links
            WHERE artifact_id = ? AND expires_at > ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [artifact_id, now],
        )
        return _row_to_link(rows[0]) if rows else None

    def issue(
        self,
        artifact_id: str,
        *,
        ttl_days: float = DEFAULT_REPORT_LINK_TTL_DAYS,
        now: datetime | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> ReportLink:
        """Idempotente por design (decisão de produto, v1): uma emissão
        repetida para o MESMO `artifact_id`, enquanto uma capability ainda
        válida existir, devolve essa MESMA capability — nunca uma nova.
        Isso evita duas URLs para o mesmo relatório (um retry de entrega
        do Discord nunca muda o link que o usuário já recebeu) e, junto
        com `self._lock`, torna duas emissões CONCORRENTES do mesmo
        artifact seguras: a segunda thread a chegar ao `with self._lock`
        vê o token que a primeira acabou de persistir e o reusa, nunca
        gera um segundo.

        `artifact_id` nunca é validado aqui (formato/existência são
        responsabilidade de `issue_report_link`) — esta classe confia no
        que recebe, exatamente como `BenchmarkBuildProgressStore` confia
        nos `RankingCandidate` que já chegam validados.
        """
        resolved_now = now or now_utc_naive()
        factory = token_factory or _default_token_factory
        expires_at = resolved_now + timedelta(days=ttl_days)

        with self._lock:
            existing = self._valid_for_artifact(artifact_id, now=resolved_now)
            if existing is not None:
                log.debug(
                    "report_link.reused",
                    artifact_id=artifact_id,
                    token_fingerprint=_fingerprint(existing.token),
                )
                return existing

            collisions = 0
            for attempt in range(_MAX_TOKEN_GENERATION_ATTEMPTS):
                token = factory()
                try:
                    self._store.execute(
                        "INSERT INTO report_links (token, artifact_id, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?)",
                        [token, artifact_id, resolved_now, expires_at],
                    )
                except duckdb.ConstraintException:
                    # NUNCA encadear a exceção original (`from exc`) nem
                    # guardá-la: verificado empiricamente nesta auditoria
                    # (CL.9A) que `duckdb.ConstraintException.args[0]`
                    # inclui o VALOR bruto que violou a constraint —
                    # `'Duplicate key "token: <token completo>" ...'`. Um
                    # `raise ... from exc` faria esse texto reaparecer
                    # inteiro em qualquer traceback formatado com
                    # `exc_info=True` (ex.: `_cmd_serve`'s
                    # `process.unexpected_error`). O log estruturado abaixo
                    # já carrega tudo que é preciso para diagnosticar
                    # (artifact_id, contagem de tentativas) sem o token.
                    collisions += 1
                    log.warning(
                        "report_link.token_collision",
                        artifact_id=artifact_id,
                        attempt=attempt + 1,
                    )
                    continue
                log.info(
                    "report_link.issued",
                    artifact_id=artifact_id,
                    token_fingerprint=_fingerprint(token),
                    expires_at=expires_at.isoformat(),
                )
                return ReportLink(
                    token=token,
                    artifact_id=artifact_id,
                    created_at=resolved_now,
                    expires_at=expires_at,
                )

        raise ReportLinkTokenExhaustedError(
            f"não foi possível gerar um token único para {artifact_id!r} após "
            f"{collisions} colisões consecutivas"
        ) from None

    def resolve(self, token: str, *, now: datetime | None = None) -> ReportLinkResolution:
        """Nunca apaga um token expirado — só distingue. `purge_expired`
        é a operação explícita para remover.
        """
        resolved_now = now or now_utc_naive()
        rows = self._store.execute_returning(
            "SELECT token, artifact_id, created_at, expires_at FROM report_links WHERE token = ?",
            [token],
        )
        if not rows:
            return ReportLinkResolution(status=ReportLinkStatus.NOT_FOUND)

        link = _row_to_link(rows[0])
        if link.expires_at <= resolved_now:
            return ReportLinkResolution(status=ReportLinkStatus.EXPIRED, link=link)
        return ReportLinkResolution(status=ReportLinkStatus.FOUND, link=link)

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Primitiva explícita — CL.2 NUNCA a agenda. CL.3/CL.6 ou
        manutenção futura decide quando chamá-la. Devolve quantas linhas
        foram removidas.
        """
        resolved_now = now or now_utc_naive()
        rows = self._store.execute_returning(
            "DELETE FROM report_links WHERE expires_at <= ? RETURNING token", [resolved_now]
        )
        if rows:
            log.info("report_link.purged", count=len(rows))
        return len(rows)


def issue_report_link(
    link_store: ReportLinkStore,
    *,
    data_dir: Path,
    artifact_id: str,
    ttl_days: float = DEFAULT_REPORT_LINK_TTL_DAYS,
    now: datetime | None = None,
    token_factory: Callable[[], str] | None = None,
) -> ReportLink:
    """A função de emissão que a camada de entrega (futura) deve chamar —
    coordena `ReportLinkStore` (DB) com `report_store.py` (filesystem),
    reusando a MESMA validação de `artifact_id` que já protege
    `report_path_for` contra path traversal, em vez de duplicar uma regex
    divergente aqui.

    1. Valida `artifact_id` — via `report_path_for`, que já rejeita
       qualquer coisa fora de `[A-Za-z0-9_-]{1,64}` (barra, backslash,
       `..`, tudo) levantando `ReportPersistenceError`.
    2. Garante que o `.html` correspondente existe — `Report
       ArtifactNotFoundError` (uma `ReportPersistenceError`) se não.
    3. Delega a `ReportLinkStore.issue` para gerar/reusar o token.

    Nunca conhece DuckDNS, hostname, HTTPS ou `/r/` — só a capability
    persistida. Devolve o `ReportLink` tipado, nunca uma URL completa.
    """
    path = report_path_for(data_dir, artifact_id)
    if not path.exists():
        raise ReportArtifactNotFoundError(
            f"nenhum relatório persistido para o artifact {artifact_id!r}"
        )
    return link_store.issue(artifact_id, ttl_days=ttl_days, now=now, token_factory=token_factory)
