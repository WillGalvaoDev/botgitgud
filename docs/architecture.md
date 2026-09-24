# Arquitetura

Como o produto está construído hoje e as decisões de design ainda vigentes. O que o produto
entrega está em [`product.md`](product.md); contratos de medida e comparabilidade em
[`methodology.md`](methodology.md); fatos verificados da API do Warcraft Logs em
[`schema_confirmado.md`](schema_confirmado.md).

## 1. Fluxo de uma análise

```
!analisar <Player> <URL>  ──►  bot/discord_bot.py ──► fila (bot/jobs.py) ──► bot/worker.py
                                                                             │
analysis/pipeline.py: run_analysis ◄─────────────────────────────────────────┘
  1. LogFetcher.fetch(jogador)           ingest/: WCL → PlayerLog (cache DuckDB + Parquet)
  2. scope gate (domain/specs.py)        spec fora do SUPPORTED_SPEC_SET → ScopeRejected
  3. partição corrente, setup analysis   Encounter Benchmark (independente da execução)
  4. CohortCriteria → pool de candidatos Store (quente) ou construção fria incremental
  5. fetch_cohort_logs                   logs das referências (cache primeiro)
  6. quarentena → higiene → M2.1 → ledger R_log (match_covariates) + populações M2.2
  7. dps_gap (M1) e métricas por habilidade, performance, comparações de cooldown
  8. findings → remediation → materiality → prioritization → conclusão
  9. RunManifest + comparability-provenance persistidos em `runs`
         │
         ▼
report/contract.py: ReportContract ──► report/coaching_answer.py (Discord, 1 mensagem)
                                   └─► report/render.py / text.py (CLI `analyze`)
```

Coorte quente: nenhuma consulta a `characterRankings`; só os logs individuais que ainda não
estão no cache. Coorte fria: construção resumível por janelas de orçamento (ver
[`operations.md`](operations.md) §6), com o job em `deferred_budget` entre janelas.

## 2. Pacotes

| Pacote | Responsabilidade |
|---|---|
| `wcl/` | cliente GraphQL v2 (OAuth, timeout, retry com backoff/jitter, orçamento por `rateLimitData`), queries centralizadas, `schema_probe` |
| `blizzard/` | cliente mínimo para nome de spell (fallback do catálogo) |
| `ingest/` | `LogFetcher` (cache primeiro, lote tolerante a falhas por referência), parsing WCL → domínio, agregação de dano jogador+pets, features de performance, `Store` (DuckDB + Parquet), rankings/descoberta/triagem |
| `domain/` | modelos (`PlayerLog`, `FightRef`, `CohortCriteria`…), specs suportadas, identidade e papel canônicos de habilidades, catálogo de spells, tabelas curadas (blacklist, buffs externos, cooldowns, tipos de recurso), `DamageScopeVersion` |
| `analysis/` | pipeline, coorte (bandas, matching, higiene, quarentena), elegibilidade M2.1, população M2.2, proveniência, contabilidade M1 e `dps_gap`, alinhamento/cadência/comparação de cooldowns, grading, performance, core abilities, procs, findings, remediação, materialidade, priorização, setup e Encounter Benchmark, política de cold build |
| `report/` | `ReportContract`, resposta de coaching do Discord, renderização de texto do CLI, sanitização Markdown |
| `bot/` | Discord (`!analisar`, `!status`, `!update`), fila persistente, worker, entrega, jobs de benchmark, snapshot operacional, artefatos por análise |
| `ops/` | supervisor de processo, canal de controle por arquivo, primitivas por plataforma, deploy/preflight Linux, `publication-check` |
| `knowledge/` | conhecimento de rotação curado a partir da Wowhead (ingestão administrativa explícita via `!update`; leitores locais nunca acessam a rede) |
| `phase4/` | trilha experimental de ML — ver [`phase4.md`](phase4.md) |
| `cli*.py` | `serve`, `supervise`, `analyze`, `build-cohort`, invalidação de coorte, `probe-schema`, `ops-status`, `recover-jobs`, comandos de deploy e experimentais |

## 3. Armazenamento

- **Parquet imutável** em `data/raw/encounter_id=<E>/difficulty=<D>/partition=<P>/`, um
  arquivo por ingestão: `<report>_<fight>_<player>_<ingested_at_ms>.parquet`. Reingestão cria
  linha nova; a leitura pega a mais recente (sem PK de unicidade na tabela `logs`).
- **DuckDB** `data/warehouse.duckdb`: `logs`, `cohort_candidates`, `runs`, `jobs`, catálogo,
  benchmark, descoberta e tabelas experimentais. Um único processo é dono do arquivo; todo
  acesso à conexão passa por um `threading.RLock` (leituras inclusive).
- Migrações são aditivas e idempotentes (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`); linhas
  antigas leem campos novos como desconhecidos, nunca como a versão corrente.
- O cache de coorte é o **pool de candidatos** por `cohort_id` (resultado caro de
  `characterRankings`), não um perfil agregado: o matching é por jogador analisado.
- `FightRef.partition` é opcional: resolvido na ingestão (`report.rankings` para o jogador; a
  partição da coorte é propagada às referências).

## 4. Coorte

- `CohortCriteria` (encontro, dificuldade, partição, classe, spec, métrica, bucket de duração,
  `matching_policy_version`) define a identidade `cohort_id`. `className` e `specName` são
  ambos obrigatórios (`--class` no `build-cohort`): "Frost" existe em duas classes.
- Bandas (`analysis/cohort.py`): sanidade ±35% para o pool, posicional ±12%, piso duro 8,
  alvo 15 (`MIN_N_FOR_GRADING`), stretch 30.
- Matching v2 (`cohort_match.py`): higiene (sem o próprio jogador, só kills, uma referência
  por pull e por jogador, desempate `dedup_priority`), depois covariáveis em ordem de
  degradação `tier_pieces → external_buffs → item_level → duration` até o piso; acima do piso
  só covariáveis de baixo viés cedem. Buffs externos comparam apenas IDs ofensivos;
  `has_augmentation` é covariável de ajuste, não filtro. Talentos nunca participam em v2 (a
  coorte de execução é independente do setup). A política v1 continua disponível com seus
  próprios `cohort_id`.
- Referência aspiracional: cauda superior da coorte por `dps` do WCL, com piso e fallback
  (`benchmark_reference.py`), comparada separadamente.

## 5. Fila, worker e orçamento

- Fila persistente em DuckDB (`jobs`, com `job_type` `analyze`/`build_cohort`/benchmark):
  dedup por pedido, justiça por usuário, prioridade para análises quentes, recuperação de crash
  no boot (`running → queued`). Estados: `queued`, `running`, `done`, `failed`,
  `deferred_budget` (trabalho válido aguardando orçamento).
- Orçamento em camadas: piso absoluto (`api_points_floor`) e depois reserva por tipo de job
  (25% para o caminho interativo). Com o limite real da conta (3.600 pts/h), o piso domina a
  reserva — correto em geral, só não produz faixa intermediária nessa conta.
- `RateLimitBudgetExceeded` nunca é tratado como falha pontual de um item: propaga
  (reenfileirando o job) depois de persistir o progresso já obtido.
- Encounter Benchmark (setup) é construído por jobs de prioridade mais baixa que qualquer
  análise interativa, com orçamento próprio.

## 6. Determinismo

- Toda saída é função das entradas: `fetch_many` devolve na ordem pedida (não de conclusão),
  `read_candidate_pool` tem `ORDER BY rowid`, empates de presença resolvem por `spell_id`
  ascendente, e a quarentena + higiene tornam a proveniência invariante à ordem da lista buscada.
- `select_top_actions` e o agrupamento de talentos desempatam pela ordem de entrada, que é
  determinística hoje; se alguma fonte de referências deixar de ser ordenada, são os próximos
  pontos a revisar.
- O catálogo de spells de runtime vive em `DATA_DIR`; o `spells.json` versionado é seed somente
  leitura (produção nunca muta o repositório).

## 7. Testes

- Suíte offline por padrão (`-m "not network"`); respostas reais da WCL gravadas como
  cassettes em `tests/fixtures/cassettes/`, reproduzidas por `ReplayTransport` (httpx) e
  regravadas manualmente por `tests/fixtures/record.py` (tokens redigidos no corpo e nos
  headers).
- O golden (`tests/golden/`) cobre o pipeline completo sobre a fixture real Zarad. A gravação
  limita as referências (rankings truncados a 24 candidatos; eventos de dano/recurso das
  referências truncados a 25 por página), então os números do gap de DPS no golden não são
  realistas: identidade e semântica da decomposição são garantidas por testes sintéticos e
  property-based. A fixture é um log de partição antiga (ver `methodology.md` §6).
- `tests/fixtures/gate1_scope/` é um replay somente-leitura de metadados reais; o corpus
  opcional `data/raw` é usado quando presente e a suíte prova que nunca o modifica.
- Portões: Ruff (lint e format), Pyright, `publication-check` e pytest em Windows e Linux (CI).

## 8. Registro de decisões vigentes

Identificadores citados no código. Cada entrada descreve a regra que continua valendo.

| ID | Decisão vigente |
|---|---|
| D-2 | `schema_probe` grava seu veredito mecânico em `docs/schema_probe_output.md` (gerado sob demanda, fora do Git); o documento curado `schema_confirmado.md` é a fonte de verdade e é referenciado por seção. |
| D-6 | Cassettes são gravados com `Authorization` redigido e com `access_token`/`refresh_token`/`id_token` do corpo substituídos por placeholder; a gravação falha se encontrar `Bearer ` ou `eyJ`. |
| D-7 | `WclClient` recebe `WclClientConfig` (dataclass própria), montada a partir de `Settings`; o cliente gerencia o token internamente. |
| D-8 | `BlizzardClient` existe só para resolver nomes de spell ausentes no WCL, com timeout, retry e cache de token. |
| D-9 | A descoberta de candidatos de coorte vive em `ingest/rankings.py`; `analysis/cohort.py` guarda apenas bandas, limiares e normalização. |
| D-10 | Defaults de coorte em `Settings` são os medidos: sanidade ±35%, posicional ±12%, piso duro 8. |
| D-11 | `CohortCriteria` e `cohort_id()` vivem em `domain/models.py`, junto de `Cohort`. |
| D-12 | Storage imutável: `partition` opcional em `FightRef`; sem PK de unicidade em `logs`; nome de Parquet inclui jogador e timestamp de ingestão. |
| D-13 | `build-cohort` exige `--class` além de `--spec` (className+specName obrigatórios em `characterRankings`); não existe subcomando `backfill`. |
| D-14 | `find_player_in_details` trata formatos inesperados como jogador não encontrado; `fetch_many` pula referências individualmente inválidas e só o lote inteiro vazio falha. |
| D-15 | A fixture de gravação trunca `characterRankings` (o pool vivo só cresce); o piso 8 de produção não muda. |
| D-18 | `RateLimitBudgetExceeded` é capturado antes de `ApiError` genérico e relançado, após persistir o progresso parcial. |
| D-19 | O "writer único" do DuckDB é um `RLock` sobre toda a conexão (sem thread dedicada, sem risco de deadlock aninhado); a fila de jobs usa a mesma conexão. |
| D-20 | `jobs.job_type` distingue tipos de job para prioridade e orçamento. |
| D-21 | Orçamento em camadas: piso absoluto antes da reserva por tipo; com 3.600 pts/h o piso domina (esperado, não bug). |
| D-22 | A cola do Discord é testada com fakes determinísticos (comandos, erros, enqueue, notificação, worker loop, guard de `on_ready`); a conexão real fica fora do teste unitário. |
| D-23 | `cli.py serve` é o único ponto que inicia o processo do bot (`build_bot(deps).run(token)`). |
| D-24 | `talent_cluster` usa similaridade de Jaccard par-a-par contra o alvo (≥ 0,85) e só existe na política v1; em v2 talentos não participam do matching. |
| D-25 | O cache de coorte é o pool de candidatos por `cohort_id`; `fetch_cohort_logs → match_cohort → perfil` roda a cada análise, quente ou fria. |
| D-26 | Diferenças de talento são exibidas por `(nodeID, rank)`: nenhuma API verificada resolve nomes dos nós da árvore. Dívida aceita. |
| D-28 | Nenhuma API expõe cooldown base; `domain/cooldowns.py` tem o mecanismo completo e a tabela começa vazia — sem fonte verificável, a classificação MAJOR/MINOR usa cadência observada (muda rótulo/posição, não a evidência). Dívida aceita. |
| D-29 | `downtime_s` = soma, por morte, de (próximo cast do jogador ou fim da luta) − morte, sem timestamp de revive; alvos por cast não é métrica ativa (ver `methodology.md` §2.3). |
| D-31 | Só achados com ganho quantificável contábil competem pela seleção quantitativa; os demais permanecem nas seções detalhadas (ordenação cross-kind em `prioritization.py`, ver `product.md` §2.2). |
| D-34 | O bot publica `data/ops-snapshot.json` atomicamente; `ops-status` o lê quando o banco está travado pelo processo dono e rotula a origem; `recover-jobs` recusa com exit 75 com o bot no ar. |
| D-35 | Produção nunca escreve no `spells.json` versionado: o cache de runtime vive em `DATA_DIR/spells.json`, semeado uma vez a partir do seed. |
| D-36 | Modelo de custo de cold build recalibrado para 23,4 queries/referência com banda 1,3, separada da banda de pontos 1,2; objetivo é nunca superestimar quantas referências cabem. |
| D-38 | Matching v2 compara só buffs externos ofensivos e declara `has_augmentation` como covariável de ajuste; v1 preservada com seu `cohort_id`. |

Dívidas e limites aceitos adicionais:

- A banda posicional conservadora (±12%) pode reduzir N; a normalização por fase já existe, mas
  ampliar o pool posicional é decisão de política ainda não tomada.
- A convenção de ~300 linhas por arquivo é sinal de revisão, não limite mecânico; módulos
  experimentais maiores e fronteiras coesas (`wcl/client.py`, `ingest/log_fetcher.py`) são
  aceitos.
- Valores numéricos opcionais de `report.rankings` podem vir como `"-"`; o parser os degrada
  para `None`.
- A visão global de `dataset-status` só mostra specs `SUPPORTED`.

Rótulos como `T1.6`, `EB.4`, `RC.3`, `M29` ou "achado 3.1" em docstrings identificam a tarefa que
introduziu o código; o histórico completo está no Git.
