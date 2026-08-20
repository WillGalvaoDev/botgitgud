# Coleta do experimento da Fase 4

## Escopo

`experiment-collect` executa somente observações selecionadas pelo `experiment-plan`. O módulo
termina na persistência dos logs que alimentam `experimental_dataset.py`: não treina modelos,
não muda thresholds e não inclui `alignment_score` nem `total_parses`.

## Lifecycle e plano congelado

Uma campanha nova valida os argumentos, executa o planner uma vez, materializa toda a seleção
em `experiment_campaign_observations` e só então permite coleta. Cada linha conserva ordinal,
`Phase4Target`, report, fight, player, percentile, bucket e timestamp. Resume por `--campaign`
carrega essas linhas; não chama o planner, não reestratifica e não cria replacements.

O `campaign_id` tem formato `exp-<sha256[0:20]>`. O hash canônico inclui difficulty,
partition, versão do planner, versão do schema de features, máximo de observações, seed/universo
representado pela lista ordenada de chaves naturais e teto da campanha. A mesma seleção e os
mesmos parâmetros produzem a mesma identidade; campanhas diferentes não compartilham estado.

## Checkpoint, resume e idempotência

Cada observação passa por `pending`, `collecting` e um estado terminal: `completed`, `failed` ou
`rejected`. Tentativas, horários, pontos, motivo, referência de saída e métricas de cache ficam
persistidos. Uma retomada converte um `collecting` interrompido em `pending`; estados terminais
não são coletados novamente. Falhas e rejeições permanecem auditáveis e nunca são substituídas.

Falhas operacionais distinguem player/fight indisponível e erro de API. Rejeições distinguem
partition, difficulty, encounter ou spec divergente, e percentile ausente. Exaustão do budget
para normalmente, preserva o checkpoint e define `stopped_reason`.

## Localidade de fight e event sharing

A seleção mantém o ordinal original. A ordem de execução agrupa observações do mesmo fight pela
ordem da primeira ocorrência e conserva o ordinal dentro do fight. Isso não muda o conjunto
selecionado. Um `FightSession` permite que backends reutilizem payload fight-wide com segurança.
O adapter atual do `LogFetcher` só declara cache hit quando nenhuma query foi feita; ele não
finge compartilhamento para queries WCL player-specific. Hits, páginas reutilizadas e custo do
primeiro versus jogadores adicionais são medidos por observação.

## Accounting e budget

Uma campanha real nova exige `--max-api-points`; estimativa nunca vira autorização. O teto é
persistido e verificado antes de cada observação, além dos limites e da reserva interativa do
`WclClient`. Pontos WCL são derivados do contador de queries quando a API não oferece medição
mais precisa e ficam marcados como estimados. `experiment-status --campaign` apresenta total,
pontos por observação/fight, médias de primeiro/adicional, cache hit rate, páginas reutilizadas e
razão actual/planned. O accounting acumulado não é duplicado em resume.

A estimativa de ~6,7 pontos/observação ainda não é um resultado medido.

## Dry-run e dataset

O dry-run congela ou valida o plano e mostra identidade, dimensões, custo, ordem e potencial de
sharing. Ele não instancia o collector: consumo WCL é zero.

```powershell
botgitgud experiment-collect --partition 4 --difficulty 5 `
  --max-observations 1200 --max-api-points 8040 --dry-run
```

Observações `completed` apontam para o log persistido e podem ser filtradas pelas chaves exatas
da campanha no builder experimental. O contrato temporal e a allowlist de SAE.3 continuam sendo
a autoridade contra leakage.

Com autorização explícita futura, o comando real seria:

```powershell
botgitgud experiment-collect --partition 4 --difficulty 5 `
  --max-observations 1200 --max-api-points 8040
```

Resume usa `botgitgud experiment-collect --campaign <campaign_id>` e nunca recria a amostra.

A campanha de 1.200 observações não foi executada durante a implementação deste módulo.
