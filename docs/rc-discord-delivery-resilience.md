# RC — Resiliência de entrega no Discord e lifecycle do worker

Blocker de Release Candidate descoberto pelo smoke real de R1-01 em 2026-08-24.

## INCIDENT

Durante o smoke real, dois jobs foram executados.

**Job 1** — `MLFpmBYjtTzPVc1k:24:Wargyu` → `failed` por `InsufficientCohort`
(*"apenas 2 logs de referência dentro da banda de ±35% de duração (mínimo: 8)"*). A mensagem
textual chegou ao Discord. **Esse comportamento estava correto** e continua igual.

**Job 2** — `gCXVMN86PwYhyaDd:9:Zilbag` (DeathKnight/Unholy) → a análise **terminou com sucesso**,
Fases 0–3 completas, job marcado `done` em 4 min 2 s. O envio do relatório HTML falhou:

```
discord.errors.Forbidden: 403 Forbidden (error code: 50013): Missing Permissions
  discord_bot.py:129 in _worker_loop   →  await _notify_outcome(bot, outcome)
  discord_bot.py:96  in _notify_outcome →  await channel.send(..., file=html_file)
```

Três consequências, todas confirmadas nos artefatos:

1. **O relatório nunca chegou ao usuário.**
2. **O relatório existiu apenas em memória.** `jobs.report_path` permaneceu `NULL`. Quando o
   processo morreu, o produto de ~1.600 queries à WCL foi perdido — irrecuperável sem reanálise.
3. **A exceção escapou de `_notify_outcome` e de `_worker_loop` e matou a task do worker.** O bot
   continuou online no gateway do Discord, aparentando saúde, sem nenhum consumidor de fila.

Agravante: o guard `worker_started` (um booleano histórico) permanecia `True` depois da morte da
task, então nem uma reconexão do Discord recriava o worker. O sistema ficava permanentemente sem
consumidor até o processo ser reiniciado — e `!status` continuava respondendo normalmente, sem
qualquer indicação do problema.

## ROOT CAUSE

Duas falhas independentes que se combinaram:

- **A entrega não tinha error boundary.** Nenhuma exceção da API do Discord era tratada, em nenhum
  dos dois níveis (`_notify_outcome` nem `_worker_loop`).
- **O artefato não era persistido antes do envio.** A durabilidade do produto da análise dependia
  do sucesso de uma chamada de rede a terceiros.

E uma terceira, estrutural: **liveness do worker era um fato histórico, não um fato atual.**

## FIX

### Persistência (RC.1/RC.11/RC.12)

`bot/report_store.py` (novo). O HTML vira arquivo em `data/reports/<job_id>.html` **antes de
qualquer tentativa de entrega**, com escrita atômica (tmp + `replace`) para que um caminho
registrado jamais aponte para arquivo parcial. `job_id` é validado contra um padrão restrito — um
valor inesperado nunca escapa do diretório de relatórios.

`worker.run_claimed_job` persiste, registra `worker.report_persisted` e só então chama
`mark_done(report_path=...)`. Se a persistência falhar, o job vai para `failed` como **erro de
produção do artifact** (`ReportPersistenceError`), o outcome sai sem `html_report`, e nenhuma
entrega é tentada com um arquivo inexistente.

### Estados separados (RC.2)

`jobs` ganhou `delivery_status` (`pending` | `delivered` | `failed`) e `delivery_error`, via
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (idempotente; um warehouse anterior ao RC migra
sozinho). `Job.analysis_completed` expõe o fato analítico independentemente da entrega.

Uma falha do Discord agora atualiza **apenas** o estado de entrega. `status`, `error` e
`report_path` ficam intactos.

### Fronteira de entrega (RC.3/RC.9)

`bot/delivery.py` (novo). Nenhuma exceção do Discord atravessa esta camada: toda falha vira um
`DeliveryOutcome` classificado, com `http_status` e `discord_code` preservados. `discord.Forbidden`
e `discord.HTTPException` são tratados explicitamente — não há `except Exception: pass`.

Em `Forbidden`, tenta o fallback textual curto:

> ⚠️ A análise foi concluída, mas não consegui anexar o relatório neste canal. O resultado foi
> preservado para reenvio.

O fallback **nunca revela caminho local do servidor** (coberto por teste). Se o fallback também
falhar, a segunda falha é contida e registrada — a entrega permanece `failed` e o relatório
permanece pendente de reenvio.

### Isolamento por job (RC.4)

`_run_one_job` é a última fronteira: `CancelledError` é repropagado (shutdown continua
funcionando), qualquer outra exceção é logada com stack (`discord_bot.job_crashed`) e o job é
levado a estado terminal — nunca fica silenciosamente `running`. O loop segue para o próximo job.

### Liveness real (RC.5/RC.6/RC.7)

`WorkerSupervisor` substitui o booleano. Guarda a `asyncio.Task`, expõe `is_alive`
(`task is not None and not task.done()`) e `ensure_running()`, que cria um worker **somente se não
houver um vivo**. Um `done_callback` distingue `worker_stopped` (cancelado/retornado) de
`worker_crashed` (exceção, com stack). Reconexão com worker vivo não duplica; worker morto é
recriado no `on_ready` seguinte. Sem restart loop: a recriação acontece por evento, não em laço.

### Reenvio (RC.10)

`deliver_existing_report(channel, job=...)` lê o relatório persistido do disco e reenvia.
**Redelivery ≠ reanalysis:** zero chamadas à WCL, garantido por teste que faz a suíte falhar em
qualquer requisição HTTP.

### Saúde honesta (RC.14)

`!status` passou a reportar a liveness real do worker, não só a fila:

```
📋 Fila vazia - nenhum job ativo.
🛑 Worker: **parado/travado** — jobs na fila não estão sendo processados.
```

Antes, o mesmo estado do incidente respondia apenas `📋 Fila vazia — nenhum job ativo.`

### Observabilidade (RC.13)

Eventos estruturados: `worker.analysis_completed`, `worker.report_persisted`,
`worker.report_persistence_failed`, `delivery.started`, `delivery.succeeded`, `delivery.failed`
(com `http_status`, `discord_code`, `job_id`, `channel_id`), `delivery.fallback_succeeded`,
`delivery.fallback_failed`, `discord_bot.worker_started`, `worker_restarted`, `worker_stopped`,
`worker_crashed`, `discord_bot.job_crashed`.

## OPERATING REQUIREMENT

O bot precisa das seguintes permissões **no canal onde é usado**:

- **View Channel**
- **Send Messages**
- **Attach Files** ← a que faltava e causou o incidente

A configuração de permissões é **ação operacional externa ao código**. O bot não tenta alterá-las,
e a correção acima não substitui concedê-las: sem `Attach Files`, toda análise cairá no fallback
textual e o relatório ficará aguardando reenvio.

## Limite desta correção

O relatório do Zilbag **não foi recuperado** — ele nunca chegou a existir em disco. Nenhum dado
histórico foi fabricado: o job `done` sem `report_path` permanece no warehouse como registro
honesto do incidente. A regeneração do caso real é decisão separada, e custaria WCL.
