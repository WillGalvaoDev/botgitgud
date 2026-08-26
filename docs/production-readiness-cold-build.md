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

---

# Safety gate pré-prewarm — dois bugs abortaram o primeiro prewarm real

O primeiro prewarm real foi abortado **antes de qualquer chamada WCL**, na revisão do gate
operacional. Alvo candidato: `Warlock/Demonology`, encounter 3183, difficulty 5, bucket 493,312,
`cohort_id=380ec0ee516f8f8a` — NOT READY, 0 candidatos, 3 logs já cacheados. **WCL calls = 0.**

## INCIDENT

**Bug 1 — `DEFERRED_BUDGET` retornava sucesso.** `build_cohorts` podia devolver um
`BucketBuildResult` com `state=DEFERRED_BUDGET`, mas `_cmd_build_cohort` seguia pelo retorno
normal: **exit 0** e a mensagem *"N coorte(s) construída(s)"*. Trabalho incompleto por orçamento
era reportado como conclusão — um operador (ou script) não teria como saber que a coorte não estava
pronta nem que precisava retomar.

**Bug 2 — o processo batch não publicava ops-snapshot.** Durante o prewarm, quem detém o lock do
DuckDB é o próprio `build-cohort`. Como ele não chamava `write_snapshot`, `ops-status` continuava
lendo o último snapshot do bot — obsoleto — e não enxergava `mode=prewarm`, `cohort_id`, progresso,
decisão de orçamento nem lifecycle. Isso contradizia a D-34, cuja decisão é que **observabilidade
passa pelo processo dono do warehouse**.

## FIX

### Exit semantics

| Estado agregado | Exit |
|---|---|
| Todos os buckets `READY` | **0** |
| Algum `DEFERRED_BUDGET`, nenhum hard failure | **75** (`EX_TEMPFAIL`) |
| Qualquer `FAILED` | **1** (hard failure domina) |
| Nenhum bucket elegível | **1** (nada foi preparado) |

`CohortDeferredBudget` e `RateLimitBudgetExceeded` continuam em 75. Saída de sucesso vai para
stdout; qualquer resultado incompleto vai para **stderr**.

### Mensagens

```
READY:     Cohort ready
             planned: 100 / completed: 100

DEFERRED:  Cohort prewarm deferred by WCL budget
             planned: 100 / completed: 43 / remaining: 57
             resume: execute the same command after budget reset

FAILED:    Cohort prewarm failed
             planned: 100 / completed: 0
```

A palavra "construída" deixou de existir em qualquer caminho parcial — há teste que falha se ela
reaparecer.

### Snapshot lifecycle

`ColdBuildPublisher` (em `bot/ops_snapshot.py`, **reusando** `write_snapshot` — não há segunda
implementação) publica desde antes do preflight até depois do desfecho:

`preflight` → `building` → `completed` | `deferred_budget` | `failed`

Um thread daemon reamostra o lifecycle a cada 1 s enquanto o build corre; o estado final é escrito
de forma **síncrona** na saída, então `ops-status` explica o último desfecho mesmo com o processo já
encerrado. Campos publicados: `mode=prewarm`, `cohort_id`, `stage`, `outcome`, e por bucket
`planned`/`completed`/`remaining`/`state`, mais `points_remaining`/`points_limit` quando conhecidos.
Nada é inventado — o que o processo não sabe simplesmente não aparece.

Escrita atômica preservada (tmp + `replace`); `ops-status` nunca lê JSON parcial. Falha de disco ao
publicar diagnóstico nunca derruba o prewarm.

### Multi-bucket

O snapshot lista os buckets individualmente e o agregado nunca reporta `ready` se algum estiver
adiado — a mesma regra do exit code.

### D-34 compliance

`ops-status` acompanha o prewarm **sem abrir o DuckDB externamente**: há teste que roda o publisher
num diretório onde o `warehouse.duckdb` sequer existe e ainda assim lê `mode=prewarm` e o
`cohort_id`.

## Contrato de READY (inalterado)

`completed < planned` → NOT READY · `completed == planned` → READY. O pool só é escrito quando tudo
está em cache.

---

# PREWARM REAL VALIDATION: PASS — e o bug de observabilidade que ele revelou

Primeiro prewarm real executado em 2026-08-25.

## Resultado

| | |
|---|---|
| Target | Warlock/Demonology · encounter 3183 · difficulty 5 · bucket solicitado 493,312 |
| Resolvido | `cohort_id=f91dffaf13ddc899` · partition 3 · bucket interno 127 · faixa 491–516 s |
| Progresso | planned **37** · completed **37** · remaining **0** |
| Estado | **READY** · exit **0** |
| Budget | 3599 → 2520,44 (limit 3600) |
| Consumo | **864 queries** · **1.078,56 pontos** · **1,248 pts/query** |
| Piso | nunca ameaçado (menor observado: 2520,44 contra piso 1000) |
| Logs | 940 → 977 (+37) |
| `fetch_report_rankings` nas referências | **0** — otimização confirmada em produção |

## O bug: `stage=preflight` durante toda a construção

Durante os ~3,5 minutos de construção real, `ops-status` mostrou **`stage=preflight`**. O snapshot
final ficou correto (`completed`) apenas porque o CLI sobrescrevia o valor no fim — ou seja, o
estado certo aparecia por acidente, não por desenho.

**Causa:** em `ColdBuildPublisher.publish()` a ordem era `cold.update(lifecycle)` seguido de
`cold.update(self._extra)`. O contexto **estático** do chamador era aplicado **depois** do lifecycle
vivo e o mascarava. Como o CLI havia posto `stage="preflight"` nesse contexto estático no início, o
`stage="building"` publicado pelo construtor real nunca chegava ao observador.

**Correção — estrutural, não um `if`:** os campos passaram a ter dono explícito.

- **Contexto estático** (`set_context`): modo, bucket solicitado, parâmetros do comando. Aplicado
  **antes** e **proibido** de conter campos de lifecycle — `set_context(stage=...)` levanta
  `ValueError`, com teste parametrizado sobre cada campo de `LIFECYCLE_OWNED_FIELDS`.
- **Lifecycle dinâmico** (`record`): `stage`, `outcome`, `cohort_id`, `planned`, `completed`,
  `remaining`, `buckets`. Passa pelo **mesmo canal** que o construtor usa
  (`record_cold_lifecycle`), então a ordem é cronológica por natureza e não há duas fontes
  competindo. Aplicado **por último**, logo sempre autoritativo.

O invariante agora tem regressão: `preflight → building → completed` (e as variantes `deferred_budget`
e `failed`) são observados em snapshots sucessivos, e o estado terminal sobrevive ao encerramento
do processo.

## Achado 1 — `cohort_id` depende da identidade canônica resolvida

O `cohort_id` esperado a priori (`380ec0ee516f8f8a`) diferiu do real (`f91dffaf13ddc899`). **Não é
bug.** A identidade inclui a partition e o bucket de duração resolvidos ao vivo — a partition muda
com o tempo (já documentado desde a T1.7), e o bucket interno 127 (491–516 s) é o que contém 493,312.

A identidade **não foi alterada** nesta correção. Há teste confirmando que a mesma identidade
canônica produz sempre o mesmo `cohort_id`, e que mudar qualquer dimensão relevante (class, spec,
encounter, difficulty, partition, faixa de duração) produz outro.

## Achado 2 — cost model mantido, apesar da divergência medida

Medição real: **862 queries / 37 refs ≈ 23,3 queries/ref**, contra os **15** do modelo. A diferença
vem de `damage_events`: 567 queries ≈ 15,3 páginas/ref, contra ~7 no build anterior — fights mais
longos (491–516 s vs ~300 s) e Demonology é pet-heavy.

Na direção oposta, `pts/query` real (**1,248**) ficou **abaixo** do esperado (1,413) e bem abaixo do
upper (1,6956). Os dois erros se compensaram: estimativa de ~2.128 pontos contra **1.079 reais** —
o preflight seguiu **conservador em pontos**, que é a dimensão que protege o piso.

**Nada foi recalibrado.** Não mudamos `15 queries/ref`, nem `1,413 pts/query`, nem o upper, e não
introduzimos modelo por spec ou por duração. Duas medições (uma de 100 refs, uma de 37) não bastam
para recalibrar sem risco de trocar um viés por outro. **Precisamos de mais prewarms reais antes de
mexer.**

---

# HOT-PATH smoke attempt #1: FAIL antes da análise

Tentativa de validar o caminho quente após o prewarm. **Não chegou a executar `!analisar`.**

Target preparado: Fiskowl · Warlock/Demonology · report `FhYZDLMbwBVAx4KX` fight 19 · encounter
3183 · difficulty 5 · partition 3 · duração 511,307 s · `cohort_id=f91dffaf13ddc899` (READY, 37
membros, 37/37 logs em cache).

## O que aconteceu

O gateway do Discord conectou, exatamente um worker iniciou, e `on_ready` tentou publicar o ops
snapshot. A serialização falhou:

```
TypeError: Object of type datetime is not JSON serializable
```

O primeiro tick do worker repetiu a falha, `worker_crashed` foi registrado, e o bot foi encerrado
**antes de qualquer análise**.

## Causa

`Store.list_ready_cohorts()` devolve `updated_at` como `datetime` — a coluna
`cohort_registry.updated_at` é `TIMESTAMP` e o DuckDB legitimamente entrega um objeto Python
tipado. Esse valor ia direto de `discord_bot._publish_snapshot` para `write_snapshot` e daí para
`json.dumps`, sem nenhuma fronteira de conversão.

**Duas falhas independentes se somaram:**

1. **Sem fronteira de serialização.** O snapshot montava o payload com objetos de domínio e
   entregava a `json.dumps` cru.
2. **Contenção estreita demais.** `_publish_snapshot` capturava apenas `OSError`, embora sua
   própria docstring declare o contrato: *"Best-effort por desenho: falhar ao escrever um arquivo
   de diagnóstico nunca pode derrubar o worker."* O `TypeError` escapou e matou a task.

Não houve contradição de contrato: a intenção documentada sempre foi **non-fatal**; a implementação
é que não a cumpria para essa classe de erro.

## Proteções que funcionaram

- **0 pontos WCL consumidos** — a falha foi no boot, antes de qualquer query.
- Coorte `f91dffaf13ddc899` permaneceu **READY**, com os 37 membros intactos.
- Cache de logs intacto.
- Nenhum relatório gerado, nenhuma entrega tentada, nenhum job órfão.

## Correção

**A. Fronteira JSON-safe.** `_json_safe()` normaliza o payload imediatamente antes de `json.dumps`:
`datetime`/`date` → ISO-8601 via `.isoformat()`, mapas e sequências recursivamente, escalares
inalterados. A responsabilidade de produzir JSON pertence a quem produz JSON — o `CohortRegistry`
não foi alterado e continua devolvendo objetos tipados.

**Fail closed, deliberadamente sem `default=str`.** Um tipo desconhecido levanta `TypeError`
nomeando o tipo **e o caminho exato** dentro do payload (`$.ready_cohorts[0].updated_at`). Com
`default=str`, qualquer tipo novo viraria uma string plausível e a regressão ficaria escondida — que
é como esse bug chegaria à produção de novo.

**Formato temporal:** reusa a convenção que o projeto já aplicava a `finished_at` e
`oldest_running_started_at` (`.isoformat()` puro). Timezone-aware preserva o offset
(`...+00:00`); naive permanece sem offset, coerente com `now_utc_naive()` (T1.8: todo datetime deste
domínio é naive-porém-UTC). Nenhum segundo formato foi introduzido.

**B. Contenção alinhada ao contrato.** `_publish_snapshot` passou a capturar
`(OSError, TypeError, ValueError)`, logando `ops_snapshot_write_failed` com o tipo do erro; o
`ColdBuildPublisher` recebeu o mesmo tratamento. Observabilidade nunca derruba o worker, e o tick
seguinte volta a publicar normalmente.

As duas propriedades têm testes separados, como devem: serializar corretamente e sobreviver a uma
falha futura de observabilidade são garantias distintas.

---

# B1/B2/B3 — o cold build interativo passa a ser resumível

Os três bloqueadores do readiness gate da v1.0 eram **um problema só**: o build frio no caminho
interativo não era operacionalmente resumível. Foram corrigidos juntos porque nenhum deles é
utilizável isolado — uma política possível sem retomada continua descartando o pedido, e uma
retomada sobre um modelo que subestima custo continua ameaçando o orçamento.

## B1 — a política interativa era matematicamente impossível

`validate_cold_build_policy` existia justamente para diagnosticar política impossível, mas avaliava
**apenas o incremento mínimo do prewarm** (uma referência). Com isso a política interativa passava
em silêncio:

| Item | Valor |
|---|---|
| Queries para 100 refs (modelo antigo) | 1.506 |
| Limite superior em pontos | 2.554 |
| Exigido para iniciar | 2.554 + piso 1.000 + margem 250 = **3.804** |
| Teto absoluto da conta | **3.600 pts/h** |

`allowed = False` com o orçamento **cheio**. Toda coorte fria era adiada para sempre, e o sintoma
chegava ao usuário como "tente novamente mais tarde" — indefinidamente.

**Causa real:** a pergunta estava errada. "As 100 referências cabem nesta janela?" não é
respondível com sim numa conta de 3.600 pontos, e nunca precisou ser: o prewarm já provou em
produção que 100 referências cabem em **várias** janelas.

**Correção.** `ColdBuildExecution` separa as duas perguntas:

- `RESUMABLE_INCREMENTAL` — basta que **uma** referência caiba no teto;
- `ONE_SHOT` — o trabalho **inteiro** precisa caber numa janela.

`validate_cold_build_policy` passou a receber modo, execução e número de referências, e
`preflight_cold_build` repassa os três. O default de `preflight_cold_build` é `ONE_SHOT`: um
chamador que não declara como pretende gastar recebe a verificação estrita, que falha alto em vez de
virar defer eterno. Os dois caminhos de produção declaram `RESUMABLE_INCREMENTAL` e preflightam
`references=1`.

## O algoritmo incremental, agora compartilhado

`analysis/cohort_increment.py` extrai o algoritmo que o prewarm já usava; **não** há uma segunda
implementação. `build_cohorts` (prewarm) e `run_analysis` (interativo/worker) chamam o mesmo
`advance_cohort_build`, diferindo apenas no `ColdBuildMode` — e portanto apenas no piso protegido e
na margem.

    pendentes = candidatos sem log em cache
    enquanto houver pendentes:
        orçamento  = client.refresh_budget()          # medição REAL, a cada lote
        affordable = affordable_references(orçamento, modo)
        se affordable == 0: adia
        lote = pendentes[: min(affordable, chunk, len(pendentes))]
        fetch_cohort_logs(lote)                       # escreve cada log no cache
        se o lote inteiro voltou sem nada novo: para  # não é orçamento: log inservível
    sobrou pendente? -> DEFERRED_BUDGET, pool NÃO escrito
    nada pendente?   -> write_candidate_pool -> READY

Três propriedades sustentam o desenho:

- **cache como checkpoint** — `fetch_many` consulta o Store antes de qualquer rede, então uma
  referência concluída numa janela anterior nunca volta a custar um ponto. Não há tabela de
  progresso: o progresso *é* o cache;
- **parcial nunca é READY** — o pool é o sinal de "coorte pronta", e só é escrito quando **todas** as
  referências planejadas estão em cache. O caminho interativo violava isto: escrevia o pool logo
  após a query de rankings, publicando uma coorte cujos membros ainda custariam ~1.500 queries;
- **guarda em tempo de execução** — o preflight autoriza, mas não protege. Se o custo real por
  referência superar o modelo, é a remedição a cada lote que encolhe o passo seguinte. O lote
  (`cold_build_chunk_references`, 5) é o intervalo entre duas medições, e é ele que limita o quanto
  o consumo pode passar do previsto antes da próxima decisão.

### Exemplo: 100 referências em três janelas

| Janela | Orçamento | Processadas | Acumulado | Estado | Refetch |
|---|---|---|---|---|---|
| 1 | ~2.810 pts | 30 | 30/100 | `deferred_budget` | — |
| 2 | ~3.325 pts | 40 | 70/100 | `deferred_budget` | **0** |
| 3 | ~2.810 pts | 30 | 100/100 | `ready` | **0** |

O pool só existe ao fim da janela 3. Coberto por
`test_cohort_increment.py::test_hundred_references_complete_across_three_windows_with_zero_refetch`.

### Limitação conhecida

Um lote cujas referências não podem ser buscadas (log removido da WCL, por exemplo) encerra a janela
com `defer_reason=no_progress` em vez de girar em laço. A coorte continua não-READY e será
retentada; se a referência for permanentemente inservível, o alvo não fecha sozinho. É o mesmo
comportamento de antes desta correção — não uma regressão — e segue registrado como pendência.

## B2 — um adiamento virava falha permanente

`CohortDeferredBudget` herda de `AnalysisError`, logo de `BotGitGudError`, e o worker tratava esse
ramo com `mark_failed`. O resultado contradizia a própria mensagem entregue ao usuário: o bot
prometia continuidade e descartava o pedido no mesmo instante — junto com o progresso já pago em
pontos de API. Só `RateLimitBudgetExceeded` chegava ao `requeue`.

**Correção.** `deferred_budget` é agora um estado de job com significado próprio:

| Estado | Significado |
|---|---|
| `failed` | não há expectativa de retentativa automática |
| `deferred_budget` | trabalho **válido** aguardando orçamento, com progresso preservado |

- `JobQueue.defer()` grava `status='deferred_budget'`, `deferred_until`, `defer_reason` e incrementa
  `defer_count`. `finished_at` continua vazio: o trabalho não terminou;
- `claim_next` reconsidera jobs adiados cujo `deferred_until` já passou — **o mesmo job**, com a
  mesma `dedup_key` e o mesmo `job_id`, sem criar nenhum job novo;
- `deferred_budget` conta como trabalho ativo: dedup, cota do usuário e `!status` o enxergam. Um
  pedido repetido enquanto adiado é deduplicado no job existente;
- a cláusula `except CohortDeferredBudget` **precede** `except BotGitGudError` no worker. A ordem é
  o próprio contrato;
- o seguidor do single-flight também adia. Antes levantava `CohortNotReady`, que caía no mesmo ramo
  genérico e falhava o job do segundo usuário.

Migração idempotente (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), como a de `delivery_status`.

### Quando o job volta

`pointsResetIn` já vem junto do `rateLimitData` que o cliente consulta, então saber a hora certa
**não custa nenhuma chamada extra**: `deferred_until = agora + pointsResetIn`. Sem esse dado, cai em
`cold_build_defer_retry_s` (600 s). Nunca há reenfileiramento imediato — o orçamento só melhora com
o reset da janela, e `claim_next` filtra por `deferred_until`, então não existe polling agressivo
nem laço de retentativa.

### O que o usuário vê

Na primeira tentativa fria, o enfileiramento avisa que a análise **continuará sozinha** e que **não
é preciso repetir o comando**. Adiamentos seguintes são silenciosos: repetir "ainda esperando" a
cada janela seria ruído. Quando a coorte fica pronta, o mesmo job termina a análise e entrega o
relatório normalmente, sem nenhuma ação do usuário.

## B3 — o modelo de custo subestimava o trabalho real

O prewarm real gastou **864 queries para 37 referências ≈ 23,4 q/ref**, contra as **15** modeladas.
Essa medição foi registrada e deliberadamente **não** recalibrada na época, porque duas medições não
bastavam. O que mudou a decisão foi o uso: um modelo que subestima **autoriza mais referências do
que o orçamento comporta** — exatamente o que um build incremental não pode fazer.

**Correção — queries e pontos passam a ser variáveis separadas, cada uma com banda própria.** Os
dois erros do smoke apontaram para lados opostos (queries/ref acima do modelo, pts/query abaixo);
uma banda única sobre o produto esconderia ambos.

| Constante | Antes | Depois | Origem |
|---|---|---|---|
| `cold_build_queries_per_reference` | 15 | **23,4** | medido: 864 q / 37 refs |
| `cold_build_queries_uncertainty` | — | **1,3** | banda de queries (nova) |
| `cold_build_points_per_query` | 1,413 | 1,413 | medido, inalterado |
| `cold_build_cost_uncertainty` | 1,2 | 1,2 | banda de pontos, inalterada |

23,4 **não** é tratado como verdade universal: é a única medição real, e a carga varia com duração,
spec, pets e paginação de `damage_events`. O fator 1,3 é a banda sobre essa medição. O objetivo
declarado do modelo é **nunca superestimar quantas referências cabem**, não acertar o consumo exato
— throughput menor é o resultado aceito de propósito. Quem decide se o build cabe é sempre o
orçamento em **pontos**; a estimativa de queries serve à previsão e à observabilidade.

Consequência prática numa conta de 3.600 pts/h: ~45 referências por janela cheia, contra as 100 que
a política antiga exigia de uma vez e nunca conseguia.

## Observabilidade

O lifecycle do build interativo usa o mesmo canal do prewarm (D-34: nada exige abrir o DuckDB de
fora). O snapshot expõe `mode`, `state` (`building`/`deferred_budget`/`ready`/`failed`),
`cohort_id`, `job_id`, `planned`, `completed`, `remaining`, `batch_size`, `current_budget` e
`estimated_points_remaining`. Todos são campos de lifecycle: `set_context` os recusa, pela mesma
regra estrutural que o bug do prewarm impôs.

O caminho da fila — o caro — passou a produzir artefato de telemetria por tentativa
(`data/ops/analysis-runs/`), com `job_id` para correlação. Um adiamento registra
`cold_build_started=true`, `cold_build_state=deferred_budget` e o progresso, e **nunca**
`final_status=completed`: um adiamento não é conclusão, e também não é `analysis_failed`.
