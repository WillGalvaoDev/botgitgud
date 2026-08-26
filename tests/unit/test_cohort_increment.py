"""B1/B3 — o build frio precisa ser incremental, checkpointado e resumível.

Nada aqui toca a rede: o "fetcher" é um duplo que escreve no Store real e
debita de um orçamento controlado, exatamente como a rede faria. É isso que
permite simular três janelas horárias sem esperar por nenhuma.

O caso central é o que a conta real impõe: 100 referências não cabem numa
janela de 3.600 pontos, e mesmo assim a coorte precisa ficar pronta.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from botgitgud.analysis.cohort_increment import (
    CohortState,
    DeferReason,
    advance_cohort_build,
)
from botgitgud.analysis.cold_build import (
    ColdBuildExecution,
    ColdBuildMode,
    ImpossibleColdBuildPolicy,
    affordable_references,
    points_per_reference_upper,
    validate_cold_build_policy,
)
from botgitgud.config import Settings
from botgitgud.domain.models import (
    CohortCriteria,
    FightRef,
    PlayerBuild,
    PlayerLog,
    RankingCandidate,
)
from botgitgud.errors import RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogRequest
from botgitgud.ingest.store import Store

LIMIT_PER_HOUR = 3600.0
PARTITION = 3
ENCOUNTER_ID = 3183
DURATION_S = 500.0


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "discord_token": "d" * 10,
        "wcl_client_id": "id",
        "wcl_client_secret": "secret",
        "blizzard_client_id": "id2",
        "blizzard_client_secret": "secret2",
        "max_workers": 1,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type, call-arg]


def _criteria() -> CohortCriteria:
    return CohortCriteria(
        encounter_id=ENCOUNTER_ID,
        difficulty=5,
        partition=PARTITION,
        class_name="Warlock",
        spec_name="Demonology",
        metric="dps",
        duration_min_s=490.0,
        duration_max_s=515.0,
    )


def _candidates(n: int) -> list[RankingCandidate]:
    return [
        RankingCandidate(
            report_code=f"R{i:015d}"[:16],
            fight_id=1,
            player_name=f"Ref{i}",
            duration_s=DURATION_S,
        )
        for i in range(n)
    ]


def _player_log(candidate: RankingCandidate) -> PlayerLog:
    return PlayerLog(
        fight=FightRef(
            report_code=candidate.report_code,
            fight_id=candidate.fight_id,
            encounter_id=ENCOUNTER_ID,
            boss_name="Fixture",
            difficulty=5,
            duration_s=candidate.duration_s,
            kill=True,
            partition=PARTITION,
        ),
        build=PlayerBuild(
            character_name=candidate.player_name,
            server=None,
            class_name="Warlock",
            spec_name="Demonology",
            role="dps",
            item_level=283.0,
            talent_hash="t",
            tier_pieces=4,
        ),
        dps=100_000.0,
        percentile=None,
        cast_timeline={},
    )


class _Budget:
    """Orçamento controlado pelo teste — nenhuma chamada à WCL."""

    def __init__(
        self, points: float, *, limit: float = LIMIT_PER_HOUR, reset_in: float | None = 1800.0
    ) -> None:
        self.points_remaining = points
        self.points_limit = limit
        self.points_reset_in = reset_in
        self.refreshes = 0

    def refresh_budget(self) -> None:
        self.refreshes += 1


class _Fetcher:
    """Escreve no Store e debita do orçamento, como a rede faria.

    Nunca é consultado para referências já em cache: quem decide isso é o
    próprio motor, e é justamente essa propriedade que o teste de retomada
    verifica.
    """

    def __init__(
        self,
        store: Store,
        budget: _Budget,
        *,
        points_per_ref: float,
        floor: float = 1000.0,
        unfetchable: frozenset[str] = frozenset(),
    ) -> None:
        self._store = store
        self._budget = budget
        self._points_per_ref = points_per_ref
        self._floor = floor
        self._unfetchable = unfetchable
        self.fetched: list[str] = []

    def fetch_many(
        self,
        refs: list[LogRequest],
        *,
        max_workers: int,
        expected_partition: int | None = None,
    ) -> list[PlayerLog]:
        out: list[PlayerLog] = []
        for ref in refs:
            if self._store.read_log(ref.report_code, ref.fight_id, ref.player) is not None:
                continue
            if ref.player in self._unfetchable:
                continue
            if self._budget.points_remaining - self._points_per_ref < self._floor:
                # Backstop do proprio cliente: o piso da API e inviolavel.
                raise RateLimitBudgetExceeded(
                    "orçamento abaixo do piso",
                    points_remaining=self._budget.points_remaining,
                    reset_in_seconds=60.0,
                )
            self._budget.points_remaining -= self._points_per_ref
            candidate = RankingCandidate(ref.report_code, ref.fight_id, ref.player, DURATION_S)
            player_log = _player_log(candidate)
            self._store.write_log(player_log)
            self.fetched.append(ref.player)
            out.append(player_log)
        return out


def _advance(
    store: Store,
    fetcher: _Fetcher,
    budget: _Budget,
    settings: Settings,
    candidates: list[RankingCandidate],
    *,
    mode: ColdBuildMode = ColdBuildMode.INTERACTIVE,
    cohort_id: str = "cohort-x",
) -> object:
    return advance_cohort_build(
        client=budget,
        fetcher=fetcher,  # type: ignore[arg-type]
        store=store,
        settings=settings,
        cohort_id=cohort_id,
        criteria=_criteria(),
        candidates=candidates,
        partition=PARTITION,
        mode=mode,
    )


def _budget_for(settings: Settings, references: int, *, mode: ColdBuildMode) -> float:
    """Orçamento que compra exatamente `references` pelo custo modelado."""
    floor = max(settings.api_points_floor, settings.hot_path_reserve)
    if mode is ColdBuildMode.PREWARM:
        floor = settings.api_points_floor
        margin = settings.cold_build_batch_safety_margin
    else:
        margin = settings.cold_build_safety_margin
    fixed = (
        settings.cold_build_fixed_queries
        * settings.cold_build_queries_uncertainty
        * settings.cold_build_points_per_query
        * settings.cold_build_cost_uncertainty
    )
    return floor + margin + fixed + references * points_per_reference_upper(settings)


# -- B1: one-shot impossível, incremental válido ------------------------------------


def test_full_one_shot_cohort_is_diagnosed_as_impossible_on_a_real_account() -> None:
    """Falharia contra a8617b6: lá a validação só olhava o incremento mínimo do
    prewarm, então a política interativa one-shot (3.804 pontos num teto de
    3.600) passava em silêncio e virava um defer eterno.
    """
    settings = _settings()
    with pytest.raises(ImpossibleColdBuildPolicy, match="impossivel"):
        validate_cold_build_policy(
            settings,
            LIMIT_PER_HOUR,
            mode=ColdBuildMode.INTERACTIVE,
            execution=ColdBuildExecution.ONE_SHOT,
            references=settings.cohort_max,
        )


def test_the_same_cohort_is_valid_as_a_resumable_incremental_build() -> None:
    settings = _settings()
    validate_cold_build_policy(
        settings,
        LIMIT_PER_HOUR,
        mode=ColdBuildMode.INTERACTIVE,
        execution=ColdBuildExecution.RESUMABLE_INCREMENTAL,
    )  # nao levanta


def test_interactive_makes_real_progress_at_the_account_ceiling() -> None:
    """O sistema não pode entrar em "defer permanente com zero progresso"."""
    affordable = affordable_references(
        _settings(), LIMIT_PER_HOUR, mode=ColdBuildMode.INTERACTIVE, planned=100
    )
    assert affordable > 0


def test_interactive_window_never_needs_all_hundred_references_at_once(tmp_path: Path) -> None:
    settings = _settings()
    store = Store(tmp_path / "data")
    budget = _Budget(LIMIT_PER_HOUR)
    fetcher = _Fetcher(store, budget, points_per_ref=points_per_reference_upper(settings))
    candidates = _candidates(100)

    increment = _advance(store, fetcher, budget, settings, candidates)

    assert increment.state is CohortState.DEFERRED_BUDGET  # type: ignore[attr-defined]
    assert increment.completed > 0  # type: ignore[attr-defined]
    assert increment.completed < 100  # type: ignore[attr-defined]
    # Parcial NUNCA e READY.
    assert store.read_candidate_pool("cohort-x") is None
    assert budget.points_remaining >= settings.api_points_floor
    store.close()


# -- 100 referências em três janelas, sem refetch -----------------------------------


def test_hundred_references_complete_across_three_windows_with_zero_refetch(
    tmp_path: Path,
) -> None:
    """O cenário exigido pelo gate: 30 + 40 + 30 = 100, e as referências já
    concluídas nunca voltam a custar um único ponto.
    """
    settings = _settings()
    store = Store(tmp_path / "data")
    candidates = _candidates(100)
    per_ref = points_per_reference_upper(settings)
    progress: list[int] = []

    for window_refs in (30, 40, 30):
        budget = _Budget(_budget_for(settings, window_refs, mode=ColdBuildMode.INTERACTIVE))
        fetcher = _Fetcher(store, budget, points_per_ref=per_ref)
        increment = _advance(store, fetcher, budget, settings, candidates)
        progress.append(increment.completed)  # type: ignore[attr-defined]
        # Cada janela busca SOMENTE o que ainda falta.
        assert len(fetcher.fetched) == window_refs
        assert len(set(fetcher.fetched)) == len(fetcher.fetched)
        assert budget.points_remaining >= settings.api_points_floor

    assert progress == [30, 70, 100]
    assert increment.state is CohortState.READY  # type: ignore[attr-defined]
    pool = store.read_candidate_pool("cohort-x")
    assert pool is not None
    assert len(pool) == 100
    store.close()


def test_a_resumed_window_spends_nothing_on_already_cached_references(tmp_path: Path) -> None:
    settings = _settings()
    store = Store(tmp_path / "data")
    candidates = _candidates(20)

    first_budget = _Budget(_budget_for(settings, 12, mode=ColdBuildMode.INTERACTIVE))
    first = _Fetcher(store, first_budget, points_per_ref=points_per_reference_upper(settings))
    _advance(store, first, first_budget, settings, candidates)
    assert len(first.fetched) == 12

    second_budget = _Budget(_budget_for(settings, 50, mode=ColdBuildMode.INTERACTIVE))
    spent_before = second_budget.points_remaining
    second = _Fetcher(store, second_budget, points_per_ref=points_per_reference_upper(settings))
    increment = _advance(store, second, second_budget, settings, candidates)

    assert increment.state is CohortState.READY  # type: ignore[attr-defined]
    # Somente as 8 restantes: nenhuma das 12 anteriores reaparece.
    assert sorted(second.fetched) == sorted(f"Ref{i}" for i in range(12, 20))
    spent = spent_before - second_budget.points_remaining
    assert spent == pytest.approx(8 * points_per_reference_upper(settings))
    store.close()


# -- guarda em tempo de execução ------------------------------------------------------


@pytest.mark.parametrize(
    ("workload", "real_points_per_ref"),
    [
        ("short_light", 12.0),
        ("medium_measured", 33.0),  # 864 queries / 37 refs * 1,413 pts/query
        ("long_paginated", 80.0),  # bem acima do modelo: o guarda precisa segurar
    ],
)
def test_runtime_guard_protects_the_floor_even_when_a_reference_costs_more_than_modelled(
    tmp_path: Path, workload: str, real_points_per_ref: float
) -> None:
    """O preflight não basta. Se o custo real por referência superar o modelo,
    é a remedição a cada lote que impede furar o piso — não a estimativa
    inicial.
    """
    settings = _settings()
    store = Store(tmp_path / f"data-{workload}")
    budget = _Budget(LIMIT_PER_HOUR)
    fetcher = _Fetcher(store, budget, points_per_ref=real_points_per_ref)

    _advance(store, fetcher, budget, settings, _candidates(100))

    assert budget.points_remaining >= settings.api_points_floor
    store.close()


def test_the_budget_is_remeasured_between_batches_not_only_at_preflight(
    tmp_path: Path,
) -> None:
    settings = _settings(cold_build_chunk_references=5)
    store = Store(tmp_path / "data")
    budget = _Budget(_budget_for(settings, 20, mode=ColdBuildMode.INTERACTIVE))
    fetcher = _Fetcher(store, budget, points_per_ref=points_per_reference_upper(settings))

    increment = _advance(store, fetcher, budget, settings, _candidates(60))

    # 20 referencias em lotes de 5 => 4 lotes, cada um precedido de uma medicao.
    assert increment.batches == 4  # type: ignore[attr-defined]
    assert budget.refreshes >= increment.batches  # type: ignore[attr-defined]
    store.close()


def test_a_batch_that_makes_no_progress_stops_instead_of_looping_forever(
    tmp_path: Path,
) -> None:
    settings = _settings()
    store = Store(tmp_path / "data")
    budget = _Budget(LIMIT_PER_HOUR * 100)  # orcamento de sobra: nao e budget
    candidates = _candidates(10)
    fetcher = _Fetcher(
        store,
        budget,
        points_per_ref=1.0,
        unfetchable=frozenset(f"Ref{i}" for i in range(10)),
    )

    increment = _advance(store, fetcher, budget, settings, candidates)

    assert increment.state is CohortState.DEFERRED_BUDGET  # type: ignore[attr-defined]
    assert increment.defer_reason is DeferReason.NO_PROGRESS  # type: ignore[attr-defined]
    assert store.read_candidate_pool("cohort-x") is None
    store.close()


# -- single-flight -------------------------------------------------------------------


def test_two_waiters_on_the_same_cohort_produce_exactly_one_builder(tmp_path: Path) -> None:
    """Dois jogadores diferentes que precisam da MESMA coorte não podem
    disparar duas construções — a API seria paga duas vezes pelo mesmo pool.
    """
    from botgitgud.analysis.cold_build import CohortSingleFlight

    settings = _settings()
    store = Store(tmp_path / "data")
    budget = _Budget(_budget_for(settings, 40, mode=ColdBuildMode.INTERACTIVE))
    fetcher = _Fetcher(store, budget, points_per_ref=points_per_reference_upper(settings))
    candidates = _candidates(10)
    flights = CohortSingleFlight()
    builds = 0
    lock = threading.Lock()
    pools: list[int] = []
    barrier = threading.Barrier(2)

    def waiter() -> None:
        nonlocal builds
        barrier.wait()
        with flights.acquire("cohort-x") as leader:
            if store.read_candidate_pool("cohort-x") is None and leader:
                with lock:
                    builds += 1
                _advance(store, fetcher, budget, settings, candidates)
        pool = store.read_candidate_pool("cohort-x")
        with lock:
            pools.append(-1 if pool is None else len(pool))

    threads = [threading.Thread(target=waiter) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert builds == 1
    # Os dois seguem com o MESMO pool, nenhum refetch para o segundo.
    assert pools == [10, 10]
    assert len(fetcher.fetched) == 10
    store.close()


def test_unfetchable_references_do_not_block_the_ones_that_still_work(
    tmp_path: Path,
) -> None:
    """Um lote inicial só de logs inservíveis não pode encerrar a janela: as
    referências boas que vinham depois continuam pagáveis.
    """
    settings = _settings(cold_build_chunk_references=5)
    store = Store(tmp_path / "data")
    budget = _Budget(_budget_for(settings, 60, mode=ColdBuildMode.INTERACTIVE))
    candidates = _candidates(20)
    fetcher = _Fetcher(
        store,
        budget,
        points_per_ref=points_per_reference_upper(settings),
        # Os cinco primeiros — exatamente o primeiro lote.
        unfetchable=frozenset(f"Ref{i}" for i in range(5)),
    )

    increment = _advance(store, fetcher, budget, settings, candidates)

    assert increment.state is CohortState.DEFERRED_BUDGET  # type: ignore[attr-defined]
    assert increment.defer_reason is DeferReason.NO_PROGRESS  # type: ignore[attr-defined]
    # As 15 boas foram buscadas apesar do primeiro lote inteiro ter falhado.
    assert sorted(fetcher.fetched) == sorted(f"Ref{i}" for i in range(5, 20))
    # E nenhuma referencia inservivel foi tentada duas vezes na mesma janela.
    assert len(fetcher.fetched) == len(set(fetcher.fetched))
    store.close()
