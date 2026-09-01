# Telemetria operacional por análise

## INCIDENT — HOT-PATH Fiskowl smoke

O smoke do hot path após o prewarm teve **sucesso funcional**: a análise usou a coorte correta
(`f91dffaf13ddc899`, READY, 37 membros, 37/37 em cache), produziu
`data/reports/interactive-da9d4ff4034d6941226a4eba.html` (42.884 bytes), entregou no Discord, e o
worker terminou vivo com a fila zerada.

Mesmo assim foi classificado **FAIL** — por falta de evidência.

Depois que o processo morreu, não havia como provar:

- quantas queries WCL a análise fez, nem o breakdown por `op_name`;
- pontos antes/depois/consumidos;
- retries;
- se algum log de referência foi refetched;
- quantas queries pertenciam ao jogador analisado vs. à coorte;
- quando o relatório foi persistido em relação ao envio;
- o status da entrega.

Tudo isso existia apenas no **stdout efêmero** da execução. Um PASS operacional que não pode ser
auditado depois não é um PASS.

## FIX — artefato de telemetria por execução

Cada análise interativa passa a deixar `data/ops/analysis-runs/<analysis_id>.json`.

**Por que arquivo e não warehouse:** gravar no DuckDB reintroduziria a D-34 — a auditoria exigiria
derrubar o processo dono. O artefato é lido sem abrir o banco, como o ops-snapshot.

**Escrita atômica** (tmp + `replace`) e **JSON-safe** pela mesma fronteira `_json_safe` da correção
`a4598c9`: `datetime` → ISO-8601, tipo inesperado levanta erro em vez de virar string.

### Contabilidade no ponto central

`WclClient.query(..., op_name=...)` já era o ponto por onde **toda** query passa. A instrumentação
entra ali — uma linha, sem contador paralelo. O recorder ativo viaja por `ContextVar`, então nada
novo atravessa a pilha; fora de uma análise instrumentada a chamada é no-op.

### Reference vs player: papel explícito, não inferência

A mesma `op_name` (`fetch_player_meta`) serve o jogador analisado **e** cada membro da coorte —
inferir pelo nome da operação seria errado. O que distingue é a **fase do pipeline**, então
`run_analysis` marca o escopo:

```python
with telemetry.role_scope(QueryRole.PLAYER_ANALYZED):
    player_log = deps.fetcher.fetch(...)
...
with telemetry.role_scope(QueryRole.REFERENCE):
    reference_logs = fetch_cohort_logs(...)
```

Isso é o que permite provar o invariante do hot path: **`reference_query_count = 0`**.

### Cache de referências

`fetch_many` já sabia `cache_hits`/`cache_misses`; agora reporta ao recorder. Num hot path
Fiskowl-like: `expected=37`, `cache_hit=37`, `refetched=0` — medido, não inferido da ausência de
escrita de Parquet.

### Orçamento sem inventar delta

`points_before`/`points_after` vêm da propriedade já cacheada do cliente (sem chamada de rede
extra). Se a janela horária resetar no meio (`after > before`), a subtração produziria um número
absurdo: nesse caso `points_consumed = null` e `accounting_reason = "window_reset"`. Orçamento
desconhecido vira `null` com `accounting_reason = "budget_unknown"`.

### Entrega

`report_persisted_at`, `delivery_started_at`, `delivery_finished_at`, `delivery_status`,
`channel_id`, `guild_id` — o que torna **provável a posteriori** que a persistência precedeu a rede.

### Falhas também deixam telemetria

O tracker publica pelo `finally`, então `return` antecipado e exceção ainda produzem artefato
auditável: `analysis_failed`, `report_persistence_failed`, `insufficient_cohort`,
`scope_rejected`, `target_not_found`, `wcl_api_error`, `enqueued_cold_build`. Falhar ao gravar
telemetria **nunca** derruba a análise.

## Exemplo de auditoria posterior

Executando só a leitura do artefato, sem nenhum log da execução original:

```
Qual coorte foi usada?     f91dffaf13ddc899 (37 membros, ready)
Houve cold build?          False | hot_path: True
Reference refetched?       0 (cache 37/37)
Queries WCL totais?        3
Breakdown por op_name?     {'fetch_player_meta': 2, 'fetch_zone_partitions': 1}
Reference vs player?       reference=0 player=3
Retries?                   1
Pontos consumidos?         6.0 | motivo: None
Persist antes do envio?    True
Delivery status?           delivered
Status final?              completed
```

## ops-status

`summarize_for_snapshot()` expõe apenas o resumo do último run (id, status, queries, pontos,
entrega). O payload detalhado fica no arquivo — o snapshot não engorda.

## Limite conhecido

O caminho **assíncrono** (fila/worker) ainda não usa o tracker. A abstração é reutilizável e
deliberadamente não acoplada ao Discord, mas estender o worker não era o mínimo obrigatório desta
correção — o caminho que falhou na auditoria foi o interativo. Fica registrado como próximo passo
natural.
