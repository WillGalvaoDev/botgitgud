# Incidente 001 — label e sharing da coleta experimental

## Resumo

A primeira execução real da campanha `exp-840b1ef99d76c33c8a0b` parou pelo piso protegido do
rate limiter (`rate_limit_budget`). Foram consumidos 3.880 pontos estimados, 138 observações
foram rejeitadas como `percentile_missing` e 1.062 permaneceram pending. Nenhuma observação foi
completed ou failed. O plano congelado continuou com 1.200 ordinals, difficulty 5 e partition 4.

## Causa raiz do label

O Stage B havia persistido `rankPercent` de `reportData.report.rankings`, identificado por report,
fight, player, difficulty e partition. Mesmo assim, o collector pediu ao `LogFetcher` que tentasse
redescobrir o percentile por `character.encounterRankings`. Essa consulta não representa a
partition histórica congelada e nenhum dos 138 report/fight foi encontrado. Os logs ficaram com
`percentile=NULL` e foram rejeitados sistematicamente.

Para campanhas experimentais, o `rank_percent` do frozen plan agora é a fonte autoritativa do
target. O payload ainda precisa coincidir em report, fight, player, spec, encounter, difficulty e
partition. Divergência em qualquer dimensão continua sendo rejection; o candidato nunca é
substituído. O fluxo interativo pode continuar usando `character.encounterRankings`.

## Ausência de sharing na tentativa

A tentativa defeituosa tocou 23 fights: primeiros players custaram em média 28,70 pontos e 115
players adicionais, 28,00. Foram registrados zero cache hits e zero páginas reutilizadas. O custo
terminal observado foi 3.880 / 138 = 28,12 pontos por observação. Esse número descreve somente a
implementação defeituosa e **não é custo definitivo do pipeline corrigido**.

A revisão das queries mostrou que meta/Summary, Casts, DamageDone, Resources e report rankings
são fight-wide e filtrados localmente. Buffs/Debuffs usam `sourceID` e são player-specific. O
backend corrigido cacheia apenas o primeiro grupo. Em fake transport de uma página e seis players,
as requests caem de 42 (7 por player, sem a consulta antiga de percentile) para 17: sete no
primeiro player e duas em cada adicional. Isso equivale estruturalmente a 34/6 = 5,67 pontos por
observação no fixture. É uma **estimativa sintética**, não medição WCL de produção; paginação
eleva o custo do primeiro player.

## Retry e accounting

Resultados terminais agora possuem ledger em `experiment_observation_attempts`. A reabertura do
incidente aceita exclusivamente rejection `percentile_missing` com label congelado entre 0 e 100.
Ela muda apenas o estado atual para pending e incrementa `reopened_count`; campaign ID, ordinal,
frozen observation, tentativas e pontos permanecem. `partition_mismatch`, completed e quaisquer
outros motivos não são reabertos.

Antes da preparação local: 0 completed, 0 failed, 138 rejected, 1.062 pending e 3.880 pontos.
Depois da reabertura elegível esperada: 0 completed, 0 failed, 0 rejected, 1.200 pending e os mesmos
3.880 pontos. O custo desperdiçado não foi apagado nem escondido.

## Interrupção e escopo da correção

O WCL reportou 922 pontos globais restantes, abaixo do piso protegido de 1.000, e o collector
parou corretamente. Esta correção não reduziu o piso, não aumentou o teto acumulado de 8.040,
não replanejou, não fez resume, não chamou WCL e não executou ML.
