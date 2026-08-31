"""COMPLETE ANALYSIS INTEGRATION PROOF — Setup e Execution coexistindo numa
única análise real, offline.

Não basta provar `setup_analysis is not None`: a pergunta é se uma análise
com benchmark disponível produz, ao mesmo tempo, findings das CINCO
categorias de Setup E a análise de Execution completa, e se as duas chegam
juntas ao `ReportContract`. Cada cenário abaixo roda o caminho equivalente
ao `!analisar` real (`run_analysis` + `render_analysis`), nunca uma
simulação das camadas.

Zero WCL real, zero Discord: `_DispatchTransport` (test_pipeline.py) para o
pipeline, `FakeWclBackend` (tests/fixtures) para o build de benchmark. Os
dois compartilham o MESMO `Store` em disco, que é como o benchmark
construído por um job fica visível para a análise seguinte.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from fake_wcl_backend import FakePlayer, FakeWclBackend
from test_log_fetcher import _meta_response
from test_pipeline import (
    N_REFS,
    _build_deps,
    _DispatchTransport,
    _happy_path_responses,
    _req,
)

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import build_encounter_benchmark
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.pipeline import AnalysisResult, Deps, run_analysis
from botgitgud.analysis.setup_finding import (
    FindingCategory,
    ObservationCode,
    Publicability,
)
from botgitgud.bot.benchmark_trigger import maybe_enqueue_benchmark_build
from botgitgud.bot.job_models import BudgetStatus
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.worker import run_claimed_job
from botgitgud.domain.models import (
    FightRef,
    GearPiece,
    PlayerBuild,
    PlayerLog,
    SetupProfile,
    TalentNode,
)
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.report.render import render_analysis
from botgitgud.report.setup_text import render_setup_section

ALL_CATEGORIES = frozenset(
    {
        FindingCategory.TALENT_BUILD,
        FindingCategory.TRINKET,
        FindingCategory.TRINKET_PAIR,
        FindingCategory.SET_BONUS,
        FindingCategory.SECONDARY_STATS,
    }
)

# O setup do jogador analisado, rico o bastante para as cinco categorias:
# talentos, dois trinkets (slots 12/13), peças de set (setID não-nulo) e
# stats secundários. O fixture compartilhado de test_pipeline.py tem gear
# sem `id`, que `extract_setup_profile` (EB.0) descarta de propósito — logo
# não serve aqui.
PLAYER_COMBATANT_INFO: dict[str, Any] = {
    "talentTree": [{"id": 1, "rank": 1, "nodeID": 100}, {"id": 2, "rank": 1, "nodeID": 101}],
    "gear": [
        {"slot": 0, "id": 7001, "itemLevel": 283.0, "setID": 1989},
        {"slot": 1, "id": 7002, "itemLevel": 283.0, "setID": 1989},
        {"slot": 2, "id": 7003, "itemLevel": 283.0, "setID": 1989},
        {"slot": 3, "id": 7004, "itemLevel": 283.0, "setID": 1989},
        {"slot": 12, "id": 5000, "itemLevel": 283.0, "setID": None},
        {"slot": 13, "id": 5001, "itemLevel": 283.0, "setID": None},
    ],
    "stats": {
        "Haste": {"min": 1000.0},
        "Crit": {"min": 800.0},
        "Mastery": {"min": 1200.0},
        "Versatility": {"min": 400.0},
    },
}


# -- fixtures do pipeline ------------------------------------------------------------


def _responses_with_rich_setup() -> dict[str, Any]:
    """`_happy_path_responses` com o `combatantInfo` do jogador analisado
    substituído pelo rico acima — as referências ficam como estavam, para
    o matching de Execution não mudar em nada.
    """
    responses = _happy_path_responses()
    responses["meta"] = list(responses["meta"])
    responses["meta"][0] = _meta_response(combatant_info=PLAYER_COMBATANT_INFO)
    return responses


def _analyze(deps: Deps) -> AnalysisResult:
    return run_analysis(_req(), deps, allow_cold_build=True)


def _categories(result: AnalysisResult) -> set[FindingCategory]:
    assert result.setup_analysis is not None
    return {f.category for f in result.setup_analysis.findings}


def _visible_categories(result: AnalysisResult) -> set[FindingCategory]:
    assert result.setup_analysis is not None
    return {
        f.category
        for f in result.setup_analysis.findings
        if f.publicability is not Publicability.HIDDEN
    }


# -- benchmark sintético READY --------------------------------------------------------


def _observation(
    target: EncounterBenchmarkTarget,
    *,
    n: int,
    percentile: float,
    talents: tuple[tuple[int, int], ...],
    trinkets: tuple[int, ...],
    set_ids: tuple[int, ...],
    stats: dict[str, float],
) -> PlayerLog:
    gear = [
        GearPiece(slot=slot, item_id=item_id, item_level=283.0, set_id=None)
        for slot, item_id in zip((12, 13), trinkets, strict=False)
    ]
    gear.extend(
        GearPiece(slot=i, item_id=7001 + i, item_level=283.0, set_id=set_id)
        for i, set_id in enumerate(set_ids)
    )
    return PlayerLog(
        fight=FightRef(
            report_code=f"BENCH{n:011d}",
            fight_id=1,
            encounter_id=target.encounter_id,
            boss_name="Fallen-King Salhadaar",
            difficulty=target.difficulty,
            duration_s=300.0,
            kill=True,
            partition=target.partition,
        ),
        build=PlayerBuild(
            character_name=f"Bench{n}",
            server="Azralon",
            class_name=target.spec.class_name,
            spec_name=target.spec.spec_name,
            role="dps",
            item_level=283.0,
            talent_hash=None,
            tier_pieces=len(set_ids),
            setup=SetupProfile(
                talents=tuple(TalentNode(node_id=nid, rank=r, spell_id=None) for nid, r in talents),
                gear=tuple(gear),
                stats=stats,
            ),
        ),
        dps=900_000.0,
        percentile=percentile,
        cast_timeline={},
    )


def _synthetic_benchmark_observations(
    target: EncounterBenchmarkTarget, *, include_set_bonus: bool = True, n: int = 24
) -> list[PlayerLog]:
    """População observada com dado suficiente para as cinco categorias —
    variada de propósito (nem todos usam a mesma build/trinkets), para as
    prevalências serem reais e não degeneradas.
    """
    observations: list[PlayerLog] = []
    for i in range(n):
        common = i % 3 != 0  # 2/3 da população compartilha o padrão comum
        observations.append(
            _observation(
                target,
                n=i,
                percentile=95.0 - (i % 4) * 20.0,
                talents=((100, 1), (101, 1)) if common else ((100, 1), (102, 1)),
                trinkets=(5000, 5001) if common else (5002, 5003),
                set_ids=(1989,) * (4 if common else 2) if include_set_bonus else (),
                stats={
                    "Haste": 1000.0 + i,
                    "Crit": 800.0 + i,
                    "Mastery": 1200.0 + i,
                    "Versatility": 400.0 + i,
                },
            )
        )
    return observations


def _seed_ready_benchmark(
    deps: Deps, target: EncounterBenchmarkTarget, *, include_set_bonus: bool = True
) -> None:
    policy = BenchmarkPolicy.default()
    observations = _synthetic_benchmark_observations(target, include_set_bonus=include_set_bonus)
    benchmark = build_encounter_benchmark(observations, target=target, policy=policy)
    BenchmarkStore(deps.store).write_benchmark(benchmark, policy=policy, observations=observations)


def _target_of(result: AnalysisResult) -> EncounterBenchmarkTarget:
    assert result.benchmark_target is not None
    return result.benchmark_target


# ==================================================================================
# CENÁRIO A — BENCHMARK READY
# ==================================================================================


def test_scenario_a_setup_and_execution_coexist_in_one_analysis(tmp_path: Path) -> None:
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))

    # 1ª passada só para descobrir o target que o pipeline resolve (mesma
    # identidade spec/encounter/difficulty/partition que ele vai consultar).
    first = _analyze(deps)
    _seed_ready_benchmark(deps, _target_of(first))

    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = _analyze(deps2)

    # -- SETUP: as cinco categorias, presentes E publicáveis
    assert result.setup_analysis is not None
    assert result.setup_analysis.benchmark_available is True
    assert _categories(result) == ALL_CATEGORIES
    visible = _visible_categories(result)
    assert FindingCategory.TALENT_BUILD in visible
    assert FindingCategory.TRINKET in visible
    assert FindingCategory.TRINKET_PAIR in visible
    assert FindingCategory.SET_BONUS in visible
    assert FindingCategory.SECONDARY_STATS in visible

    # -- EXECUTION: rodou normalmente, na MESMA análise
    assert result.comparisons
    assert result.performance is not None
    assert result.dps_gap is not None
    assert result.matched_cohort_members == N_REFS
    assert result.header.matched_covariates


def test_scenario_a_report_contract_carries_all_five_sections(tmp_path: Path) -> None:
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    _seed_ready_benchmark(deps, _target_of(first))

    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = _analyze(deps2)
    rendered = render_analysis(result)
    contract = rendered.contract

    assert contract.resultado.char_name == "Zarad"  # 1. RESULTADO
    assert contract.setup is not None  # 2. SETUP
    assert {f.category for f in contract.setup.findings} == ALL_CATEGORIES
    assert contract.execucao.comparisons  # 3. EXECUÇÃO
    assert contract.execucao.performance is not None
    assert contract.execucao.dps_gap is not None
    assert contract.confianca.matched_cohort_members == N_REFS  # 5. CONFIANÇA/AMOSTRA
    assert contract.confianca.reference_pool_members is not None

    # 4. TOP 3 — execution-only, sempre
    from botgitgud.analysis.findings import Finding

    assert all(isinstance(a, Finding) for a in contract.top_actions)
    setup_ids = {id(f) for f in contract.setup.findings}
    assert not any(id(a) in setup_ids for a in contract.top_actions)

    # SETUP aparece no relatório de texto; a mensagem inline continua sem ele
    assert render_setup_section(contract.setup)
    assert "SETUP" not in rendered.summary


# ==================================================================================
# CENÁRIO B — BENCHMARK MISSING
# ==================================================================================


def test_scenario_b_missing_benchmark_degrades_honestly_and_queues_a_build(
    tmp_path: Path,
) -> None:
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    queue = JobQueue(deps.store)

    result = _analyze(deps)

    # Execution completa, apesar de não haver benchmark nenhum
    assert result.comparisons
    assert result.performance is not None
    assert result.dps_gap is not None

    # Setup degrada honestamente: nada visível, nenhum score, nenhum ganho
    assert result.setup_analysis is not None
    assert result.setup_analysis.benchmark_available is False
    assert _visible_categories(result) == set()
    assert all(
        f.observation is ObservationCode.MISSING_DATA for f in result.setup_analysis.findings
    )
    assert not hasattr(result.setup_analysis, "score")
    assert all(not hasattr(f, "estimated_gain_pct") for f in result.setup_analysis.findings)

    # O relatório continua entregável, e sem seção SETUP inventada
    rendered = render_analysis(result)
    assert "<html" in rendered.html
    assert rendered.summary
    assert render_setup_section(rendered.contract.setup) == []

    # E um benchmark_build foi enfileirado — sem a análise ter esperado nada
    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=result, source="test")
    assert outcome is not None
    assert outcome.created
    queued = [j for j in queue.list_recent() if j.job_type == "benchmark_build"]
    assert len(queued) == 1
    assert queued[0].status == "queued"


# ==================================================================================
# CENÁRIO C — BUILD -> READY -> NEXT ANALYSIS
# ==================================================================================


def _benchmark_backend_for(candidates: list[dict[str, Any]]) -> FakeWclBackend:
    """Um backend que conhece exatamente os candidatos do payload do job —
    com dois trinkets, peças de set e stats, para o benchmark construído
    cobrir as cinco categorias.
    """
    backend = FakeWclBackend()
    by_fight: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for c in candidates:
        by_fight.setdefault((c["report_code"], c["fight_id"]), []).append(c)
    for (report_code, fight_id), members in by_fight.items():
        players = []
        for i, c in enumerate(members):
            common = i % 3 != 0
            players.append(
                FakePlayer(
                    name=c["player_name"],
                    rank_percent=95.0 - (i % 4) * 20.0,
                    talents=((100, 1), (101, 1)) if common else ((100, 1), (102, 1)),
                    trinket_id=5000 if common else 5002,
                    trinket_id_2=5001 if common else 5003,
                    set_ids=(1989,) * (4 if common else 2),
                    stats=(
                        ("Crit", 800.0 + i),
                        ("Mastery", 1200.0 + i),
                        ("Versatility", 400.0 + i),
                    ),
                )
            )
        backend.add_fight(report_code, fight_id, tuple(players))
    return backend


class _BackendClient:
    def __init__(self, backend: FakeWclBackend) -> None:
        self._backend = backend
        self.points_remaining = 1_000_000.0
        self.points_limit = 1_000_000.0
        self.points_reset_in: float | None = None

    def query(self, query: str, variables: dict[str, object], *, op_name: str) -> dict[str, object]:
        return self._backend.query_fn(query, variables, op_name=op_name)

    def refresh_budget(self) -> None:
        return None


def test_scenario_c_build_then_ready_then_next_analysis_is_complete(tmp_path: Path) -> None:
    """Sem NENHUMA ação manual intermediária: análise 1 (sem benchmark) ->
    job enfileirado -> worker constrói -> análise 2 encontra o benchmark
    sozinha e devolve Setup completo + Execution.
    """
    import json

    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    queue = JobQueue(deps.store)

    # 1/2/3 — primeira análise, benchmark ausente, job enfileirado
    first = _analyze(deps)
    assert first.setup_analysis is not None
    assert first.setup_analysis.benchmark_available is False
    maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=first, source="test")

    job = queue.claim_next(BudgetStatus(points_remaining=1_000_000.0, limit_per_hour=1_000_000.0))
    assert job is not None
    assert job.job_type == "benchmark_build"

    # 4/5 — o job roda pelo worker REAL, contra um backend offline que
    # conhece exatamente os candidatos que o gatilho enfileirou.
    payload = json.loads(job.payload_json or "{}")
    backend = _benchmark_backend_for(payload["candidates"])
    client = _BackendClient(backend)
    build_deps = Deps(
        client=client,  # type: ignore[arg-type]
        fetcher=LogFetcher(client, deps.store, deps.catalog),  # type: ignore[arg-type]
        store=deps.store,
        catalog=deps.catalog,
        settings=deps.settings,
    )
    outcome = run_claimed_job(queue, job, build_deps)
    assert outcome.ok, outcome.message

    stored = BenchmarkStore(deps.store).read_benchmark(_target_of(first).benchmark_id)
    assert stored is not None  # READY, persistido

    # 6/7/8/9/10 — nova análise para o MESMO target encontra tudo sozinha
    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    second = _analyze(deps2)

    assert second.setup_analysis is not None
    assert second.setup_analysis.benchmark_available is True
    assert _categories(second) == ALL_CATEGORIES
    assert second.comparisons  # Execution também roda
    assert second.dps_gap is not None

    contract = render_analysis(second).contract
    assert contract.setup is not None
    assert {f.category for f in contract.setup.findings} == ALL_CATEGORIES
    assert contract.execucao.comparisons


# ==================================================================================
# CENÁRIO D — PARTIAL CATEGORY
# ==================================================================================


def test_scenario_d_one_unavailable_category_does_not_destroy_setup(tmp_path: Path) -> None:
    """Benchmark READY, mas sem NENHUM dado de set bonus. As outras
    categorias continuam funcionando e a Execution não é afetada.
    """
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    _seed_ready_benchmark(deps, _target_of(first), include_set_bonus=False)

    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = _analyze(deps2)

    assert result.setup_analysis is not None
    by_category = {f.category: f for f in result.setup_analysis.findings}
    assert set(by_category) == ALL_CATEGORIES  # nenhuma categoria some da lista

    # O jogador TEM peças de set; a população do benchmark não tem nenhuma.
    # Isso é ausência de observação, nunca um veredito — e nunca uma
    # exceção: antes desta rodada um `assert` em `compare_set_bonus`
    # derrubava a análise inteira exatamente aqui.
    set_finding = by_category[FindingCategory.SET_BONUS]
    assert set_finding.observation is ObservationCode.LOW_PREVALENCE
    assert set_finding.prevalence is not None
    assert set_finding.prevalence.count == 0

    for category in (
        FindingCategory.TALENT_BUILD,
        FindingCategory.TRINKET,
        FindingCategory.TRINKET_PAIR,
        FindingCategory.SECONDARY_STATS,
    ):
        assert by_category[category].observation is not ObservationCode.MISSING_DATA
        assert by_category[category].publicability is not Publicability.HIDDEN

    # Execution intacta
    assert result.comparisons
    assert result.dps_gap is not None


# ==================================================================================
# CENÁRIO E — HONESTIDADE
# ==================================================================================

_FORBIDDEN_WORDS = (
    "bad",
    "wrong",
    "worse",
    "optimal",
    "best",
    "replace",
    "upgrade",
)


def test_scenario_e_setup_text_never_makes_a_causal_claim(tmp_path: Path) -> None:
    """Setup pouco observado: permitido dizer "pouco observado nesta
    amostra"; proibido qualquer veredito ou ganho de DPS.
    """
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    target = _target_of(first)

    # População que NÃO usa o setup do jogador — prevalência baixa/zero.
    policy = BenchmarkPolicy.default()
    observations = [
        _observation(
            target,
            n=i,
            percentile=95.0 - (i % 4) * 20.0,
            talents=((200, 1), (201, 1)),
            trinkets=(6000, 6001),
            set_ids=(1989, 1989),
            stats={"Haste": 3000.0 + i, "Crit": 100.0 + i},
        )
        for i in range(24)
    ]
    benchmark = build_encounter_benchmark(observations, target=target, policy=policy)
    BenchmarkStore(deps.store).write_benchmark(benchmark, policy=policy, observations=observations)

    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = _analyze(deps2)
    assert result.setup_analysis is not None
    observations_seen = {f.observation for f in result.setup_analysis.findings}
    assert ObservationCode.LOW_PREVALENCE in observations_seen

    rendered = render_analysis(result)
    section = "\n".join(render_setup_section(rendered.contract.setup)).lower()
    assert section  # há de fato uma seção para inspecionar
    for word in _FORBIDDEN_WORDS:
        assert word not in section, word
    assert "% dps" not in section
    assert "ganho" not in section


def test_setup_never_produces_a_score_or_a_gain(tmp_path: Path) -> None:
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    _seed_ready_benchmark(deps, _target_of(first))
    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = _analyze(deps2)

    setup = result.setup_analysis
    assert setup is not None
    assert not hasattr(setup, "score")
    assert not hasattr(setup, "setup_score")
    for finding in setup.findings:
        assert not hasattr(finding, "estimated_gain_pct")
        assert not hasattr(finding, "score")


# ==================================================================================
# SEPARAÇÃO SETUP / EXECUTION
# ==================================================================================


def test_setup_never_influences_execution_cohort_or_grading(tmp_path: Path) -> None:
    """A mesma análise, com e sem benchmark: tudo que é Execution sai
    idêntico. Setup entra no relatório sem tocar em coorte nem em nota.
    """
    deps_without = _build_deps(tmp_path / "a", _DispatchTransport(_responses_with_rich_setup()))
    without = _analyze(deps_without)

    deps_with = _build_deps(tmp_path / "b", _DispatchTransport(_responses_with_rich_setup()))
    seed = _analyze(deps_with)
    _seed_ready_benchmark(deps_with, _target_of(seed))
    deps_with2 = _build_deps(tmp_path / "b", _DispatchTransport(_responses_with_rich_setup()))
    with_benchmark = _analyze(deps_with2)

    assert with_benchmark.setup_analysis is not None
    assert with_benchmark.setup_analysis.benchmark_available is True
    assert without.setup_analysis is not None
    assert without.setup_analysis.benchmark_available is False

    assert with_benchmark.matched_cohort_members == without.matched_cohort_members
    assert with_benchmark.reference_pool_members == without.reference_pool_members
    assert with_benchmark.header.matched_covariates == without.header.matched_covariates
    assert with_benchmark.header.relaxed_covariates == without.header.relaxed_covariates
    assert with_benchmark.comparisons == without.comparisons
    assert with_benchmark.top_actions == without.top_actions
    assert with_benchmark.dps_gap == without.dps_gap


def test_talent_build_does_not_filter_the_encounter_benchmark(tmp_path: Path) -> None:
    """O benchmark é a agregação da POPULAÇÃO — o mesmo objeto, não importa
    qual jogador o está lendo. Provado diretamente: um benchmark construído
    de uma população fixa é idêntico independentemente do build do
    jogador, porque `build_encounter_benchmark` não recebe jogador algum.
    """
    import inspect

    signature = inspect.signature(build_encounter_benchmark)
    assert set(signature.parameters) == {"observations", "target", "policy"}

    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    target = _target_of(first)
    observations = _synthetic_benchmark_observations(target)
    policy = BenchmarkPolicy.default()

    a = build_encounter_benchmark(observations, target=target, policy=policy)
    b = build_encounter_benchmark(copy.deepcopy(observations), target=target, policy=policy)
    assert a == b


def test_production_matching_is_still_v1_and_talent_cluster_still_applies(
    tmp_path: Path,
) -> None:
    """MATCHING V2: NÃO ativado nesta rodada — limitação aceita e
    documentada. Este teste é a prova de que continua assim, e falha se
    alguém ativar v2 sem passar por essa decisão.
    """
    from botgitgud.analysis.cohort_match import DEGRADATION_ORDER
    from botgitgud.domain.models import DEFAULT_MATCHING_POLICY_VERSION, CohortCriteria

    assert DEFAULT_MATCHING_POLICY_VERSION == "v1"
    assert "talent_cluster" in DEGRADATION_ORDER

    criteria = CohortCriteria(
        encounter_id=3179,
        difficulty=5,
        partition=3,
        class_name="Warlock",
        spec_name="Demonology",
        metric="dps",
        duration_min_s=280.0,
        duration_max_s=320.0,
    )
    assert criteria.matching_policy_version == "v1"

    # e o pipeline continua sem passar outra coisa
    import ast
    import inspect

    import botgitgud.analysis.pipeline as pipeline_module

    tree = ast.parse(inspect.getsource(pipeline_module))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CohortCriteria"
        ):
            assert all(kw.arg != "matching_policy_version" for kw in node.keywords)


def test_setup_analysis_costs_no_extra_wcl_query(tmp_path: Path) -> None:
    """Setup Analysis não custa nada a mais na análise: o benchmark vem do
    Store (SQL local) e `analyze_setup` é puro — nenhum dos módulos SA
    conhece cliente, fetcher ou query.
    """
    import ast
    import inspect

    import botgitgud.analysis.setup_analysis as sa
    import botgitgud.analysis.setup_setbonus as sb
    import botgitgud.analysis.setup_stats as ss
    import botgitgud.analysis.setup_talents as st
    import botgitgud.analysis.setup_trinkets as sti

    for module in (sa, st, sti, sb, ss):
        tree = ast.parse(inspect.getsource(module))
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            m.startswith(("botgitgud.wcl", "botgitgud.ingest", "botgitgud.bot")) for m in modules
        ), (module.__name__, modules)

    # e o pipeline lê o benchmark do Store, nunca da rede
    import botgitgud.analysis.pipeline as pipeline_module

    tree = ast.parse(inspect.getsource(pipeline_module))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_analysis"
    )
    reads = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "read_benchmark"
    ]
    assert len(reads) == 1

    # execução de verdade: a análise com benchmark presente continua
    # produzindo o mesmo relatório de execução (nenhuma query extra
    # observável no comportamento).
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    _seed_ready_benchmark(deps, _target_of(first))
    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    second = _analyze(deps2)
    assert second.setup_analysis is not None
    assert second.setup_analysis.benchmark_available is True
    assert second.comparisons == first.comparisons


# -- ainda: nada de Phase 4 mudou ---------------------------------------------------


def test_phase4_is_untouched_by_setup_analysis(tmp_path: Path) -> None:
    from botgitgud.phase4.resolver import ResolutionStatus

    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = _analyze(deps)
    _seed_ready_benchmark(deps, _target_of(first))
    deps2 = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = _analyze(deps2)

    assert result.phase4_resolution.status is ResolutionStatus.UNAVAILABLE
