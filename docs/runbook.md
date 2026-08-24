# Runbook operacional — botgitgud v1.0

Todos os comandos partem da raiz e usam `.venv\Scripts\python.exe`. Nenhum comando deste runbook
consulta a WCL ou consome pontos.

## Como o `ops-status` se comporta (leia antes de tudo)

O `serve` mantém o `data/warehouse.duckdb` aberto para escrita, e o DuckDB **nega qualquer outra
conexão ao mesmo arquivo, inclusive read-only** (verificado ao vivo, DuckDB 1.5.5 — ver
`docs/desvios.md` D-34). Por isso o comando tem dois modos, e ele mesmo diz em qual está:

| Situação | Saída | Exit |
|---|---|---|
| Bot **parado** | `warehouse=ok` + `source=local_warehouse` — inspeção completa: tabelas, tamanho, Parquets, fila por estado | 0 |
| Bot **rodando** | `warehouse=locked_by_running_bot` + `source=running_bot` — fila, PID, idade do snapshot e último orçamento observado, publicados pelo próprio bot | 0 |
| Bot rodando e **sem snapshot legível** | mensagem controlada explicando o lock e o que fazer | 75 |
| Warehouse ausente | `warehouse=missing path=...` | 0 |

O modo `running_bot` lê o `data/ops-snapshot.json` que o worker publica a cada 2s. Ele cobre fila,
PID e orçamento, mas **não** cobre tabelas, tamanho do banco nem contagem de Parquets — para isso é
preciso parar o bot. `snapshot_stale=true` significa que o bot parou de escrever há mais de 30s:
trate como processo travado ou morto.

---

1. **Orçamento próximo do piso.** Com o bot no ar, rode
   `python -m botgitgud.cli ops-status`: a linha `points_remaining=` traz a **última leitura feita
   pelo próprio bot**. Se aparecer `points_remaining=not_queried_yet`, o bot ainda não consultou o
   orçamento — isso é normal e esperado com a fila vazia, porque ele só consulta quando há job
   `queued`. Não force uma consulta. `claim_next` preserva o piso (default 1.000) e a reserva de
   25% para o caminho interativo. Pause novos pedidos e aguarde o reset; nunca reduza o piso
   durante incidente.
2. **Orçamento esgotado.** `RateLimitBudgetExceeded` reenfileira o job sem falhá-lo. Aguarde o
   `pointsResetIn` registrado no log; não force retries, não rode `probe-schema`, discovery ou
   coleta experimental.
3. **Job travado.** Rode `python -m botgitgud.cli ops-status` — funciona com o bot no ar. Olhe
   `running=` e `oldest_running_started_at=`: um job `running` cuja idade excede em muito a duração
   típica de uma análise é suspeito. Se `snapshot_stale=true` ou o comando devolver exit 75, o
   processo do bot provavelmente morreu; confirme com a lista de processos.
4. **Recuperar jobs.** `recover-jobs` **escreve** no warehouse, logo exige o bot parado — com o bot
   no ar ele recusa com mensagem clara e exit 75, sem traceback. Na prática você raramente precisa
   dele: o próprio boot do bot já reverte `running → queued` (`on_ready` chama
   `recover_from_crash`). Use-o quando quiser reverter sem subir o bot: pare o processo, rode
   `python -m botgitgud.cli recover-jobs`, depois `python -m botgitgud.cli ops-status` para
   conferir.
5. **Discord desconectou.** Confirme processo e log. Na reconexão, `on_ready` recupera jobs e o
   guard mantém um único worker (`discord_bot.worker_started` aparece só na primeira vez). Se não
   reconectar após o backoff do Discord, pare e reinicie uma vez; confira a fila pelos comandos
   acima.
6. **WCL indisponível.** O cliente tenta até 4 vezes com backoff exponencial e jitter. Em
   `RateLimitCheckFailed`, a fila falha fechada e não reivindica job. Não contorne o check; aguarde
   e acompanhe por `ops-status`, que não depende da WCL.
7. **Suspeita de corrupção.** Pare o bot, não abra o arquivo em modo escrita e não restaure sobre
   ele. Faça uma cópia de evidência e rode
   `python -m botgitgud.cli ops-status --data-dir CAMINHO`. Com o bot parado, qualquer saída
   diferente de `warehouse=ok` indica problema real — exige isolar o diretório e seguir o restore.
8. **Backup/restore.** Com o bot parado, copie `data/warehouse.duckdb`, `data/raw/` e `spells.json`
   para um diretório de backup. Restaure sempre em outro diretório, rode
   `python -m botgitgud.cli ops-status --data-dir DIRETORIO_RESTAURADO` e compare as contagens das
   tabelas com o snapshot. A política detalhada está em `docs/warehouse-policy.md`.
9. **Parar/reiniciar.** Interrompa o processo normalmente e espere encerrar. Se morreu durante um
   job, o próximo boot já reverte; se preferir reverter antes de subir, use `recover-jobs` com o bot
   parado. Então `python -m botgitgud.cli serve`. Nunca mantenha duas instâncias sobre o mesmo
   warehouse — a segunda falha ao abrir o banco, por desenho.
10. **Saúde básica.** `python -m botgitgud.cli ops-status` e `python -m botgitgud.cli --help`.
    Com o bot no ar, espere `source=running_bot`, `snapshot_stale=false` e nenhuma fila presa. Com
    o bot parado, espere `warehouse=ok` e 741 Parquets no baseline. O comando não autentica Discord
    nem WCL; conectividade real pertence ao smoke humano (R1-01).

---

Ensaio R3-03 em 2026-08-24: backup de 172.503.638 bytes em 1,066 s; restore separado em 0,438 s;
741 Parquets, 15 tabelas e todas as contagens iguais, inclusive as seis tabelas irreproduzíveis.
O destino permanente do backup ainda precisa ser escolhido pelo operador.
