"""CL.3 — servidor HTTP local (loopback) que resolve capability links
(CL.2) em relatórios HTML persistidos (`report_store.py`).

Zero WCL, zero Discord: só `aiohttp` real (`web.AppRunner`/`TCPSite`)
falando com `127.0.0.1` numa porta efêmera (`port=0`) e um `Store`
DuckDB real em disco (nunca `:memory:`). Sem `pytest-asyncio` instalado
— mesma convenção já estabelecida em `test_interactive_delivery.py`/
`test_discord_bot.py`: função de teste síncrona que chama
`asyncio.run(...)` sobre o corpo assíncrono real.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from botgitgud.bot.job_models import now_utc_naive
from botgitgud.bot.report_links import ReportLink, ReportLinkStore, issue_report_link
from botgitgud.bot.report_server import DEFAULT_REPORT_SERVER_PORT, ReportServer
from botgitgud.bot.report_store import persist_report
from botgitgud.ingest.store import Store


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _env(
    tmp_path: Path, *, html: str = "<html><body>ok</body></html>"
) -> tuple[Store, ReportLinkStore, ReportLink]:
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    persist_report(tmp_path, "job1", html)
    link = issue_report_link(links, data_dir=tmp_path, artifact_id="job1")
    return store, links, link


async def _get(base: str, path: str, *, method: str = "GET") -> aiohttp.ClientResponse:
    async with aiohttp.ClientSession() as session, session.request(method, base + path) as resp:
        await resp.read()
        return resp


# ==================================================================================
# 1-7: resolução FOUND e headers
# ==================================================================================


def test_valid_token_returns_200(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.status == 200
    store.close()


def test_content_type_is_html_utf8(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
    store.close()


def test_response_body_matches_the_persisted_html_exactly(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path, html="<html><body>hello world</body></html>")

    async def scenario() -> str:
        async with (
            ReportServer(link_store=links, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{server.port}/r/{link.token}") as resp,
        ):
            return await resp.text()

    body = _run(scenario())
    assert body == "<html><body>hello world</body></html>"
    store.close()


def test_x_content_type_options_nosniff(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    store.close()


def test_content_security_policy_is_restrictive(tmp_path: Path) -> None:
    """5: `default-src 'none'; style-src 'unsafe-inline'` — auditado contra
    o renderer real (report/html_report.py), que não usa script, iframe,
    handler inline, nem recurso externo algum.
    """
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert (
        resp.headers["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'"
    )
    store.close()


def test_referrer_policy_no_referrer(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    store.close()


def test_cache_control_private_no_store(tmp_path: Path) -> None:
    """7: o token é uma bearer capability — nunca deve acabar num cache
    público de proxy/navegador.
    """
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.headers["Cache-Control"] == "private, no-store"
    store.close()


# ==================================================================================
# 8-11: NOT_FOUND / EXPIRED / boundary / missing artifact
# ==================================================================================


def test_unknown_token_returns_404(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", "/r/never-issued-token")

    resp = _run(scenario())
    assert resp.status == 404
    store.close()


def test_expired_token_returns_410(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    persist_report(tmp_path, "job1", "<html/>")
    issued_at = now_utc_naive()
    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id="job1", now=issued_at, ttl_days=1.0
    )
    later = issued_at + timedelta(days=2)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(
            link_store=links, data_dir=tmp_path, port=0, now=lambda: later
        ) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.status == 410
    store.close()


def test_expiry_boundary_is_inherited_from_cl2(tmp_path: Path) -> None:
    """10: o servidor nunca recalcula a regra — só passa `now` adiante
    para `resolve()`. Exatamente no instante de expiração: EXPIRED (a
    MESMA semântica que CL.2 já prova isoladamente).
    """
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    persist_report(tmp_path, "job1", "<html/>")
    issued_at = now_utc_naive()
    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id="job1", now=issued_at, ttl_days=1.0
    )

    async def scenario_at_boundary() -> aiohttp.ClientResponse:
        async with ReportServer(
            link_store=links, data_dir=tmp_path, port=0, now=lambda: link.expires_at
        ) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    async def scenario_just_before() -> aiohttp.ClientResponse:
        just_before = link.expires_at - timedelta(microseconds=1)
        async with ReportServer(
            link_store=links, data_dir=tmp_path, port=0, now=lambda: just_before
        ) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    assert _run(scenario_at_boundary()).status == 410
    assert _run(scenario_just_before()).status == 200
    store.close()


def test_valid_capability_with_missing_artifact_returns_404(tmp_path: Path) -> None:
    """11: capability válida, arquivo desapareceu depois de emitida —
    decisão explícita do projeto: 404 (a capability aponta para algo que
    não está mais lá; distinto de um bug interno, que continua 500).
    """
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    persist_report(tmp_path, "job1", "<html/>")
    link = issue_report_link(links, data_dir=tmp_path, artifact_id="job1")
    (tmp_path / "reports" / "job1.html").unlink()  # o arquivo evapora

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    resp = _run(scenario())
    assert resp.status == 404
    store.close()


# ==================================================================================
# 12-14: métodos
# ==================================================================================


def test_post_to_report_route_is_405(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}", method="POST")

    assert _run(scenario()).status == 405
    store.close()


def test_put_to_report_route_is_405(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}", method="PUT")

    assert _run(scenario()).status == 405
    store.close()


def test_head_returns_200_with_empty_body(tmp_path: Path) -> None:
    """14: comportamento nativo do aiohttp para uma rota GET — auditado
    (não implementado à mão) e agora travado por teste.
    """
    store, links, link = _env(tmp_path)

    async def scenario() -> tuple[int, bytes]:
        async with (
            ReportServer(link_store=links, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
            session.head(f"http://127.0.0.1:{server.port}/r/{link.token}") as resp,
        ):
            body = await resp.read()
            return resp.status, body

    status, body = _run(scenario())
    assert status == 200
    assert body == b""
    store.close()


# ==================================================================================
# 15-16: /healthz
# ==================================================================================


def test_healthz_returns_200(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            return await _get(f"http://127.0.0.1:{server.port}", "/healthz")

    resp = _run(scenario())
    assert resp.status == 200
    store.close()


def test_healthz_reveals_no_internal_details(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)

    async def scenario() -> str:
        async with (
            ReportServer(link_store=links, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{server.port}/healthz") as resp,
        ):
            return await resp.text()

    body = _run(scenario())
    lowered = body.lower()
    for forbidden in ("warehouse", "duckdb", "discord", "token", "wcl", str(tmp_path).lower()):
        assert forbidden not in lowered
    store.close()


# ==================================================================================
# 17: rota desconhecida
# ==================================================================================


def test_unknown_route_returns_404(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)

    async def scenario() -> aiohttp.ClientResponse:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            base = f"http://127.0.0.1:{server.port}"
            responses = []
            for path in ("/", "/reports", "/list", "/debug", "/metrics"):
                responses.append(await _get(base, path))
            return responses  # type: ignore[return-value]

    responses = _run(scenario())
    assert all(r.status == 404 for r in responses)
    store.close()


# ==================================================================================
# 18-20: redação em logs estruturados
# ==================================================================================


def test_token_never_appears_in_structured_logs(tmp_path: Path) -> None:
    """18/20: captura os eventos estruturados emitidos por start/GET/stop
    e confirma que o token COMPLETO nunca aparece — só o fingerprint de 8
    chars é permitido.
    """
    import structlog

    store, links, link = _env(tmp_path)
    events: list[dict[str, object]] = []

    def _capture(_logger: object, _method: str, event_dict: Any) -> Any:
        events.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, _capture],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )
    try:

        async def scenario() -> None:
            async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
                await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")
                await _get(f"http://127.0.0.1:{server.port}", "/r/unknown-token-xyz")

        _run(scenario())
        store.close()
    finally:
        structlog.reset_defaults()

    assert events
    for event in events:
        rendered = repr(event)
        assert link.token not in rendered, f"token completo vazou: {event}"


def test_token_never_appears_in_aiohttp_access_logs(tmp_path: Path) -> None:
    """19: `access_log=None` desliga o access log padrão do aiohttp
    inteiramente — provado anexando um handler ao logger
    `aiohttp.access` e confirmando ZERO registros, mesmo com uma
    requisição real carregando o token na URL.
    """
    store, links, link = _env(tmp_path)
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    access_logger = logging.getLogger("aiohttp.access")
    handler = _Capture()
    previous_level = access_logger.level
    access_logger.addHandler(handler)
    access_logger.setLevel(logging.DEBUG)
    try:

        async def scenario() -> None:
            async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
                await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

        _run(scenario())
    finally:
        access_logger.removeHandler(handler)
        access_logger.setLevel(previous_level)
        store.close()

    assert captured == []
    for message in captured:
        assert link.token not in message


# ==================================================================================
# 21-24: traversal
# ==================================================================================


def test_dot_dot_slash_traversal_never_reaches_a_file(tmp_path: Path) -> None:
    """21: um `../` literal no path do cliente é normalizado ANTES de
    sair (o cliente HTTP colapsa `/r/../secret` -> `/secret`), então nem
    alcança a rota `/r/{token}` — 404 do dispatcher genérico. Prova
    também que o arquivo secreto colocado fora de `reports/` nunca
    aparece no corpo da resposta.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    store, links, _link = _env(tmp_path)

    async def scenario() -> tuple[int, str]:
        async with (
            ReportServer(link_store=links, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{server.port}/r/../secret.txt") as resp,
        ):
            return resp.status, await resp.text()

    status, body = _run(scenario())
    assert status == 404
    assert "TOP SECRET" not in body
    store.close()


def test_encoded_traversal_resolves_as_a_literal_unknown_token(tmp_path: Path) -> None:
    """22: percent-encoded (`%2e%2e%2f`) chega ao handler como o TEXTO
    LITERAL `../secret.txt` dentro de `match_info["token"]" — nunca
    decodificado pelo filesystem. Como esse texto nunca foi emitido como
    token nenhum, `resolve()` devolve NOT_FOUND (404) muito antes de
    qualquer `report_path_for`/acesso a arquivo ser sequer considerado.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    store, links, _link = _env(tmp_path)

    async def scenario() -> tuple[int, str]:
        async with (
            ReportServer(link_store=links, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
        ):
            url = f"http://127.0.0.1:{server.port}/r/..%2fsecret.txt"
            async with session.get(url) as resp:
                return resp.status, await resp.text()

    status, body = _run(scenario())
    assert status == 404
    assert "TOP SECRET" not in body
    store.close()


def test_slash_and_backslash_shaped_tokens_never_reach_a_file(tmp_path: Path) -> None:
    """23/24: `a%2Fb`/`a%5Cb` chegam como os textos literais `a/b`/`a\\b`
    — nenhum dos dois foi emitido como token, então nenhum vira um
    caminho de arquivo. O token NUNCA é concatenado a `data_dir`; o
    único caminho até um arquivo passa por `resolve()` (busca exata por
    igualdade) primeiro.
    """
    store, links, _link = _env(tmp_path)

    async def scenario() -> list[int]:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            base = f"http://127.0.0.1:{server.port}"
            statuses = []
            for path in ("/r/a%2Fb", "/r/a%5Cb", "/r/..%2f..%2fetc%2fpasswd"):
                statuses.append((await _get(base, path)).status)
            return statuses

    statuses = _run(scenario())
    assert statuses == [404, 404, 404]
    store.close()


# ==================================================================================
# 25-27: GET é side-effect free
# ==================================================================================


def test_get_never_mutates_expires_at(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> None:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    _run(scenario())
    row = store.execute_returning(
        "SELECT expires_at FROM report_links WHERE token = ?", [link.token]
    )
    assert row[0][0] == link.expires_at
    store.close()


def test_get_never_creates_a_new_report_link(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> None:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            for _ in range(5):
                await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")

    _run(scenario())
    count = store.execute_returning("SELECT count(*) FROM report_links")[0][0]
    assert count == 1
    store.close()


def test_repeated_valid_get_stays_valid(tmp_path: Path) -> None:
    store, links, link = _env(tmp_path)

    async def scenario() -> list[int]:
        async with ReportServer(link_store=links, data_dir=tmp_path, port=0) as server:
            base = f"http://127.0.0.1:{server.port}"
            return [(await _get(base, f"/r/{link.token}")).status for _ in range(3)]

    assert _run(scenario()) == [200, 200, 200]
    store.close()


# ==================================================================================
# 28-32: lifecycle
# ==================================================================================


def test_server_starts_and_reports_running(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    server = ReportServer(link_store=links, data_dir=tmp_path, port=0)

    async def scenario() -> bool:
        await server.start()
        try:
            return server.is_running
        finally:
            await server.stop()

    assert _run(scenario()) is True
    store.close()


def test_server_stops_and_releases_the_port(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    server = ReportServer(link_store=links, data_dir=tmp_path, port=0)

    async def scenario() -> bool:
        await server.start()
        await server.stop()
        return server.is_running

    assert _run(scenario()) is False
    store.close()


def test_server_restarts_on_the_same_port_after_stop(tmp_path: Path) -> None:
    """30: para em `port`, e um SEGUNDO ciclo start/stop na MESMA porta
    (agora liberada) funciona — sem "address already in use".
    """
    store, links, link = _env(tmp_path)
    server = ReportServer(link_store=links, data_dir=tmp_path, port=0)

    async def scenario() -> tuple[int, int]:
        await server.start()
        port = server.port
        status_first = (await _get(f"http://127.0.0.1:{port}", f"/r/{link.token}")).status
        await server.stop()

        server2 = ReportServer(link_store=links, data_dir=tmp_path, port=port)
        await server2.start()
        status_second = (await _get(f"http://127.0.0.1:{port}", f"/r/{link.token}")).status
        await server2.stop()
        return status_first, status_second

    assert _run(scenario()) == (200, 200)
    store.close()


def test_port_zero_lets_the_os_choose_a_free_port(tmp_path: Path) -> None:
    store = Store(tmp_path)
    links = ReportLinkStore(store)
    server = ReportServer(link_store=links, data_dir=tmp_path, port=0)

    async def scenario() -> int:
        await server.start()
        try:
            return server.port
        finally:
            await server.stop()

    port = _run(scenario())
    assert port != 0
    assert port > 0
    store.close()


def test_two_servers_on_the_same_port_the_second_fails_the_first_stays_healthy(
    tmp_path: Path,
) -> None:
    """32: o segundo `start()` na mesma porta falha claramente
    (`OSError`), nunca deixa um runner pela metade, e o PRIMEIRO servidor
    continua respondendo normalmente depois disso.
    """
    store, links, link = _env(tmp_path)
    server1 = ReportServer(link_store=links, data_dir=tmp_path, port=0)

    async def scenario() -> tuple[bool, int]:
        await server1.start()
        port = server1.port
        server2 = ReportServer(link_store=links, data_dir=tmp_path, port=port)
        raised = False
        try:
            await server2.start()
        except OSError:
            raised = True
        assert server2.is_running is False  # nunca ficou "meio iniciado"

        status_after = (await _get(f"http://127.0.0.1:{port}", f"/r/{link.token}")).status
        await server1.stop()
        return raised, status_after

    raised, status_after = _run(scenario())
    assert raised is True
    assert status_after == 200
    store.close()


def test_default_port_constant_is_8080() -> None:
    assert DEFAULT_REPORT_SERVER_PORT == 8080


# ==================================================================================
# 33: exceção interna -> 500 genérico
# ==================================================================================


def test_unexpected_internal_error_returns_generic_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, links, link = _env(tmp_path)

    def _boom(self: object, _token: str, *, now: object = None) -> None:
        raise RuntimeError("erro interno simulado com segredo: super-secret-detail")

    monkeypatch.setattr(ReportLinkStore, "resolve", _boom)

    async def scenario() -> tuple[int, str]:
        async with (
            ReportServer(link_store=links, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{server.port}/r/{link.token}") as resp,
        ):
            return resp.status, await resp.text()

    status, body = _run(scenario())
    assert status == 500
    assert "super-secret-detail" not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
    assert str(tmp_path) not in body
    store.close()


# ==================================================================================
# persistência realista: DuckDB em disco + HTTP real + restart
# ==================================================================================


def test_full_persistence_through_server_restart(tmp_path: Path) -> None:
    """Prova conjunta exigida: capability persistence + server lifecycle +
    restart persistence, tudo com componentes reais (Store em disco,
    servidor HTTP real, sem mocks).

    issue link -> start aiohttp -> GET -> 200
    stop -> close Store
    reopen Store -> restart server -> GET o MESMO token -> 200
    """
    store1 = Store(tmp_path)
    links1 = ReportLinkStore(store1)
    persist_report(tmp_path, "job1", "<html>persisted</html>")
    link = issue_report_link(links1, data_dir=tmp_path, artifact_id="job1")

    async def first_round() -> int:
        async with ReportServer(link_store=links1, data_dir=tmp_path, port=0) as server:
            return (await _get(f"http://127.0.0.1:{server.port}", f"/r/{link.token}")).status

    assert _run(first_round()) == 200
    store1.close()

    store2 = Store(tmp_path)
    links2 = ReportLinkStore(store2)

    async def second_round() -> tuple[int, str]:
        async with (
            ReportServer(link_store=links2, data_dir=tmp_path, port=0) as server,
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{server.port}/r/{link.token}") as resp,
        ):
            return resp.status, await resp.text()

    status, body = _run(second_round())
    assert status == 200
    assert body == "<html>persisted</html>"
    store2.close()
