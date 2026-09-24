# Operação

Como operar o bot: saúde, incidentes, supervisão de processo, logs, dados e backup,
credenciais e custo de API. O deploy Linux/systemd está em
[`linux-deployment.md`](linux-deployment.md). Nenhum comando deste documento consulta a WCL
nem consome pontos, salvo quando indicado.

Comandos partem da raiz do repositório (`.venv\Scripts\python.exe` no Windows,
`.venv/bin/python` no Linux).

## 1. Saúde e estado — `ops-status`

O `serve` mantém `data/warehouse.duckdb` aberto para escrita e o DuckDB nega qualquer outra
conexão ao mesmo arquivo, inclusive read-only. Por isso o processo dono publica
`data/ops-snapshot.json` (escrita atômica, a cada tick do worker e no `on_ready`), e
`ops-status` tem dois modos, que ele mesmo declara:

| Situação | Saída | Exit |
|---|---|---|
| Bot parado | `warehouse=ok`, `source=local_warehouse`: tabelas, tamanho, Parquets, fila por estado | 0 |
| Bot rodando | `warehouse=locked_by_running_bot`, `source=running_bot`: fila, PID, idade do snapshot, último orçamento observado, coortes READY, lifecycle de cold build | 0 |
| Bot rodando sem snapshot legível | mensagem controlada | 75 |
| Warehouse ausente | `warehouse=missing path=...` | 0 |

Com o bot no ar, `snapshot_stale=true` (sem escrita há mais de 30 s) significa processo
travado ou morto. O snapshot nunca dispara consulta à WCL; `points_remaining=not_queried_yet`
é normal com a fila vazia. Tabelas, tamanho do banco e contagem de Parquets exigem o bot parado.

## 2. Incidentes

1. **Orçamento próximo do piso.** `claim_next` preserva o piso (default 1.000 pontos) e a
   reserva de 25% para o caminho interativo. Pause novos pedidos e aguarde o reset; nunca reduza
   o piso durante incidente.
2. **Orçamento esgotado.** `RateLimitBudgetExceeded` reenfileira o job sem falhá-lo. Aguarde o
   `pointsResetIn`; não force retries nem rode `probe-schema`, discovery ou coleta experimental.
3. **Job em `deferred_budget`.** Não é falha: é uma coorte fria sendo construída por janelas,
   com progresso preservado (ver §6). O job volta sozinho depois de `deferred_until` e o usuário
   não repete `!analisar`. O bloco `cold_build=` do `ops-status` mostra `state`, `planned`,
   `completed`, `remaining` e `estimated_points_remaining`. Não recrie o job nem rode
   `recover-jobs` para destravá-lo. Se `completed` não sobe entre janelas com orçamento, procure
   `defer_reason=no_progress` (referência inservível, não orçamento).
4. **Job travado.** `running=` e `oldest_running_started_at=` no `ops-status`. Exit 75 ou
   `snapshot_stale=true` indicam processo morto; confirme na lista de processos.
5. **Recuperar jobs.** `recover-jobs` escreve no warehouse, logo exige o bot parado (com o bot no
   ar recusa com exit 75). O boot já reverte `running → queued` (`on_ready` chama
   `recover_from_crash`).
6. **Discord desconectou.** Na reconexão, `on_ready` recupera jobs e o `WorkerSupervisor` mantém
   um único worker (cria um só se não houver um vivo). Se não reconectar após o backoff do
   Discord, reinicie uma vez.
7. **WCL indisponível.** O cliente tenta até 4 vezes com backoff exponencial e jitter. Em
   `RateLimitCheckFailed` a fila falha fechada e não reivindica job.
8. **Suspeita de corrupção.** Pare o bot, faça uma cópia de evidência e rode
   `ops-status --data-dir CAMINHO`; qualquer saída diferente de `warehouse=ok` com o bot parado
   exige isolar o diretório e restaurar (§5).
9. **Parar/reiniciar.** Nunca mantenha duas instâncias sobre o mesmo warehouse — a segunda falha
   ao abrir o banco, por desenho.

`!status` no Discord informa a fila e a liveness real do worker (worker parado/travado aparece
explicitamente).

## 3. Supervisão de processo

A política de restart está inteira em Python (`src/botgitgud/ops/supervisor.py`,
`python -m botgitgud.cli supervise`), portável entre Windows e Linux; o lançador de cada
plataforma só inicia o supervisor e não decide reinícios.

- **Filho**: `sys.executable -m botgitgud.cli serve`, `cwd` = raiz do repositório. O supervisor
  não carrega credenciais nem abre o warehouse.
- **Restart**: qualquer saída sem `control/stop.request` é inesperada (inclusive exit 0).
  Backoff `min(2 · 2^(n−1), 60)` s; volta a 1 se o filho ficou de pé mais de 300 s.
- **Storm breaker**: 5 restarts em 600 s encerram o loop (`restart_storm_detected`) e exigem
  intervenção humana.
- **Instância única**: lock do supervisor (`control/supervisor.lock`), um único filho rastreado e
  verificação real de liveness do PID em `control/bot.pid` na subida.
- **Parada limpa por arquivo, nunca por sinal**: `control/stop.request` é observado pelo próprio
  bot (poll de 1 s), que chama `bot.close()`; o `finally` do `serve` fecha Store e cliente. O
  supervisor só espera o PID sumir e escala para terminate após 30 s. Quem lê
  `stop.request` nunca o apaga; só um `start` deliberado o limpa.

**Windows**: `scripts/install-bot-service.ps1` registra uma tarefa do Task Scheduler que lança
`scripts/bot-supervisor.ps1` (disparo `Logon` por padrão; `-Trigger Startup` para 24/7, pedindo
a credencial do Windows interativamente). `start-`, `stop-` e `uninstall-bot-service.ps1` são
idempotentes. Nenhum script lê o conteúdo do `.env`.

**Linux**: systemd supervisiona apenas o supervisor Python — ver
[`linux-deployment.md`](linux-deployment.md).

| Sintoma | Onde olhar |
|---|---|
| Bot cai e não volta | `supervisor.jsonl`: `restart_storm_detected` → iniciar manualmente após corrigir a causa |
| Crash ou parada manual? | `discord_bot.stop_requested` seguido de `process.stopped` = parada limpa; ausência de `process.stopped` = crash |
| Suspeita de dois processos | três camadas impedem; confira `control/bot.pid` e a lista de processos |

## 4. Logs operacionais

| Fonte | Responde |
|---|---|
| `data/logs/botgitgud.jsonl` (+ `.1..N`) | a história: o que aconteceu, em que ordem |
| `data/logs/supervisor.jsonl` | ciclo do supervisor e dos filhos |
| `data/ops-snapshot.json` | o estado atual |
| `data/ops/analysis-runs/<id>.json` | o detalhe de uma análise/tentativa (com `job_id`) |
| tabela `jobs` | o estado dos pedidos, inclusive `delivery_status` |

JSON Lines, um evento por linha, com `timestamp`, `level`, `event`, `logger`, `pid` e
`session_id` (12 hex, novo a cada processo; o `serve` o imprime no boot). Exceções viram
`exception_type`, `exception_message` e `traceback` como string JSON. Rotação por tamanho:
`LOG_MAX_BYTES` (10 MB) × (`LOG_BACKUP_COUNT` 5 + 1) ≈ 60 MB. O sink de arquivo só é ligado por
`serve` e `build-cohort`.

Redação na fronteira de escrita: qualquer chave contendo `token`, `secret`, `password`,
`passwd`, `authorization`, `api_key`, `apikey` ou `credential` vira `***redacted***`. Payloads de
API e conteúdo de relatório nunca são logados.

Isolar um run: `jq -c 'select(.session_id=="<id>")' data/logs/botgitgud.jsonl*`. Grupos de
eventos: `process.*`, `discord_bot.*`, `delivery.*`/`report_delivery.*`, `job.*`/`worker.*`,
`cold_build.*`, `wcl.*`. `job.started` e `job.resumed` se distinguem por `defer_count`.

## 5. Dados, backup e restore

Tudo fica sob `DATA_DIR` (default `data/`, ignorado pelo Git).

| Classe | Dados | Política |
|---|---|---|
| Irreproduzível / auditoria | `experiment_campaigns`, `experiment_campaign_observations`, `experiment_observation_attempts`, `experiment_architecture_eval_runs`, `experiment_architecture_decision_runs`, `experiment_calibration_runs` | backup obrigatório antes de operação destrutiva; retenção indefinida; nunca purgar |
| Reproduzível a custo de API | `logs`, `data/raw/*.parquet`, `discovery_reports`, `discovery_fights`, `discovery_targets`, `cohort_candidates` | backup diário enquanto houver ingestão e antes de release; 7 diários + 4 semanais |
| Operacional / regenerável | `runs`, `jobs`, `backfill_checkpoints`, `spells`, `data/spells.json` | incluir na cópia; `runs` é auditoria útil |
| Reservado | `phase4_model_registry` | deve permanecer vazio |

- Schema aditivo: sinais novos entram em colunas opcionais; Parquets antigos continuam legíveis
  (detalhes vazios = incompletude, não inválido) e nunca são migrados ou regravados.
- Backup: bot parado; copiar `warehouse.duckdb`, `data/raw/` e o catálogo; verificar restore.
- Restore sempre em diretório novo; validar com `ops-status --data-dir DIRETORIO` comparando
  contagens de tabelas; só então trocar `DATA_DIR`. Nunca restaurar diretamente sobre `data/`.
- No Linux, `deploy-backup` / `deploy-restore` / `deploy-list-backups` automatizam isso.
- Revisar a política ao ultrapassar 1 GiB (≈ 51 KiB de Parquet por log).
- Benchmarks de setup ainda na política v1 são reconstruídos em v2 só com o progresso local
  (sem API): `python scripts/maintenance/rebuild_setup_benchmarks_v2.py --data-dir data`.

**Catálogo de spells**: `spells.json` na raiz é um seed versionado e somente leitura; o cache de
runtime é `DATA_DIR/spells.json`, copiado do seed no primeiro boot e o único destino de escrita.
Cache corrompido é colocado em quarentena (`spells.corrupt.<timestamp>.json`) e recomeça vazio.

## 6. Coortes frias e orçamento de API

Uma coorte fria exige buscar ~100 logs de referência (medido: ~23,4 queries por referência).
Custo, adiamento e retomada:

- **Execução resumível** (`RESUMABLE_INCREMENTAL`, usada pelo `build-cohort` e pelo caminho
  interativo): basta que **uma** referência caiba no orçamento. A cada lote de até 5 referências
  o orçamento real é remedido; o cache de logs é o checkpoint (nenhuma referência concluída
  volta a custar pontos). O pool de candidatos — o sinal de coorte READY — só é gravado quando
  todas as referências planejadas estão em cache.
- **Preflight**: ausência de snapshot de orçamento falha fechada; o build só avança se sobrar
  `max(piso, reserva do hot path) + margem` depois do custo estimado. Modelo de custo:
  `cold_build_queries_per_reference=23.4` com banda 1,3 e pontos por query 1,413 com banda 1,2 —
  deliberadamente conservador (nunca superestimar quantas referências cabem). Numa conta de
  3.600 pontos/h cabem ~45 referências por janela.
- **Adiamento**: `CohortDeferredBudget` põe o job em `deferred_budget` com
  `deferred_until = agora + pointsResetIn` (sem esse dado, 600 s); o mesmo job (mesmo `job_id`)
  é reivindicado de novo depois disso. Um pedido repetido é deduplicado no job existente. Só a
  primeira tentativa avisa o usuário que a análise continuará sozinha.
- **Single-flight** por `cohort_id`: pedidos da mesma coorte esperam o líder; o seguidor também
  adia. A fila é serial, com prioridade para análises quentes.
- **Prewarm**: `build-cohort --encounter E --class C --spec S --difficulty D [--duration-bucket B]`
  passa pelo mesmo preflight; exit 75 = adiamento, rodar de novo após o reset. Planejar fora do
  caminho interativo. `ops-status` lista as coortes READY.
- Replay aritmético do mix medido: `python -m botgitgud.analysis.cold_build_benchmark`.

Limites conhecidos: uma referência permanentemente inservível impede a coorte de fechar
(`defer_reason=no_progress`); o percentil de referência não é otimizado; o snapshot é
observabilidade por arquivo, não monitoramento histórico.

## 7. Entrega no Discord

- Cada análise concluída entrega exatamente uma mensagem de texto, sem anexo e sem URL
  (garantido por teste). Permissões necessárias no canal: **View Channel** e **Send Messages**.
- Nenhuma exceção do Discord atravessa `bot/delivery.py`: toda falha vira um `DeliveryOutcome`
  classificado (`http_status`, `discord_code`) e atualiza apenas o `delivery_status` do job;
  o resultado analítico (`status`) fica intacto.
- `_run_one_job` é a última fronteira: um job nunca fica silenciosamente `running`.
- O worker é vigiado pelo `WorkerSupervisor`: morte do worker aparece como
  `discord_bot.worker_crashed` e ele é recriado no `on_ready` seguinte, sem loop de restart.

## 8. Credenciais e publicação

Cinco credenciais obrigatórias, só no `.env` local (nunca versionado): `DISCORD_TOKEN`,
`WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`, `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`.

Rotação: revogar e reemitir o token do bot no Discord Developer Portal; reemitir os pares WCL e
Blizzard e confirmar que os antigos falham; atualizar apenas o `.env`; nunca colar valores em
issue, chat, teste ou documento.

Antes de publicar ou fazer push: `python -m botgitgud.cli publication-check` — recusa `.env`,
chaves privadas, warehouse DuckDB, backups e dados de runtime versionados. Não substitui secret
scanning. Os cassettes de teste têm `Authorization` e tokens OAuth redigidos na gravação
(`tests/fixtures/http_cassette.py`), e `tests/fixtures/record.py` recusa salvar se encontrar
`Bearer ` ou um JWT.
