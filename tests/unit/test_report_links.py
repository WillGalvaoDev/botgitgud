"""CL.2 — capability links persistentes para relatórios HTML.

Zero WCL, zero Discord, zero HTTP: tudo aqui é Store/DuckDB local e
filesystem via `report_store.py`. `tmp_path` é sempre um diretório real em
disco (nunca `:memory:`) — a persistência através de fechar/reabrir o
`Store` é exatamente o que a futura migração cloud precisa provar.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from botgitgud.bot.job_models import now_utc_naive
from botgitgud.bot.report_links import (
    CREATE_REPORT_LINKS_TABLE,
    DEFAULT_REPORT_LINK_TTL_DAYS,
    ReportArtifactNotFoundError,
    ReportLink,
    ReportLinkResolution,
    ReportLinkStatus,
    ReportLinkStore,
    ReportLinkTokenExhaustedError,
    issue_report_link,
)
from botgitgud.bot.report_store import ReportPersistenceError, persist_report
from botgitgud.ingest.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def _make_artifact(tmp_path: Path, artifact_id: str = "job1") -> str:
    persist_report(tmp_path, artifact_id, f"<html>{artifact_id}</html>")
    return artifact_id


# ==================================================================================
# 1-5: emissão
# ==================================================================================


def test_issue_creates_a_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)

    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)

    assert isinstance(link, ReportLink)
    assert link.token
    store.close()


def test_token_has_urlsafe32_shaped_entropy(tmp_path: Path) -> None:
    """2: `secrets.token_urlsafe(32)` produz 43 caracteres base64-urlsafe
    (32 bytes -> ceil(32*8/6) = 43, sem padding `=`), alfabeto
    `[A-Za-z0-9_-]`. Não reimplementamos o gerador — só confirmamos o
    formato que ele deve ter.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)

    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)

    assert len(link.token) == 43
    assert all(c.isalnum() or c in "-_" for c in link.token)
    store.close()


def test_artifact_id_is_persisted_correctly(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path, "job-xyz")

    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)

    assert link.artifact_id == "job-xyz"
    resolution = links.resolve(link.token)
    assert resolution.link is not None
    assert resolution.link.artifact_id == "job-xyz"
    store.close()


def test_created_at_matches_the_injected_now(tmp_path: Path) -> None:
    """4: `now` é injetável — sem depender do relógio real para
    determinismo.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    fixed_now = now_utc_naive().replace(microsecond=0)

    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id, now=fixed_now)

    assert link.created_at == fixed_now
    store.close()


def test_expires_at_is_created_at_plus_ttl(tmp_path: Path) -> None:
    """5: `expires_at = created_at + TTL`, TTL default = 30 dias, vindo de
    `DEFAULT_REPORT_LINK_TTL_DAYS` — nunca um `timedelta(days=30)` solto.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    fixed_now = now_utc_naive()

    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id, now=fixed_now)

    assert DEFAULT_REPORT_LINK_TTL_DAYS == 30.0
    assert link.expires_at == fixed_now + timedelta(days=DEFAULT_REPORT_LINK_TTL_DAYS)
    store.close()


def test_custom_ttl_is_respected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    fixed_now = now_utc_naive()

    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=fixed_now, ttl_days=1.0
    )

    assert link.expires_at == fixed_now + timedelta(days=1.0)
    store.close()


# ==================================================================================
# 6-10: resolução
# ==================================================================================


def test_resolve_valid_token_is_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)

    resolution = links.resolve(link.token)

    assert isinstance(resolution, ReportLinkResolution)
    assert resolution.status is ReportLinkStatus.FOUND
    assert resolution.link == link
    store.close()


def test_resolve_unknown_token_is_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)

    resolution = links.resolve("this-token-was-never-issued")

    assert resolution.status is ReportLinkStatus.NOT_FOUND
    assert resolution.link is None
    store.close()


def test_resolve_expired_token_is_expired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    issued_at = now_utc_naive()
    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=issued_at, ttl_days=1.0
    )

    resolution = links.resolve(link.token, now=issued_at + timedelta(days=2))

    assert resolution.status is ReportLinkStatus.EXPIRED
    assert resolution.link is not None
    assert resolution.link.token == link.token
    store.close()


def test_boundary_just_below_expiry_is_found(tmp_path: Path) -> None:
    """9a: now < expires_at -> FOUND."""
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    issued_at = now_utc_naive()
    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=issued_at, ttl_days=1.0
    )

    just_before = link.expires_at - timedelta(microseconds=1)
    resolution = links.resolve(link.token, now=just_before)

    assert resolution.status is ReportLinkStatus.FOUND
    store.close()


def test_boundary_exactly_at_expiry_is_expired(tmp_path: Path) -> None:
    """9b: now == expires_at -> EXPIRED (nunca FOUND)."""
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    issued_at = now_utc_naive()
    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=issued_at, ttl_days=1.0
    )

    resolution = links.resolve(link.token, now=link.expires_at)

    assert resolution.status is ReportLinkStatus.EXPIRED
    store.close()


def test_expired_token_is_not_deleted_by_resolve(tmp_path: Path) -> None:
    """10: resolver um token expirado NUNCA o remove — só distingue.
    Resolver de novo continua EXPIRED, nunca vira NOT_FOUND por conta
    disso.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    issued_at = now_utc_naive()
    link = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=issued_at, ttl_days=1.0
    )
    later = issued_at + timedelta(days=2)

    first = links.resolve(link.token, now=later)
    second = links.resolve(link.token, now=later)

    assert first.status is ReportLinkStatus.EXPIRED
    assert second.status is ReportLinkStatus.EXPIRED  # ainda lá, não sumiu
    store.close()


# ==================================================================================
# 11: restart persistence
# ==================================================================================


def test_token_resolves_after_store_close_and_reopen(tmp_path: Path) -> None:
    """11 — o requisito central para a migração cloud: fechar e reabrir o
    Store (restart do processo/supervisor/VM) preserva o token.
    """
    store1 = _store(tmp_path)
    links1 = ReportLinkStore(store1)
    artifact_id = _make_artifact(tmp_path)
    link = issue_report_link(links1, data_dir=tmp_path, artifact_id=artifact_id)
    store1.close()

    store2 = _store(tmp_path)
    links2 = ReportLinkStore(store2)
    resolution = links2.resolve(link.token)

    assert resolution.status is ReportLinkStatus.FOUND
    assert resolution.link is not None
    assert resolution.link.artifact_id == artifact_id
    store2.close()


# ==================================================================================
# 12: múltiplos artifacts
# ==================================================================================


def test_multiple_artifacts_get_independent_tokens(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    a = _make_artifact(tmp_path, "job-a")
    b = _make_artifact(tmp_path, "job-b")

    link_a = issue_report_link(links, data_dir=tmp_path, artifact_id=a)
    link_b = issue_report_link(links, data_dir=tmp_path, artifact_id=b)

    assert link_a.token != link_b.token
    assert links.resolve(link_a.token).link.artifact_id == "job-a"  # type: ignore[union-attr]
    assert links.resolve(link_b.token).link.artifact_id == "job-b"  # type: ignore[union-attr]
    store.close()


# ==================================================================================
# 13-14: idempotência
# ==================================================================================


def test_reissuing_for_the_same_still_valid_artifact_reuses_the_token(tmp_path: Path) -> None:
    """13: política escolhida para v1 — reusar uma capability ainda
    válida em vez de gerar uma nova. Um retry de entrega do Discord nunca
    muda o link que o usuário já recebeu.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)

    first = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)
    second = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)
    third = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)

    assert first.token == second.token == third.token
    rows = store.execute_returning(
        "SELECT count(*) FROM report_links WHERE artifact_id = ?", [artifact_id]
    )
    assert rows[0][0] == 1  # uma única linha, não três
    store.close()


def test_reissuing_after_expiry_produces_a_new_capability(tmp_path: Path) -> None:
    """14: depois que a capability anterior expira, uma nova emissão gera
    um token NOVO — nunca reusa um expirado.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    issued_at = now_utc_naive()

    first = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=issued_at, ttl_days=1.0
    )
    later = issued_at + timedelta(days=2)
    second = issue_report_link(
        links, data_dir=tmp_path, artifact_id=artifact_id, now=later, ttl_days=1.0
    )

    assert first.token != second.token
    assert links.resolve(first.token, now=later).status is ReportLinkStatus.EXPIRED
    assert links.resolve(second.token, now=later).status is ReportLinkStatus.FOUND
    store.close()


# ==================================================================================
# 15: concorrência
# ==================================================================================


def test_concurrent_issue_for_the_same_artifact_produces_a_single_token(tmp_path: Path) -> None:
    """15: duas emissões concorrentes do MESMO artifact nunca produzem
    duas capabilities diferentes — `ReportLinkStore._lock` protege a
    decisão "reusar ou criar" inteira, não só cada instrução SQL
    individual (que o lock do próprio Store já serializa sozinho, mas
    isso não basta para esta decisão de duas etapas).
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)

    results: list[ReportLink] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def _go() -> None:
        barrier.wait()
        link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)
        with lock:
            results.append(link)

    threads = [threading.Thread(target=_go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    tokens = {r.token for r in results}
    assert len(tokens) == 1
    rows = store.execute_returning(
        "SELECT count(*) FROM report_links WHERE artifact_id = ?", [artifact_id]
    )
    assert rows[0][0] == 1
    store.close()


# ==================================================================================
# 16-17: token collision
# ==================================================================================


def test_token_collision_retries_and_succeeds(tmp_path: Path) -> None:
    """16: um gerador determinístico força uma colisão na primeira
    tentativa (mesmo token que já existe) e sucede na segunda.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    a = _make_artifact(tmp_path, "job-a")
    b = _make_artifact(tmp_path, "job-b")

    # Um primeiro link real "toma" o token "colliding-token".
    issue_report_link(
        links, data_dir=tmp_path, artifact_id=a, token_factory=lambda: "colliding-token"
    )

    tokens = iter(["colliding-token", "colliding-token", "finally-unique-token"])
    second = issue_report_link(
        links, data_dir=tmp_path, artifact_id=b, token_factory=lambda: next(tokens)
    )

    assert second.token == "finally-unique-token"
    store.close()


def test_token_collision_exhaustion_raises_explicitly(tmp_path: Path) -> None:
    """17: colisões até esgotar as tentativas -> erro explícito, nunca um
    loop sem fim, nunca um token duplicado silenciosamente aceito.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    a = _make_artifact(tmp_path, "job-a")
    b = _make_artifact(tmp_path, "job-b")

    issue_report_link(links, data_dir=tmp_path, artifact_id=a, token_factory=lambda: "always-same")

    with pytest.raises(ReportLinkTokenExhaustedError) as exc_info:
        issue_report_link(
            links, data_dir=tmp_path, artifact_id=b, token_factory=lambda: "always-same"
        )

    # A mensagem de erro nunca inclui um token completo — só o artifact_id.
    assert "always-same" not in str(exc_info.value)
    assert "job-b" in str(exc_info.value)
    # Nenhuma linha lixo foi deixada para trás.
    rows = store.execute_returning("SELECT count(*) FROM report_links WHERE artifact_id = ?", [b])
    assert rows[0][0] == 0
    store.close()


# ==================================================================================
# 18-21: artifact safety
# ==================================================================================


@pytest.mark.parametrize("evil", ["../secret", "../../etc/passwd", "a/b", "a\\b", "", "x" * 65])
def test_traversal_and_malformed_artifact_ids_are_rejected(tmp_path: Path, evil: str) -> None:
    """18/19/20: reusa a MESMA validação de `report_store.report_path_for`
    — nenhuma regex divergente nova. `issue_report_link` nunca chega a
    tocar o banco para um artifact_id inválido.
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)

    with pytest.raises(ReportPersistenceError):
        issue_report_link(links, data_dir=tmp_path, artifact_id=evil)

    assert store.execute_returning("SELECT count(*) FROM report_links")[0][0] == 0
    store.close()


def test_nonexistent_artifact_is_explicitly_rejected(tmp_path: Path) -> None:
    """21: artifact_id bem formado, mas nenhum .html correspondente
    existe — comportamento explícito e testado, tipo de erro distinto de
    "formato inválido".
    """
    store = _store(tmp_path)
    links = ReportLinkStore(store)

    with pytest.raises(ReportArtifactNotFoundError):
        issue_report_link(links, data_dir=tmp_path, artifact_id="never-persisted")

    assert store.execute_returning("SELECT count(*) FROM report_links")[0][0] == 0
    store.close()


# ==================================================================================
# 22-23: purge
# ==================================================================================


def test_purge_removes_only_expired_links(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    expired_artifact = _make_artifact(tmp_path, "expired-job")
    valid_artifact = _make_artifact(tmp_path, "valid-job")
    issued_at = now_utc_naive()

    expired = issue_report_link(
        links, data_dir=tmp_path, artifact_id=expired_artifact, now=issued_at, ttl_days=1.0
    )
    valid = issue_report_link(
        links, data_dir=tmp_path, artifact_id=valid_artifact, now=issued_at, ttl_days=30.0
    )
    later = issued_at + timedelta(days=2)

    purged = links.purge_expired(now=later)

    assert purged == 1
    assert links.resolve(expired.token, now=later).status is ReportLinkStatus.NOT_FOUND
    assert links.resolve(valid.token, now=later).status is ReportLinkStatus.FOUND
    store.close()


def test_purge_leaves_valid_links_untouched_when_nothing_expired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    links = ReportLinkStore(store)
    artifact_id = _make_artifact(tmp_path)
    link = issue_report_link(links, data_dir=tmp_path, artifact_id=artifact_id)

    purged = links.purge_expired()

    assert purged == 0
    assert links.resolve(link.token).status is ReportLinkStatus.FOUND
    store.close()


# ==================================================================================
# 24-25: upgrade additive do warehouse
# ==================================================================================


def test_report_links_table_is_added_without_touching_existing_data(tmp_path: Path) -> None:
    """24/25: simula um warehouse que já tinha estado ANTES de
    `report_links` existir (uma tabela do próprio `Store`, populada), e
    prova que construir `ReportLinkStore` pela primeira vez é
    puramente aditivo — `CREATE TABLE IF NOT EXISTS`, nunca DROP, nunca
    rebuild.
    """
    from botgitgud.domain.models import RunManifest

    store = _store(tmp_path)
    # Estado "pré-CL.2": uma tabela que já existia no warehouse real.
    store.write_run(
        RunManifest(
            cohort_id="pre-existing-cohort",
            code_version="pre-cl2",
            generated_at=now_utc_naive(),
            n_members=10,
            wcl_partition=3,
            settings_hash="h",
        )
    )
    tables_before = {
        r[0] for r in store.execute_returning("SELECT table_name FROM information_schema.tables")
    }
    assert "report_links" not in tables_before  # confirma o estado "antigo"

    # A "migração": primeira vez que ReportLinkStore é construído contra
    # este warehouse.
    ReportLinkStore(store)

    tables_after = {
        r[0] for r in store.execute_returning("SELECT table_name FROM information_schema.tables")
    }
    assert "report_links" in tables_after
    # Nada do que já existia sumiu ou mudou.
    assert tables_before <= tables_after
    runs = store.execute_returning("SELECT cohort_id, n_members FROM runs")
    assert runs == [("pre-existing-cohort", 10)]
    assert store.execute_returning("SELECT count(*) FROM report_links")[0][0] == 0
    store.close()


# ==================================================================================
# 26: token nunca em log estruturado
# ==================================================================================


def test_token_never_appears_in_structured_logs(tmp_path: Path) -> None:
    """26: captura os eventos estruturados emitidos por emissão + colisão
    + resolução + purge (mesmo padrão de test_benchmark_job.py's
    test_lifecycle_telemetry_is_emitted — DropEvent depois de capturar,
    para nunca alcançar o PrintLogger real) e confirma que o token
    COMPLETO nunca aparece — só o fingerprint truncado ou o artifact_id.
    """
    import structlog

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
        store = _store(tmp_path)
        links = ReportLinkStore(store)
        a = _make_artifact(tmp_path, "job-a")
        b = _make_artifact(tmp_path, "job-b")

        # Tokens deliberadamente do MESMO comprimento de um
        # secrets.token_urlsafe(32) real (43 chars) — um token curto
        # demais faria o fingerprint de 8 chars "truncar" para o token
        # inteiro por acidente, mascarando o que este teste precisa provar.
        colliding_token = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        second_token = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        issue_report_link(
            links, data_dir=tmp_path, artifact_id=a, token_factory=lambda: colliding_token
        )
        link_b = issue_report_link(
            links,
            data_dir=tmp_path,
            artifact_id=b,
            token_factory=iter([colliding_token, second_token]).__next__,
        )
        links.resolve(link_b.token)
        links.purge_expired(now=now_utc_naive() + timedelta(days=40))
        store.close()
    finally:
        structlog.reset_defaults()

    assert events  # de fato emitiu alguma coisa
    full_tokens = {colliding_token, second_token}
    for event in events:
        rendered = repr(event)
        for token in full_tokens:
            assert token not in rendered, f"token completo {token!r} vazou para o log: {event}"


# ==================================================================================
# 27: nenhuma URL/hostname hardcoded
# ==================================================================================


def test_module_never_hardcodes_a_url_or_hostname() -> None:
    """27: CL.2 não conhece host/URL/rota HTTP nenhuma — só a capability
    persistida. Varre o código-fonte REAL (docstrings/comentários fora:
    eles descrevem em prosa o que este módulo deliberadamente NÃO sabe,
    o que naturalmente cita esses termos sem ser um valor hardcoded).
    """
    import inspect
    import re

    import botgitgud.bot.report_links as mod

    src = inspect.getsource(mod)
    without_docstrings = re.sub(r'"""[\s\S]*?"""', "", src)
    without_comments = re.sub(r"#.*", "", without_docstrings)
    for forbidden in ("http://", "https://", "duckdns", "/r/"):
        assert forbidden not in without_comments.lower(), forbidden


# ==================================================================================
# schema / DDL
# ==================================================================================


def test_create_table_statement_defines_token_as_primary_key() -> None:
    assert "token VARCHAR PRIMARY KEY" in CREATE_REPORT_LINKS_TABLE
