# Runbook operacional — botgitgud v1.0

Todos os comandos partem da raiz e usam `.venv\Scripts\python.exe`. `ops-status` é local,
read-only e não consulta WCL.

1. **Orçamento próximo do piso.** Rode `python -m botgitgud.cli ops-status`. Ele confirma o piso
   configurado (default 1.000), mas deliberadamente mostra `live_budget=not_queried`: consultar o
   saldo exige WCL. Observe logs sanitizados do serviço; `claim_next` preserva o piso e 25% para o
   caminho interativo. Pause novos pedidos e aguarde reset; não reduza o piso durante incidente.
2. **Orçamento esgotado.** `RateLimitBudgetExceeded` reenfileira o job sem falhá-lo. Aguarde o
   `pointsResetIn` registrado no log; não force retries, não rode `probe-schema`, discovery ou
   coleta experimental.
3. **Job travado.** Pare o bot e rode `python -m botgitgud.cli ops-status`; `running>0` com nenhum
   processo ativo é crash confirmado.
4. **Recuperar jobs.** Com o bot parado, rode `python -m botgitgud.cli recover-jobs` e depois
   `python -m botgitgud.cli ops-status`. A transição é somente `running → queued`, limpando
   `started_at`; jobs concluídos/falhos não mudam.
5. **Discord desconectou.** Confirme processo/log. Na reconexão, `on_ready` recupera jobs e o guard
   inicia um único worker. Se não reconectar após o backoff do Discord, pare e reinicie uma vez;
   confira a fila pelos comandos acima.
6. **WCL indisponível.** O cliente tenta até 4 vezes com backoff exponencial e jitter. Em
   `RateLimitCheckFailed`, a fila falha fechada e não reivindica job. Não contorne o check; aguarde
   e valide `ops-status` local.
7. **Suspeita de corrupção.** Pare o bot, não abra o arquivo em modo escrita e não restaure sobre
   ele. Faça uma cópia de evidência e rode `python -m botgitgud.cli ops-status --data-dir CAMINHO`.
   Saída diferente de `warehouse=ok` exige isolar o diretório e seguir o restore.
8. **Backup/restore.** Com o bot parado, copie `data/warehouse.duckdb`, `data/raw/` e `spells.json`
   para um diretório de backup. Restaure sempre em outro diretório, rode
   `python -m botgitgud.cli ops-status --data-dir DIRETORIO_RESTAURADO` e compare contagens das
   tabelas com o snapshot. A política detalhada está em `docs/warehouse-policy.md`.
9. **Parar/reiniciar.** Interrompa o processo normalmente e espere encerrar. Se morreu durante um
   job, execute `recover-jobs`; então `python -m botgitgud.cli serve`. Nunca mantenha duas
   instâncias sobre o mesmo warehouse.
10. **Saúde básica.** Rode `python -m botgitgud.cli ops-status` e
    `python -m botgitgud.cli --help`. Espere `warehouse=ok`, 741 Parquets no baseline e nenhuma
    fila presa. O comando não autentica Discord/WCL; conectividade real pertence ao smoke humano.

Ensaio R3-03 em 2026-08-24: backup de 172.503.638 bytes em 1,066 s; restore separado em 0,438 s;
741 Parquets, 15 tabelas e todas as contagens iguais, inclusive as seis tabelas irreproduzíveis.
O destino permanente do backup ainda precisa ser escolhido pelo operador.
