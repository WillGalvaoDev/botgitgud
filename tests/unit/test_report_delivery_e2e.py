"""CL.5 — prova ponta-a-ponta do produto final: análise concluída -> HTML
persistido -> capability link (CL.2) -> resumo compacto (CL.4) + URL -> UMA
mensagem Discord -> HTTP GET real na URL entregue -> 200 com o MESMO HTML.

Zero WCL, zero Discord real: só `aiohttp` real (`ReportServer`, CL.3) numa
porta efêmera (`port=0`) em loopback, e um `Store` DuckDB real em disco.
Mesma convenção de `test_report_server.py`/`test_interactive_delivery.py`
— sem `pytest-asyncio`, funções síncronas chamando `asyncio.run(...)`.

Este arquivo também prova o item mais crítico do ticket: a regressão do
incidente de soak (Discord fazendo preview de um `.html` grande anexado
como texto) nunca pode voltar a acontecer neste caminho — ver
`test_regression_discord_delivery_never_attaches_html`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import aiohttp
import discord

import botgitgud.bot.discord_bot as discord_module
from botgitgud.bot.delivery import DeliveryContext, ReportDeliveryConfig, deliver_completed_report
from botgitgud.bot.discord_bot import BotGitGudBot
from botgitgud.bot.report_links import ReportLinkStore
from botgitgud.bot.report_server import ReportServer
from botgitgud.bot.report_store import persist_report
from botgitgud.ingest.store import Store
from botgitgud.ops.control import request_stop
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.discord_summary import MAX_DISCORD_REPORT_SUMMARY
from botgitgud.report.text import ReportHeader

_ARTIFACT_ID = "artifact-e2e"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _contract(**overrides: object) -> ReportContract:
    defaults: dict[str, object] = {
        "resultado": ReportHeader(
            "Zarad", "Fallen-King Salhadaar", "Warlock", "Demonology", 20, 300.0, 360.0
        ),
        "setup": None,
        "execucao": ExecutionSection(comparisons=(), performance=None, dps_gap=None),
        "top_actions": (),
        "confianca": ConfidenceSummary(
            reference_pool_members=40,
            matched_cohort_members=20,
            cohort_warnings=(),
            matched_covariates=(),
            relaxed_covariates=(),
        ),
        "manifest": None,
    }
    defaults.update(overrides)
    return ReportContract(**defaults)  # type: ignore[arg-type]


class _FakeChannel:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fail = fail

    async def send(self, content: str, **kwargs: Any) -> Any:
        if self._fail is not None:
            raise self._fail
        self.calls.append((content, kwargs))


def _forbidden(code: int = 50013) -> discord.Forbidden:
    response: Any = type("R", (), {"status": 403, "reason": "Forbidden"})()
    return discord.Forbidden(response, {"code": code, "message": "Missing Permissions"})


async def _http_get(url: str) -> aiohttp.ClientResponse:
    async with aiohttp.ClientSession() as session, session.get(url) as resp:
        await resp.read()
        return resp


def _extract_url(content: str) -> str:
    """Recorta a URL entre `](` e `)` — a mesma forma que
    `discord_summary.py::_link_block` produz.
    """
    return content.rsplit("](", 1)[1].rstrip(")")


# ==================================================================================
# HTTP E2E — o teste principal desta rodada: CL.2 + CL.3 + CL.4 + CL.5 juntos
# ==================================================================================


def test_full_pipeline_persist_issue_serve_deliver_and_fetch(tmp_path: Path) -> None:
    html = "<html><body>relatorio completo</body></html>"
    persist_report(tmp_path, _ARTIFACT_ID, html)
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)

    async def scenario() -> tuple[str, aiohttp.ClientResponse]:
        async with ReportServer(link_store=link_store, data_dir=tmp_path, port=0) as server:
            config = ReportDeliveryConfig(
                link_store=link_store,
                data_dir=tmp_path,
                public_base_url=f"http://127.0.0.1:{server.port}",
            )
            channel = _FakeChannel()
            outcome = await deliver_completed_report(
                channel,
                contract=_contract(),
                artifact_id=_ARTIFACT_ID,
                config=config,
                context=DeliveryContext(channel_id="1"),
            )
            assert outcome.delivered
            content = channel.calls[0][0]
            url = _extract_url(content)
            resp = await _http_get(url)
            return content, resp

    content, resp = _run(scenario())
    store.close()

    assert len(content) <= MAX_DISCORD_REPORT_SUMMARY
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    body = _run(_read_text(resp))
    assert body == html


async def _read_text(resp: aiohttp.ClientResponse) -> str:
    return await resp.text()


def test_delivered_url_survives_multiple_fetches_get_stays_side_effect_free(
    tmp_path: Path,
) -> None:
    html = "<html>x</html>"
    persist_report(tmp_path, _ARTIFACT_ID, html)
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)

    async def scenario() -> tuple[int, int]:
        async with ReportServer(link_store=link_store, data_dir=tmp_path, port=0) as server:
            config = ReportDeliveryConfig(
                link_store=link_store,
                data_dir=tmp_path,
                public_base_url=f"http://127.0.0.1:{server.port}",
            )
            channel = _FakeChannel()
            await deliver_completed_report(
                channel,
                contract=_contract(),
                artifact_id=_ARTIFACT_ID,
                config=config,
                context=DeliveryContext(),
            )
            url = _extract_url(channel.calls[0][0])
            first = (await _http_get(url)).status
            second = (await _http_get(url)).status
            return first, second

    first, second = _run(scenario())
    store.close()
    assert (first, second) == (200, 200)


# ==================================================================================
# retry / idempotência: mesmo artifact -> mesma capability -> mesma URL
# ==================================================================================


def test_retry_after_discord_failure_reuses_the_same_url_and_capability_row(
    tmp_path: Path,
) -> None:
    persist_report(tmp_path, _ARTIFACT_ID, "<html/>")
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)
    config = ReportDeliveryConfig(
        link_store=link_store, data_dir=tmp_path, public_base_url="https://botgitgud.duckdns.org"
    )

    failing = _FakeChannel(fail=_forbidden())
    first = _run(
        deliver_completed_report(
            failing,
            contract=_contract(),
            artifact_id=_ARTIFACT_ID,
            config=config,
            context=DeliveryContext(),
        )
    )
    assert first.status == "failed"

    succeeding = _FakeChannel()
    second = _run(
        deliver_completed_report(
            succeeding,
            contract=_contract(),
            artifact_id=_ARTIFACT_ID,
            config=config,
            context=DeliveryContext(),
        )
    )
    assert second.delivered
    delivered_url = _extract_url(succeeding.calls[0][0])

    rows = link_store._store.execute_returning(
        "SELECT token FROM report_links WHERE artifact_id = ?", [_ARTIFACT_ID]
    )
    assert len(rows) == 1
    assert delivered_url.endswith(f"/r/{rows[0][0]}")
    store.close()


def test_expired_capability_issues_a_new_one_on_next_delivery(tmp_path: Path) -> None:
    """25: expira ANTES de uma nova emissão/entrega — `issue_report_link`
    (CL.2) já resolve isto (uma capability expirada nunca é reusada); aqui
    provamos a integração ponta-a-ponta pelo caminho de entrega real, sem
    alterar nenhuma semântica de CL.2.
    """
    from datetime import timedelta

    from botgitgud.bot.job_models import now_utc_naive
    from botgitgud.bot.report_links import issue_report_link

    persist_report(tmp_path, _ARTIFACT_ID, "<html/>")
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)

    long_ago = now_utc_naive() - timedelta(days=40)
    expired_link = issue_report_link(
        link_store, data_dir=tmp_path, artifact_id=_ARTIFACT_ID, ttl_days=1.0, now=long_ago
    )

    config = ReportDeliveryConfig(
        link_store=link_store, data_dir=tmp_path, public_base_url="https://botgitgud.duckdns.org"
    )
    channel = _FakeChannel()
    outcome = _run(
        deliver_completed_report(
            channel,
            contract=_contract(),
            artifact_id=_ARTIFACT_ID,
            config=config,
            context=DeliveryContext(),
        )
    )
    assert outcome.delivered
    delivered_url = _extract_url(channel.calls[0][0])
    assert not delivered_url.endswith(f"/r/{expired_link.token}")

    rows = link_store._store.execute_returning(
        "SELECT token FROM report_links WHERE artifact_id = ?", [_ARTIFACT_ID]
    )
    assert len(rows) == 2  # a expirada continua lá (CL.2 nunca apaga sozinha) + a nova
    store.close()


# ==================================================================================
# regressão nomeada do incidente de soak
# ==================================================================================


def test_regression_discord_delivery_never_attaches_html(tmp_path: Path) -> None:
    """O incidente que interrompeu o soak: o Discord fez preview de um
    `.html` anexado como texto puro. Este teste prova, com um HTML
    deliberadamente grande (>40KB, como um relatório real), que a entrega
    produz exatamente UMA mensagem <=1800 chars e ZERO attachments — o
    HTML grande continua persistido e acessível pelo report server, nunca
    pelo Discord diretamente.
    """
    huge_html = "<html><body>" + ("x" * 41_000) + "</body></html>"
    assert len(huge_html) > 40_000
    persist_report(tmp_path, _ARTIFACT_ID, huge_html)
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)

    async def scenario() -> tuple[str, dict[str, Any], aiohttp.ClientResponse, int]:
        async with ReportServer(link_store=link_store, data_dir=tmp_path, port=0) as server:
            config = ReportDeliveryConfig(
                link_store=link_store,
                data_dir=tmp_path,
                public_base_url=f"http://127.0.0.1:{server.port}",
            )
            channel = _FakeChannel()
            outcome = await deliver_completed_report(
                channel,
                contract=_contract(),
                artifact_id=_ARTIFACT_ID,
                config=config,
                context=DeliveryContext(),
            )
            assert outcome.delivered
            content, kwargs = channel.calls[0]
            url = _extract_url(content)
            resp = await _http_get(url)
            return content, kwargs, resp, len(channel.calls)

    content, kwargs, resp, n_calls = _run(scenario())
    store.close()

    assert n_calls == 1  # exatamente uma mensagem
    assert len(content) <= MAX_DISCORD_REPORT_SUMMARY
    assert "file" not in kwargs and "files" not in kwargs and "attachments" not in kwargs
    assert resp.status == 200
    body = _run(_read_text(resp))
    assert len(body) > 40_000
    assert body == huge_html


# ==================================================================================
# BotGitGudBot lifecycle: setup_hook inicia o ReportServer, close() para
# ==================================================================================


async def _bare_bot(report_server: ReportServer) -> BotGitGudBot:
    """Constrói um `BotGitGudBot` real e roda só a parte de `__aenter__`
    que prepara `self.loop`/`self.http`/`self._connection` — SEM login,
    SEM gateway, SEM nenhuma chamada de rede ao Discord (verificado: só
    `_async_setup_hook()`, que é síncrona por dentro além de pegar o loop
    corrente). `bot.close()` funciona corretamente depois disto mesmo sem
    conexão real — testado empiricamente antes de este arquivo existir.
    """
    intents = discord.Intents.default()
    bot = BotGitGudBot(report_server=report_server, command_prefix="!", intents=intents)
    await bot._async_setup_hook()
    return bot


def test_setup_hook_starts_the_report_server(tmp_path: Path) -> None:
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)
    report_server = ReportServer(link_store=link_store, data_dir=tmp_path, port=0)

    async def scenario() -> bool:
        bot = await _bare_bot(report_server)
        await bot.setup_hook()
        try:
            return report_server.is_running
        finally:
            await bot.close()

    assert _run(scenario()) is True
    store.close()


def test_close_stops_the_report_server(tmp_path: Path) -> None:
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)
    report_server = ReportServer(link_store=link_store, data_dir=tmp_path, port=0)

    async def scenario() -> bool:
        bot = await _bare_bot(report_server)
        await bot.setup_hook()
        await bot.close()
        return report_server.is_running

    assert _run(scenario()) is False
    store.close()


def test_report_server_still_serves_while_bot_is_up(tmp_path: Path) -> None:
    """29/31: prova que o Store continua aberto e o ReportServer
    efetivamente serve enquanto o bot está de pé — não só que os objetos
    foram criados.
    """
    html = "<html>lifecycle</html>"
    persist_report(tmp_path, _ARTIFACT_ID, html)
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)
    report_server = ReportServer(link_store=link_store, data_dir=tmp_path, port=0)

    async def scenario() -> aiohttp.ClientResponse:
        bot = await _bare_bot(report_server)
        await bot.setup_hook()
        try:
            from botgitgud.bot.report_links import issue_report_link

            link = issue_report_link(link_store, data_dir=tmp_path, artifact_id=_ARTIFACT_ID)
            return await _http_get(f"http://127.0.0.1:{report_server.port}/r/{link.token}")
        finally:
            await bot.close()

    resp = _run(scenario())
    store.close()
    assert resp.status == 200


def test_setup_hook_failure_on_occupied_port_propagates_and_cleans_up(tmp_path: Path) -> None:
    """32/33: porta ocupada -> setup_hook levanta -> nada fica
    parcialmente iniciado (o SEGUNDO ReportServer nunca chega a
    `is_running=True`; o PRIMEIRO, usado só para ocupar a porta, continua
    saudável — igual à prova já feita em test_report_server.py para o
    ReportServer isolado, aqui repetida através do wiring do BotGitGudBot).
    """
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)
    occupier = ReportServer(link_store=link_store, data_dir=tmp_path, port=0)

    async def scenario() -> tuple[bool, bool]:
        await occupier.start()
        try:
            colliding = ReportServer(
                link_store=link_store, data_dir=tmp_path, host="127.0.0.1", port=occupier.port
            )
            bot = await _bare_bot(colliding)
            raised = False
            try:
                await bot.setup_hook()
            except OSError:
                raised = True
            # close() precisa ser seguro mesmo com start() tendo falhado —
            # nunca deixar nada pendurado.
            await bot.close()
            return raised, colliding.is_running
        finally:
            await occupier.stop()

    raised, still_running = _run(scenario())
    store.close()
    assert raised is True
    assert still_running is False


# ==================================================================================
# SIGTERM/controlled shutdown equivalente: stop.request -> bot.close() -> server.stop()
# ==================================================================================


def test_stop_request_watcher_stops_the_report_server_through_bot_close(tmp_path: Path) -> None:
    """34: o watcher de `stop.request` (ops/control.py) só chama
    `bot.close()` — a prova de que isso também para o ReportServer é
    exatamente o wiring que `BotGitGudBot.close()` adiciona (CL.5).
    """
    store = Store(tmp_path)
    link_store = ReportLinkStore(store)
    report_server = ReportServer(link_store=link_store, data_dir=tmp_path, port=0)

    async def scenario() -> bool:
        bot = await _bare_bot(report_server)
        await bot.setup_hook()
        assert report_server.is_running

        watcher = asyncio.ensure_future(
            discord_module._stop_request_watcher(bot, tmp_path, poll_interval_s=0.01)
        )
        await asyncio.sleep(0.03)
        assert report_server.is_running  # ainda não pedido
        request_stop(tmp_path)
        await asyncio.wait_for(watcher, timeout=2.0)
        return report_server.is_running

    assert _run(scenario()) is False
    store.close()
