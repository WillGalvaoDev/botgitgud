"""CL.3 — servidor HTTP local que resolve capability links (CL.2) em
relatórios HTML persistidos (`report_store.py`).

    HTTP client -> aiohttp -> GET /r/{token}
                                  |
                                  v
                        ReportLinkStore.resolve()
                                  |
                    +-------------+-------------+
                    |             |             |
                 NOT_FOUND     EXPIRED        FOUND
                    |             |             |
                   404           410     artifact_id -> report_path_for -> HTML

Bind é SEMPRE loopback (`127.0.0.1`) por padrão — nunca `0.0.0.0`. HTTPS,
reverse proxy e hostname público são infraestrutura posterior (CL.4+); o
futuro Caddy será o único processo exposto à Internet, e este servidor
nunca deveria ser alcançável de fora da própria máquina. `aiohttp` já é
dependência transitiva do discord.py (3.14.3, confirmado nesta auditoria)
— nenhum framework web novo.

Rotas: só `GET /r/{token}` e `GET /healthz`. `add_get()` já dá 405 de
graça para POST/PUT no mesmo caminho e 200 com corpo vazio para HEAD
(comportamento nativo do `aiohttp.web.UrlDispatcher`, verificado, não
implementado à mão); qualquer rota nunca registrada já cai no 404 padrão
do dispatcher sem nenhum handler extra.

**Token nunca vira path.** O único caminho do token até um arquivo é:
`token` -> `ReportLinkStore.resolve()` (busca por igualdade exata na
coluna `token`, nunca concatenação) -> `artifact_id` já validado por
`report_store.report_path_for` na EMISSÃO (CL.2) -> `report_path_for`
de novo aqui, a MESMA função, nunca um `data_dir / token` construído à
mão. Isso é o que torna traversal estruturalmente impossível, não uma
sanitização de string.

**DuckDB sem executor**: `ReportLinkStore.resolve()` é uma única consulta
SQL por PK-like lookup numa tabela pequena — rápida o bastante (medida:
sub-milissegundo) para chamar SÍNCRONA e diretamente dentro do handler
`async def`, sem `run_in_executor`/threadpool. Introduzir um pool só por
dogma pagaria overhead real (troca de contexto entre threads, uma
segunda fila) por um ganho que não existe aqui — `Store` já serializa
tudo por um `threading.RLock` de qualquer forma, então paralelizar a
consulta não paralelizaria o acesso ao banco.

**Access log desligado.** `AppRunner(..., access_log=None)` — verificado
empiricamente: sem isto, o formato padrão do aiohttp
(`%a %t "%r" %s %b ...`) inclui `%r` (a linha de requisição inteira,
`GET /r/<token> HTTP/1.1`) em `logging.getLogger("aiohttp.access")`, que
herdaria qualquer handler configurado no logger raiz do processo — o
capability apareceria em `botgitgud.jsonl`/`journalctl` por baixo do
structured logging deste projeto, nunca pela vontade deste módulo. Os
logs estruturados PRÓPRIOS deste módulo (`report_server.*`) só carregam
`token_fingerprint` (8 primeiros chars, mesmo helper de
`bot/report_links.py::_fingerprint`, reusado — nunca reimplementado) ou
`artifact_id`, nunca o token inteiro.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import structlog
from aiohttp import web

from botgitgud.bot.job_models import now_utc_naive
from botgitgud.bot.report_links import ReportLinkStatus, ReportLinkStore, _fingerprint
from botgitgud.bot.report_store import ReportPersistenceError, load_report, report_path_for

log = structlog.get_logger(__name__)

DEFAULT_REPORT_SERVER_HOST = "127.0.0.1"
DEFAULT_REPORT_SERVER_PORT = 8080

# CSP restritiva por auditoria, não por padrão genérico: report/html_report.py
# (o único gerador de relatório) foi varrido nesta rodada por
# <script>/javascript:/<iframe>/atributo on*=/href|src externo/@import/
# @font-face — zero ocorrências (o módulo já documenta "no CDN, no
# external resource" desde T3.4: todo <style> é inline, todo gráfico é
# SVG server-rendered). `default-src 'none'` bloqueia TUDO por padrão;
# `style-src 'unsafe-inline'` é a ÚNICA exceção necessária, para o
# <style> inline no <head> continuar renderizando. Se o renderer um dia
# ganhar imagem/fonte/script externo, esta política PRECISA ser revisada
# junto — nunca ampliada em silêncio.
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    # A URL em si carrega a capability secret — um Referer vazado para
    # qualquer link clicado DENTRO do relatório entregaria o token a um
    # terceiro. Custo zero: o relatório não depende de Referer para nada.
    "Referrer-Policy": "no-referrer",
    # O token é uma bearer capability — um proxy/cache público que
    # guardasse a resposta serviria o MESMO relatório para qualquer um
    # que descobrisse (ou reusasse) a URL depois. `private` restringe a
    # cache do próprio navegador; `no-store` impede até isso de
    # persistir em disco.
    "Cache-Control": "private, no-store",
}

_PLAIN_TEXT = "text/plain"

_LINK_STORE_KEY = web.AppKey("link_store", ReportLinkStore)
_DATA_DIR_KEY = web.AppKey("data_dir", Path)
_NOW_KEY = web.AppKey[Callable[[], datetime]]("now")


def _plain(status: int, text: str) -> web.Response:
    return web.Response(status=status, text=text, content_type=_PLAIN_TEXT, charset="utf-8")


async def _handle_healthz(_request: web.Request) -> web.Response:
    """Só prova que o processo está vivo — nenhum detalhe interno (sem
    warehouse path, sem budget WCL, sem token do Discord, sem qualquer
    coisa que um `!status` do bot já não exponha por um caminho
    autenticado).
    """
    return web.Response(status=200, text="ok", content_type=_PLAIN_TEXT, charset="utf-8")


async def _handle_report(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    fingerprint = _fingerprint(token)
    try:
        return await _resolve_and_serve(request, token, fingerprint)
    except Exception:
        log.error("report_server.internal_error", token_fingerprint=fingerprint, exc_info=True)
        return _plain(500, "internal server error")


async def _resolve_and_serve(request: web.Request, token: str, fingerprint: str) -> web.Response:
    link_store = request.app[_LINK_STORE_KEY]
    data_dir = request.app[_DATA_DIR_KEY]
    now_provider = request.app[_NOW_KEY]

    # GET é side-effect free por construção: `resolve()` (CL.2) só lê —
    # nunca cria, renova ou apaga uma report_link. Nenhum código deste
    # módulo chama `issue`/`purge_expired`.
    resolution = link_store.resolve(token, now=now_provider())

    if resolution.status is ReportLinkStatus.NOT_FOUND:
        return _plain(404, "not found")
    if resolution.status is ReportLinkStatus.EXPIRED:
        return _plain(410, "gone")

    assert resolution.link is not None  # FOUND sempre carrega o link
    artifact_id = resolution.link.artifact_id

    try:
        path = report_path_for(data_dir, artifact_id)
        if not path.exists():
            raise ReportPersistenceError(f"artifact ausente: {artifact_id!r}")
        html = load_report(path)
    except ReportPersistenceError as exc:
        # Capability válida, recurso servido não está mais disponível
        # (arquivo apagado/corrompido/nunca existiu apesar do link
        # persistido apontar para ele) — nunca um 500 genérico para um
        # modo de falha ANTECIPADO. Preferência explícita: 404, porque a
        # pergunta que a URL faz ("existe algo aqui?") tem resposta não —
        # distinto de um bug interno inesperado, que continua sendo 500.
        log.warning(
            "report_server.artifact_missing",
            artifact_id=artifact_id,
            token_fingerprint=fingerprint,
            error=str(exc),
        )
        return _plain(404, "not found")

    return web.Response(
        status=200,
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers=_SECURITY_HEADERS,
    )


def create_report_app(
    *,
    link_store: ReportLinkStore,
    data_dir: Path,
    now: Callable[[], datetime] | None = None,
) -> web.Application:
    """Monta a `aiohttp.web.Application` — sem side effect de rede
    nenhum (não faz bind, não escuta porta; isso é `ReportServer`
    abaixo). `now` é injetável para testes determinísticos de expiração
    (nunca `sleep` por 30 dias); o default é o MESMO relógio UTC-naive
    que `report_links.py` já usa em todo o resto do warehouse.
    """
    app = web.Application()
    app[_LINK_STORE_KEY] = link_store
    app[_DATA_DIR_KEY] = data_dir
    app[_NOW_KEY] = now or now_utc_naive

    app.router.add_get("/r/{token}", _handle_report)
    app.router.add_get("/healthz", _handle_healthz)
    return app


class ReportServer:
    """Lifecycle explícito (`start`/`stop`) em cima de
    `AppRunner`/`TCPSite` — a menor camada que embrulha as duas etapas
    que `aiohttp.web.run_app` faria por baixo dos panos, mas sem tomar o
    thread principal (CL.5/CL.6 vão rodar isto dentro do MESMO processo
    async do bot Discord, então nunca pode ser bloqueante).

    `port=0` deixa o SO escolher uma porta livre — `self.port` devolve a
    porta REAL vinda de `AppRunner.addresses` depois de `start()`, nunca
    o valor pedido; testes usam isto para nunca colidir com nada já
    escutando na máquina.
    """

    def __init__(
        self,
        *,
        link_store: ReportLinkStore,
        data_dir: Path,
        host: str = DEFAULT_REPORT_SERVER_HOST,
        port: int = DEFAULT_REPORT_SERVER_PORT,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._app = create_report_app(link_store=link_store, data_dir=data_dir, now=now)
        self._host = host
        self._requested_port = port
        self._runner: web.AppRunner | None = None
        self._bound_port: int | None = None

    @property
    def port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("ReportServer ainda não foi iniciado (start() não chamado)")
        return self._bound_port

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    async def start(self) -> None:
        if self._runner is not None:
            raise RuntimeError(
                "ReportServer já está rodando — chame stop() antes de start() de novo"
            )
        # access_log=None: ver docstring do módulo — nunca deixar o
        # formato padrão do aiohttp (que inclui a URL inteira, portanto
        # o token) alcançar nenhum handler de logging configurado no
        # processo.
        runner = web.AppRunner(self._app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._requested_port)
        try:
            await site.start()
        except OSError:
            # Nunca deixa um runner pela metade: se o bind falhar (porta
            # já em uso), este objeto continua em estado "nunca iniciado"
            # — uma chamada de start() futura pode tentar de novo, e
            # nenhum socket/task órfão sobra deste tentativa.
            await runner.cleanup()
            raise
        self._runner = runner
        self._bound_port = runner.addresses[0][1]
        log.info("report_server.started", host=self._host, port=self._bound_port)

    async def stop(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._bound_port = None
        log.info("report_server.stopped")

    async def __aenter__(self) -> ReportServer:
        await self.start()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.stop()
