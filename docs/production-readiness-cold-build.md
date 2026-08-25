# Production Readiness — cold cohort builds

## Evidência e decisão

O RC smoke real do Discord passou. Uma análise com coorte quente consumiu 4
queries WCL; a construção fria consumiu 1.504 queries em aproximadamente 4
minutos, acrescentando 100 logs e 100 candidatos. Esta implementação não
repete esse smoke e não faz chamadas reais.

| operação | queries no smoke real |
|---|---:|
| fetch_player_damage_events | 701 |
| fetch_player_events | 195 |
| fetch_player_resource_events | 101 |
| fetch_player_meta | 101 |
| fetch_report_rankings | 100 |
| fetch_player_percentile | 100 |
| fetch_player_debuffs | 100 |
| fetch_player_buffs | 100 |
| fetch_rankings_page | 4 |
| fetch_zone_partitions | 2 |

O custo é estrutural: cerca de 100 referências requerem eventos específicos
por jogador. Tamanho da coorte, features, matching, grading e findings não
foram reduzidos.

## Budget preflight e proteção do hot path

O modelo conservador centralizado em `Settings` usa:

- `cold_build_fixed_queries=6`;
- `cold_build_queries_per_reference=15`;
- `cohort_max=100`;
- `cold_build_points_per_query=2.0`;
- `cold_build_safety_margin=250` pontos;
- `hot_path_reserve=1000` pontos.

Assim, o limite conservador padrão é `(6 + 100 × 15) × 2 = 3.012` pontos.
O preflight força um snapshot de budget antes da primeira query de construção
e só permite o cold build quando:

`available_api_points - estimated_api_points >= max(api_points_floor, hot_path_reserve) + safety_margin`

O piso protegido padrão é 1.000 e a margem é 250. Ausência de snapshot de
budget falha fechada. O agendador continua tratando `analyze` e
`build_cohort` separadamente: uma análise hot acima do piso permanece
elegível mesmo quando o custo projetado adia um cold build.

## Semântica de defer e single-flight

`CohortDeferredBudget` representa `COHORT_DEFERRED_BUDGET`; é diferente de
`CohortNotReady` e `InsufficientCohort`. O job termina de forma controlada,
sem relatório parcial e sem retry infinito, e o usuário recebe: “Essa análise
precisa preparar uma nova coorte e o orçamento da Warcraft Logs está
temporariamente reservado. Tente novamente mais tarde.”

Cold builds do processo usam single-flight pela identidade canônica
`CohortCriteria.cohort_id()`. Pedidos da mesma coorte aguardam o líder e
reutilizam o pool persistido; IDs diferentes permanecem isolados. Falha libera
o flight e permite tentativa posterior. A fila do Discord continua serial:
hot jobs têm prioridade, mas um hot job enfileirado depois de um cold build já
iniciado aguarda o job corrente. Essa limitação é preferível a introduzir
concorrência de API perigosa.

## Reduções seguras

`fetch_report_rankings` foi removido apenas do caminho de referência. A
partition já conhecida no `CohortCriteria` é propagada para cada referência;
o log analisado continua consultando sua própria informação. Isso preserva
partition, difficulty e identidade dos dois caminhos e elimina cerca de 100
queries por construção.

O page size de casts passou de 5.000 para 10.000, igual aos demais streams de
eventos. A paginação continua guiada por `nextPageTimestamp`; teste de replay
prova que o conjunto semântico completo de eventos é idêntico. Economia
esperada no workload medido: cerca de 95 queries.

A otimização de percentile foi classificada `DEFERRED_OPTIMIZATION`. O cache
atual persiste um único `PlayerLog`, sem estados separados reference-ready e
analysis-ready; um cache hit com `percentile=None` poderia ser devolvido como
log analisado incompleto. As 100 queries são mantidas. Não houve redesign do
cache nem mudança analítica.

## Sharing

**Fight-wide sharing is not a material optimization for ranking-based cold
cohort construction in the measured workload: 100 candidates came from 98
distinct report/fight pairs.** Havia 98 reports, 96 fights com um jogador e
dois fights com dois jogadores; economia potencial aproximada de 2 queries,
ou 0,1%. Status: `NO_ACTION — measured benefit ~0.1%`.

O collector experimental tem muitos jogadores por fight, portanto amortiza
queries fight-wide. A coorte fria baseada em ranking distribui top players
entre reports/fights. São workloads distintos. O sharing existente foi
preservado; nenhuma infraestrutura nova foi criada.

## Prewarm e coortes quentes

`build-cohort` passa pelo mesmo preflight. Uso operacional recomendado:

```text
python -m botgitgud.cli ops-status --data-dir data
python -m botgitgud.cli build-cohort --encounter 3179 --class Warlock --spec Demonology --difficulty 5 --duration-bucket 240
python -m botgitgud.cli ops-status --data-dir data
```

`ops-status` lista coortes READY com cohort_id, class/spec, encounter,
difficulty, partition, duração, número de membros e atualização. Um bucket
READY não volta a baixar seus logs. Exit 75 significa defer temporário; o
operador deve aguardar recuperação do budget e executar novamente. Nenhum
batch real foi executado nesta tarefa.

## D-34 e ops snapshot

O processo dono do DuckDB publica `data/ops-snapshot.json` por escrita atômica
(`tmp` + replace), schema version 2 e timestamp Unix. `ops-status` prefere um
snapshot fresco e não abre DuckDB enquanto o bot está publicando estado. O
snapshot inclui:

- worker alive, freshness e PID;
- queued/running/done/failed;
- active job e latest completed job;
- delivery status e booleano de existência do report, sem expor path;
- último snapshot de budget WCL já consultado pelo dono;
- coortes READY/known e dimensões operacionais;
- último lifecycle de cold build: preflight, allowed/deferred, build_started,
  build_completed ou build_failed, cohort_id, custo, budget, piso e motivo.

Nenhum token ou Authorization header é registrado.

## Benchmark offline

O benchmark é um replay aritmético determinístico do mix medido, não uma nova
medição real. Execute com:

```text
python -m botgitgud.analysis.cold_build_benchmark
```

| série | total |
|---|---:|
| REAL SMOKE BASELINE | 1.504 |
| OFFLINE BEFORE | 1.504 |
| OFFLINE AFTER | 1.309 |

O AFTER aplica somente `fetch_report_rankings: 100 → 0` e
`fetch_player_events: 195 → 100`; demais operações ficam iguais. Redução
offline: 12,97%. Percentile e sharing não entram como economia.

## Equivalência e limitações

As mudanças alteram somente origem de partition já conhecida e tamanho de
página. Cohort membership, duração, partition, difficulty, PlayerLog,
features, alignment, grading, findings, Top 3 e relatório continuam usando os
mesmos dados. Testes end-to-end do pipeline e goldens verificam o contrato.

Limitações restantes: a fila é serial; o modelo é deliberadamente
conservador e pode adiar builds que caberiam por cache parcial; o percentile
de referência não foi otimizado; o snapshot é observabilidade por arquivo,
não monitoring histórico; e o destino permanente de backup continua decisão
operacional humana. Prewarm deve ser planejado fora do caminho interativo.

---

# Correção de budget semantics — a política era matematicamente impossível

Descoberto após `5b612de`, ao tentar aplicar a recomendação operacional de prewarm.

## A. Por que um cold build completo não cabe numa janela horária

A política original exigia que **todo** o custo estimado coubesse antes de começar:

```
available − estimated ≥ max(api_floor, hot_reserve) + margin
3600      − 3012      ≥ 1000 + 250
588                   ≥ 1250        → false, para qualquer available
```

Reorganizando: iniciar exigia **4262** pontos numa conta cujo teto é **3600**. Nenhum cold build
podia ser autorizado — nunca, em nenhum nível de orçamento — e o sintoma aparecia apenas como um
`COHORT_DEFERRED_BUDGET` eterno, inclusive para `build-cohort`. A recomendação de prewarm era
inexequível.

Duas causas somadas:

1. **Cost model inflado.** O modelo assumia `2,0 pts/query`. O smoke real mediu **2.131,29 pontos
   para 1.508 queries = 1,413 pts/query** — superestimava em 42%.
2. **Semântica all-or-nothing.** Mesmo com o custo correto (~2.128 pts), somar piso + margem
   excede 3600. **A hipótese B da investigação é a correta:** o build precisa ser resumível por
   janela de orçamento, não caber inteiro numa hora.

## B. Interactive cold vs prewarm

Duas políticas, deliberadamente diferentes:

| | protected_floor | margem |
|---|---|---|
| `INTERACTIVE` (via `!analisar`) | `max(api_floor, hot_reserve)` | `cold_build_safety_margin` (250) |
| `PREWARM` (via `build-cohort`) | `api_floor` apenas | `cold_build_batch_safety_margin` (100) |

O modo interativo mantém **hot path > cold convenience**: um build frio completo é adiado em vez de
comer a reserva dos outros usuários. O prewarm é o operador gastando budget de propósito — ainda
respeita o piso da API, mas não precisa preservar a reserva interativa, e era justamente essa
reserva que o tornava impossível.

O cost model virou uma **banda derivada**: `estimated_upper_bound = esperado × cold_build_cost_uncertainty`
(1,2×). Derivada, não uma segunda constante solta — sobrescrever o custo esperado escala o limite
superior junto, e os dois nunca divergem em silêncio. A decisão usa o limite superior; o preflight
não finge precisão que não tem.

## C. Checkpoint e resume

`affordable_references()` responde "quantas referências cabem **agora** sem furar piso + margem".
O prewarm processa esse tanto, e o restante fica para a janela seguinte.

O resume **não refaz trabalho**: `fetch_many` consulta o cache de logs antes de qualquer rede, então
referências concluídas numa janela anterior não voltam a custar pontos. O checkpoint é o próprio
cache de logs individuais — não há formato novo a manter.

**Parcial nunca é READY.** O pool (`write_candidate_pool`) só é escrito quando todas as referências
planejadas estão em cache. Enquanto faltar uma, o bucket retorna `CohortState.DEFERRED_BUDGET` e a
análise interativa continua não encontrando coorte pronta — nunca usa pool incompleto como se
estivesse completo.

## D. Simulação (limitPerHour = 3600)

| available | hot analysis | interactive cold (100 refs) | prewarm: refs que cabem |
|---|---|---|---|
| 3600 | ALLOW | DEFER | **97** |
| 3000 | ALLOW | DEFER | **74** |
| 2000 | ALLOW | DEFER | **34** |
| 1500 | ALLOW | DEFER | **15** |
| 1100 | ALLOW | DEFER | 0 |

Nenhuma linha viola `api_points_floor`. O caminho quente nunca é bloqueado por um cold build.
Um build de 100 referências leva ~2 janelas horárias de prewarm — o que é a realidade do custo,
não uma limitação artificial.

## E. Política impossível é diagnosticada, não adiada

`validate_cold_build_policy()` roda **dentro do preflight**, depois do refresh (que é quem revela o
teto da conta). Se nem o menor incremento possível cabe em `limitPerHour`, levanta
`ImpossibleColdBuildPolicy` com a conta explícita, em vez de devolver defer para sempre.

Nota: quando `available == limitPerHour`, "adiado agora" e "impossível por configuração" coincidem
matematicamente — a distinção só existe com orçamento parcialmente gasto.

## F. Quando executar prewarm

Fora de horário de pico, com `build-cohort`. Se o comando parar por orçamento, **repita o mesmo
comando após o reset**: ele continua de onde parou, sem refetch. Acompanhe por `ops-status`, que lê
o snapshot do processo dono (D-34) e não exige derrubar o bot.
