# B5 — trilha operacional durável

## O problema

Todo o registro cronológico do bot vivia em stdout. Se o processo rodasse 24 horas e o terminal
fechasse, não havia como reconstruir startup, ciclo do worker, ciclo dos jobs, adiamentos e
retomadas, retries da WCL, falhas de entrega, lifecycle do cold build ou exceções inesperadas.

O portão da v1.0 exige métricas de um soak de ≥24h. Sem trilha persistente, o soak não consegue
produzir a evidência que o próprio portão pede.

## O que existe e o que este documento acrescenta

O projeto já tinha três fontes de verdade, e **nenhuma foi substituída**:

| Fonte | Responde |
|---|---|
| `data/ops/analysis-runs/<id>.json` | o detalhe de **uma análise** |
| `data/ops-snapshot.json` | o **estado atual** (fila, worker, coortes, cold build) |
| tabela `jobs` no DuckDB | o **estado dos pedidos** |
| **`data/logs/botgitgud.jsonl`** (novo) | a **história**: o que aconteceu, em que ordem |

O snapshot diz o agora; o artefato diz o detalhe; o log diz a sequência. O log **não** copia o
payload dos artefatos — carrega identificadores para correlacionar.

## Localização

```
<DATA_DIR>/logs/botgitgud.jsonl        # ativo
<DATA_DIR>/logs/botgitgud.jsonl.1..N   # rotacionados
```

Com o default, `data/logs/`. O diretório fica sob `data/`, que já está no `.gitignore` — nenhum log
entra no repositório.

## Formato

JSON Lines: **um evento por linha, uma linha por evento**. Não há formato humano como fonte
primária; o console continua legível em paralelo, mas é o arquivo que é a evidência.

```json
{"job_id": "a3f1", "job_type": "analyze", "report_code": "FhYZDLMbwBVAx4KX", "fight_id": 19, "player": "Fiskowl", "channel_id": "1529559571968954489", "deduped": false, "queue_position": 1, "event": "job.queued", "logger": "botgitgud.bot.discord_bot", "level": "info", "timestamp": "2026-08-26T19:50:28.233586Z", "pid": 5748, "session_id": "62d8abdb65b9"}
```

Campos presentes em toda linha: `timestamp` (ISO-8601 UTC), `level`, `event`, `logger`, `pid`,
`session_id`. Os demais aparecem **quando existem** — `job_id`, `analysis_id`, `cohort_id`,
`channel_id`, `guild_id`, `report_code`, `fight_id`, `player`, `attempt`, `exception_type`,
`exception_message`, `traceback`. Nenhum campo é inventado para preencher a linha.

### Exceções

Uma exceção inesperada vira três campos explícitos:

```json
{"event":"process.unexpected_error","exception_type":"ValueError","exception_message":"boom","traceback":"Traceback (most recent call last):\n  File ...\nValueError: boom\n", ...}
```

O traceback é preservado inteiro, mas como **string JSON** — as quebras de linha ficam escapadas, e
a entrada continua sendo uma única linha do JSONL. É isso que permite `grep`/`jq` sobre um arquivo
que contém stacks.

### Um job adiado, do pedido à retomada

A trilha completa de um cold build que atravessou uma janela de orçamento, correlacionada por
`job_id` e `session_id`:

```
process.started                command=serve
discord_bot.gateway_connected
discord_bot.worker_started
job.queued                     job_id=a3f1 report_code=FhYZDLMbwBVAx4KX fight_id=19 player=Fiskowl
job.started                    job_id=a3f1 defer_count=0 points_remaining=3421.0
cold_build.preflight           cohort_id=f91dffaf13ddc899 mode=interactive execution=resumable_incremental
cold_build.deferred_budget     cohort_id=f91dffaf13ddc899 planned=100 completed=30 remaining=70 reason=budget
worker.job_deferred_budget     job_id=a3f1 completed=30 retry_after_s=1800.0
job.resumed                    job_id=a3f1 defer_count=1 points_remaining=3600.0
```

Nenhuma dessas perguntas era respondível depois que o processo morria.

## Rotação e retenção

Rotação **por tamanho** (`logging.handlers.RotatingFileHandler`), não diária: o volume depende da
carga e não do relógio — um dia ocioso gera quase nada e um dia de prewarm gera muito. Um teto por
tamanho limita o que precisa ser limitado (disco); um teto diário não limitaria.

| Ajuste | Default | Efeito |
|---|---|---|
| `LOG_MAX_BYTES` | 10.000.000 | tamanho do arquivo ativo antes de rotacionar |
| `LOG_BACKUP_COUNT` | 5 | quantos rotacionados são mantidos |

Teto de disco = `LOG_MAX_BYTES × (LOG_BACKUP_COUNT + 1)` ≈ **60 MB**. Os mais antigos são
descartados pelo próprio handler; nenhum serviço externo, nenhum cron.

## Session id

Cada início de processo gera um `session_id` (12 hex) que vai em **todas** as linhas daquele run. O
`serve` também o imprime no stdout no boot:

```
session_id=90a27cde5bb8
```

É por ele que se isola exatamente um soak — dois runs escrevem no mesmo arquivo, e sem o id
viravam um borrão só.

## Eventos cobertos

| Grupo | Eventos |
|---|---|
| Processo | `process.started`, `process.stopping`, `process.stopped`, `process.unexpected_error` |
| Discord | `discord_bot.gateway_connected`, `discord_bot.ready`, `delivery.started`, `delivery.succeeded`, `delivery.failed`, `delivery.fallback_succeeded`, `delivery.fallback_failed`, `discord_bot.notify_channel_missing` |
| Worker | `discord_bot.worker_started`, `discord_bot.worker_restarted`, `discord_bot.worker_stopped`, `discord_bot.worker_crashed` |
| Job | `job.queued`, `job.started`, `job.resumed`, `worker.job_deferred_budget`, `worker.job_requeued_budget_exceeded`, `worker.analysis_completed`, `worker.job_failed`, `discord_bot.job_crashed` |
| Cold build | `cold_build.preflight`, `cold_build.build_started`, `cold_build.building`, `cold_build.deferred_budget`, `cold_build.build_completed`, `cold_build.build_failed`, `cold_build.deferred`, `cold_build.allowed` |
| WCL | `wcl.query_transport_error` (com `attempt`), `wcl.rate_limit_check_failed`, `wcl.rate_limit_refresh_transport_error`, `wcl.rate_limit_budget_exceeded` |
| Ops | `discord_bot.ops_snapshot_write_failed` |

Os nomes seguem a convenção `modulo.evento` que o projeto já usava; nenhuma segunda convenção foi
introduzida. O lifecycle de cold build passou a emitir log **além** de atualizar o snapshot: o
snapshot só guarda o estágio atual, e "por quantas janelas este build passou" é pergunta de
história.

`job.started` e `job.resumed` são distinguidos por `defer_count`: o momento da reivindicação é o
único ponto do sistema que sabe se é a primeira execução ou a retomada de um adiamento.

## O que NÃO é logado

Redação por nome de chave, aplicada na fronteira de escrita — console **e** arquivo. Qualquer chave
cujo nome contenha `token`, `secret`, `password`, `passwd`, `authorization`, `api_key`, `apikey` ou
`credential` tem o valor substituído por `***redacted***`.

Redigir na fronteira (e não em cada chamada) é o que torna a garantia verificável: não depende de
todo autor futuro lembrar da regra, e um campo novo chamado `refresh_token` já nasce coberto.

Também não vão para o log: payloads completos de resposta da API, conteúdo de relatório e o corpo
das requisições. O log carrega identificadores, não conteúdo.

## Como auditar um soak de 24h

1. **Antes de subir**, anote o `session_id` impresso pelo `serve` (ou leia a primeira linha
   `process.started`).
2. Isole o run:
   ```
   jq -c 'select(.session_id=="90a27cde5bb8")' data/logs/botgitgud.jsonl*
   ```
3. Perguntas do portão da v1.0, respondidas direto do arquivo:

| Métrica do portão | Filtro |
|---|---|
| crashes não recuperados | `select(.event=="discord_bot.worker_crashed" or .event=="process.unexpected_error")` |
| restarts do worker | `select(.event=="discord_bot.worker_restarted")` |
| jobs recebidos / concluídos / falhos | `select(.event | startswith("job.") or startswith("worker.job"))` |
| jobs presos | `job.started` sem `worker.analysis_completed` correspondente para o mesmo `job_id` |
| consumo de API | `select(.event=="wcl.rate_limit_budget_exceeded")` e o `points_remaining` de `job.started` |
| latência por job | delta entre `job.started` e `worker.analysis_completed` do mesmo `job_id` |
| falhas WCL | `select(.event | startswith("wcl."))` |
| falhas Discord | `select(.event | startswith("delivery.") and .level!="info")` |

4. Os arquivos rotacionados (`.1`, `.2`, …) fazem parte do mesmo run: inclua o glob. Um soak de 24h
   pode atravessar rotações.

## Limites conhecidos

- **Um processo por arquivo.** A escrita é atômica por linha dentro de um processo, mas dois
  processos apontando para o mesmo `data_dir` intercalariam escritas. O projeto já proíbe duas
  instâncias sobre o mesmo warehouse (o DuckDB recusa), então isso não é alcançável hoje.
- **Rotação não é atômica entre processos** — mesma razão, mesmo limite.
- O sink é ligado apenas pelos comandos de longa duração (`serve` e `build-cohort`). Um `analyze`
  ou `ops-status` de dez segundos não é um soak, e criar `data/logs/` a cada invocação de CLI só
  encheria o disco de ruído.
