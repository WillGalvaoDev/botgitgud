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
partition, versão do planner, versão do schema de features, máximo de observações e o universo
representado pela lista ordenada de chaves naturais. A mesma seleção e os mesmos parâmetros
científicos produzem a mesma identidade; campanhas diferentes não compartilham estado.

API budget is an execution policy and is not part of ExperimentCampaign identity.

## Checkpoint, resume e idempotência

Cada observação passa por `pending`, `collecting` e um estado terminal: `completed`, `failed` ou
`rejected`. Tentativas, horários, pontos, motivo, referência de saída e métricas de cache ficam
persistidos. `experiment_observation_attempts` conserva cada resultado terminal e seu custo.
Uma retomada converte um `collecting` interrompido em `pending`; estados terminais não são
coletados novamente. A única exceção é uma reabertura administrativa explícita de motivo
autorizado. O incidente 001 autoriza somente `percentile_missing`: ordinal, campaign, observação
congelada, contador de tentativas e custo histórico permanecem imutáveis. Não há replacement.

Falhas operacionais distinguem player/fight indisponível e erro de API. Rejeições distinguem
report, fight, player, partition, difficulty, encounter ou spec divergente. O label experimental
é `rank_percent` congelado pelo planner a partir de `report.rankings`; o collector não consulta
`character.encounterRankings` para redescobri-lo. Exaustão do budget
para normalmente, preserva o checkpoint e define `stopped_reason`.

## Robustez do refresh de rate limit

Durante a primeira execução real da campanha experimental, um timeout transitório de rede durante
o refresh periódico de orçamento expôs um caminho de falha sem tratamento. O refresh de rate limit
agora tenta novamente falhas transitórias e falha fechado quando o estado de rate limit não pode
ser estabelecido.

Concretamente: `WclClient._refresh_rate_limit` fazia uma chamada HTTP direta sem a política de
retry/backoff que o caminho normal de `query()` já tinha. Uma falha de transporte (timeout de
conexão, timeout de leitura, erro de conexão) nesse ponto se propagava sem tipo definido e
derrubava o processo inteiro — diferente de uma resposta HTTP não-200 do mesmo endpoint, que
sempre teve um caminho de degradação suave (log + retorno, sem exceção) porque o próprio `query()`
acaba revelando uma indisponibilidade sistêmica real por conta própria.

O refresh agora reusa exatamente a mesma política de tentativas e backoff de `query()`
(`max_attempts`, `_backoff_delay`), mas só para falhas de transporte — o comportamento de resposta
não-200 permanece inalterado. Se as tentativas se esgotarem, o cliente levanta
`RateLimitCheckFailed` (`ApiError`) em vez de propagar a exceção de transporte crua. Isso é
distinto de `RateLimitBudgetExceeded`: aquela significa "sabemos que o orçamento está baixo";
esta significa "não sabemos o estado, então não presumimos que existe orçamento" — falha fechada.
`refresh_budget()` (usado pelo agendador de jobs do Discord) ganha a mesma proteção; ele continua
nunca levantando `RateLimitBudgetExceeded`, mas agora pode levantar `RateLimitCheckFailed` após
esgotar as tentativas — um `ApiError`, já tratado pelo `except ApiError` existente do worker loop
sem qualquer mudança nesse código.

No `ExperimentCollector`, `RateLimitCheckFailed` é tratado exatamente como `RateLimitBudgetExceeded`
já era: a observação em `collecting` volta imediatamente para `pending` (mesmo `reset_collecting`
existente, sem inventar tentativas nem pontos), a execução para com `stopped_reason =
"rate_limit_refresh_failed"` — nunca `budget_exhausted` nem `rate_limit_budget`, já que nenhum dos
dois foi de fato observado — e nenhuma observação posterior é tentada. `completed`, `failed` e
`rejected` anteriores permanecem exatamente como estavam; accounting histórico não é alterado nem
inventado.

Toda execução de `run()` também marca `stopped_reason = "in_progress"` antes de processar qualquer
observação, sobrescrito ao final pelo motivo real. Isso garante que um valor antigo (de uma
execução anterior) nunca seja confundido com o resultado da execução atual, mesmo se um caminho de
falha ainda não descoberto voltar a derrubar o processo sem tratamento.

## Localidade de fight e event sharing

A seleção mantém o ordinal original. A ordem de execução agrupa observações do mesmo fight pela
ordem da primeira ocorrência e conserva o ordinal dentro do fight. Isso não muda o conjunto
selecionado. Um `FightSession` reutiliza respostas cuja query é realmente fight-wide: meta e
Summary, páginas de Casts, DamageDone e Resources, e `report.rankings`. A chave contém query e
todos os argumentos (report, fight, intervalo e shape). Buffs e Debuffs incluem `sourceID` e
continuam player-specific. Cache hit e `pages_reused` só são registrados quando uma resposta
HTTP existente evita uma chamada real. O cache é em memória e limitado a uma execução; após
interrupção, checkpoints persistem, mas respostas cruas não são serializadas.

## Accounting e budget

Uma campanha real nova ou retomada exige `--max-api-points`; estimativa nunca vira autorização.
O argumento representa o **teto total acumulado autorizado da campanha**, não uma franquia nova
por processo. Assim, após consumir 8.040 pontos, retomar com teto 9.000 permite no máximo 960
pontos adicionais. Um teto menor que o consumo histórico produz `budget_exhausted`, sem chamada,
sem apagar accounting e sem alterar o campaign ID. O teto é persistido e verificado antes de cada
observação, além dos limites e da reserva interativa do `WclClient`. Pontos WCL são derivados do contador de queries quando a API não oferece medição
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

Observações `completed` apontam para o log persistido. Em materialização por `campaign_id`, o
builder junta o log à observação congelada e usa seu `rank_percent` como `y_rank_percent`; eventos
e atributos do log são somente features/contexto. O contrato temporal e a allowlist de SAE.3
continuam sendo a autoridade contra leakage. `alignment_score` e `total_parses` continuam fora.

Com autorização explícita futura, o comando real seria:

```powershell
botgitgud experiment-collect --partition 4 --difficulty 5 `
  --max-observations 1200 --max-api-points 8040
```

Resume usa `botgitgud experiment-collect --campaign <campaign_id> --max-api-points <teto-total>`
e nunca recria a amostra.

A primeira tentativa da campanha de 1.200 observações é registrada no incidente 001. A correção
desse incidente não executou resume nem fez chamadas WCL.
