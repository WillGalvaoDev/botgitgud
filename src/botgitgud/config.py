"""T1.1 — typed configuration, the single source of truth for every
constant introduced across Fases 0-3.

See docs/desvios.md D-10: the T1.1 pseudocode in docs/implementacao.md
lists stale cohort defaults (cohort_min_hard=10, cohort_min_warn=30,
duration_tolerance_pct=0.07) that predate the T0.8 correction — T0.8's own
section explicitly revises these after measuring the real API
(docs/schema_confirmado.md §8) and is chronologically later in the same
document. The values below match what T0.6/T0.8 actually implemented and
tested, not the earlier draft.

Existing Fase 0 modules (analysis/*, wcl/client.py, blizzard/client.py)
keep their own local constants for now — rewiring every call site to read
from Settings is part of T1.6's restructuring, not this task. This class
exists so that migration has one place to land.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS: set[str] = {
    "discord_token",
    "wcl_client_id",
    "wcl_client_secret",
    "blizzard_client_id",
    "blizzard_client_secret",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # -- credentials (SecretStr: str(settings.x) never leaks the value) ---
    discord_token: SecretStr
    wcl_client_id: SecretStr
    wcl_client_secret: SecretStr
    blizzard_client_id: SecretStr
    blizzard_client_secret: SecretStr

    # -- general ------------------------------------------------------------
    data_dir: Path = Path("data")
    log_level: str = "INFO"
    log_json: bool = False
    # B5: trilha operacional duravel em JSON Lines sob `data_dir/logs`. Rotacao
    # por TAMANHO porque o volume depende da carga, nao do relogio — um dia
    # ocioso gera quase nada e um dia de prewarm gera muito. Teto de disco =
    # log_max_bytes * (log_backup_count + 1), ~60 MB nos defaults.
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5
    max_workers: int = 4

    # -- B6: supervisão externa ao processo `serve` --------------------------
    supervisor_poll_interval_s: float = 2.0
    supervisor_backoff_base_s: float = 2.0
    supervisor_backoff_max_s: float = 60.0
    # Um crash raro nao deve herdar o backoff acumulado de crashes antigos: se
    # o filho ficou de pe por mais que isto, o proximo restart volta ao
    # attempt 1 em vez de continuar escalando um backoff que ja nao reflete o
    # estado atual.
    supervisor_backoff_reset_after_s: float = 300.0
    # N falhas dentro da janela = crash-loop real, nao ma sorte pontual. O
    # supervisor para de tentar (nao consome CPU em loop) e exige intervencao.
    supervisor_storm_threshold: int = 5
    supervisor_storm_window_s: float = 600.0
    # Tempo dado ao bot para fechar sozinho (bot.close(), DuckDB liberado)
    # antes de a escalada para TerminateProcess ser considerada.
    supervisor_stop_grace_s: float = 30.0
    # Intervalo de polling do arquivo de stop request DENTRO do bot — separado
    # do polling do supervisor porque sao processos diferentes com relogios
    # de resposta independentes.
    bot_stop_poll_interval_s: float = 1.0

    # -- T0.3: WclClient ------------------------------------------------------
    wcl_connect_timeout_s: float = 5.0
    wcl_read_timeout_s: float = 30.0
    wcl_pool_timeout_s: float = 60.0
    wcl_max_attempts: int = 4
    wcl_backoff_base_s: float = 1.0
    wcl_backoff_factor: float = 2.0
    wcl_rate_limit_cache_ttl_s: float = 60.0
    api_points_floor: float = 1000.0
    hot_path_reserve: float = 1000.0
    cold_build_safety_margin: float = 250.0
    # Margem menor no modo prewarm: o operador esta gastando budget de
    # proposito, e o piso da API continua sendo respeitado.
    cold_build_batch_safety_margin: float = 100.0
    # Custo por query MEDIDO no smoke real de 2026-08-25: 2.131,29 pontos para
    # 1.508 queries = 1,413 pts/query. O valor antigo (2,0) superestimava em
    # 42% e era a metade da politica impossivel. `upper` mantem uma banda
    # conservadora para o preflight sem fingir precisao que nao existe.
    cold_build_points_per_query: float = 1.413
    # Banda derivada, nao uma segunda constante solta: sobrescrever o custo
    # esperado escala o limite superior junto, e os dois nunca divergem em
    # silencio. 1,2x cobre a variacao por tipo de query documentada em
    # docs/schema_confirmado.md sem fingir precisao que nao temos.
    cold_build_cost_uncertainty: float = 1.2
    # MEDIDO no prewarm real de 2026-08-25: 864 queries para 37 referencias =
    # 23,4 queries/referencia. O valor anterior (15) foi falsificado por essa
    # medicao — subestimava o trabalho real em ~36%, e um modelo que subestima
    # autoriza mais referencias do que o orcamento comporta.
    cold_build_queries_per_reference: float = 23.4
    # Banda de QUERIES, separada da banda de PONTOS: as duas variaveis erraram
    # em direcoes opostas no smoke real (queries/ref acima do modelo, pts/query
    # abaixo), entao misturar as duas esconde os dois erros. Uma unica medicao
    # nao autoriza tratar 23,4 como verdade universal — a carga varia com
    # duracao, spec, pets e paginacao de damage events — e o objetivo declarado
    # do modelo e NUNCA superestimar quantas referencias cabem, nao acertar o
    # consumo exato.
    cold_build_queries_uncertainty: float = 1.3
    cold_build_fixed_queries: int = 6
    # Tamanho do lote entre duas reavaliacoes do orcamento real. O preflight
    # sozinho nao basta: se o custo por referencia superar o modelo, e este
    # lote que limita o quanto se pode passar do previsto antes da proxima
    # medicao. Mantenha o pior caso de um lote abaixo da margem de seguranca.
    cold_build_chunk_references: int = 5
    # Espera padrao antes de reconsiderar um job adiado por orcamento quando a
    # WCL nao informou `pointsResetIn`. Nunca reenfileirar em loop apertado: o
    # orcamento so melhora com o reset da janela.
    cold_build_defer_retry_s: float = 600.0

    # -- EB.4: construção incremental do Encounter Benchmark -------------------
    # NENHUM valor abaixo foi medido ao vivo ainda (measured=false, provisório)
    # — ao contrário dos `cold_build_*` acima, que carregam a proveniência da
    # medição real nos próprios comentários. O workload é outro: setup-only
    # nunca baixa damage/casts/buffs/debuffs/resource events, então o modelo de
    # ~23,4 queries/referência da Execution Cohort NÃO se aplica aqui — seria
    # inventar precisão que não existe. Unidade é FIGHT, não referência: uma
    # única `report.rankings` (rankPercent+partition) + uma única
    # `QUERY_PLAYER_SETUP_ONLY` (combatantInfo) servem TODOS os candidatos
    # daquele fight, fight-wide.
    benchmark_build_points_per_query: float = 2.0
    benchmark_build_queries_per_fight: float = 2.0
    # Banda mais larga que `cold_build_cost_uncertainty` (1.2) de propósito:
    # sem medição real nenhuma para ancorar a estimativa, um build de
    # prioridade baixa deve superestimar o próprio custo, não subestimar.
    benchmark_build_cost_uncertainty: float = 1.5
    benchmark_build_fixed_queries: int = 2  # partition/1a página de rankings na descoberta
    # Reavaliado a cada lote (guarda em tempo de execução, não só preflight) —
    # mesmo papel de cold_build_chunk_references.
    benchmark_build_chunk_fights: int = 5
    # Piso EXTRA além de api_points_floor — deliberadamente mais conservador
    # que qualquer ColdBuildMode existente (nem PREWARM, nem INTERACTIVE):
    # benchmark é a prioridade mais baixa do sistema, nunca deve competir por
    # orçamento com um `!analisar` real nem com um prewarm operacional.
    benchmark_build_safety_margin: float = 250.0
    benchmark_build_defer_retry_s: float = 600.0

    # -- T0.4: BlizzardClient -------------------------------------------------
    blizzard_connect_timeout_s: float = 5.0
    blizzard_read_timeout_s: float = 10.0
    blizzard_max_attempts: int = 3
    blizzard_backoff_base_s: float = 1.0
    blizzard_backoff_factor: float = 2.0

    # -- T0.5: alignment ------------------------------------------------------
    gap_penalty_s: float = 25.0

    # -- T0.6: cadence classification ------------------------------------------
    major_threshold_s: float = 90.0
    min_eligible_interval_s: float = 15.0
    min_eligible_presence: float = 0.70
    single_use_n_threshold: float = 1.5

    # -- T0.7: report rendering ------------------------------------------------
    # T2.3 removed the fixed green/yellow delta thresholds this section used to
    # hold (achado 3.6) — grading is quantile-relative now (analysis/grading.py),
    # not a Settings-tunable absolute value.

    # -- T0.8: cohort duration bands and size thresholds -----------------------
    # Values as measured/revised against the live API (see module docstring).
    sanity_band_pct: float = 0.35
    positional_band_pct: float = 0.12
    positional_min_n: int = 8
    cohort_min_hard: int = 8
    cohort_min_warn: int = 20
    cohort_max: int = 100
    max_ranking_pages: int = 10

    def settings_hash(self) -> str:
        """T1.5: content hash of every non-credential field — feeds
        RunManifest.settings_hash so a report can be traced back to the
        analysis parameters that produced it. Credential fields are
        excluded on purpose: they must never be hashed into anything that
        ends up in a rendered report or a DuckDB row.
        """
        data = self.model_dump(mode="json", exclude=_SECRET_FIELDS)
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:12]
