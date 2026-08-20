# Arquitetura multi-target da Fase 4

## Problema e objetivo

O gate de 5.000 observações é definido por `(spec, encounter, difficulty, partition)`. Com muitas
specs DPS, encontros, difficulties e partitions, um singleton criaria acoplamento e risco de
contaminação estatística. O produto continua analisando qualquer spec DPS suportada; um modelo da
Fase 4 é capability adicional, não pré-requisito.

**Suporte multi-target NÃO significa que todos os targets já possuem um modelo Fase 4.**

**A ausência de um modelo Fase 4 nunca impede a análise das Fases 0–3.**

## Auditoria anterior

- `SpecId` já identificava classe/spec da WCL; o scope gate contém as 25 specs DPS.
- `FightRef`, `logs`, cohorts e `RunManifest` já carregavam encounter, difficulty e partition.
- `LogFetcher.fetch(report, fight, player)` descobre o target no report e, desde T-DG.0, obtém a
  partition por `report.rankings`; não depende de classe específica.
- A+B já eram genéricos: `discovery_fights` contém encounter/difficulty/partition e
  `discovery_targets`, class/spec. O join tem todas as dimensões exigidas.
- `dataset-status` já tinha consulta específica e ranking global, mas não mostrava capabilities.
- Não existia carregador/resolver de modelo; a Fase 4 estava apenas prevista em T4.1–T4.4.
- DuckDB, IDs de cohort, `RunManifest.code_version` e paths hive já forneciam padrões de persistência
  e versionamento aproveitáveis, sem infraestrutura externa.

## Arquitetura implementada

### Phase4Target

Combina `SpecId`, `encounter_id`, `difficulty` e `partition`. Valida números, exige partition
explícita, remove espaços da identidade WCL e produz ID ASCII path-safe, por exemplo
`DeathKnight/Unholy/3182/5/3`. É frozen/hashable, serializável por `to_dict()` e parseável. Não usa
display name localizado do boss.

### Registry

`Phase4ModelRegistry` mantém no DuckDB uma linha por target, com versões de modelo/dataset, datas de
treino, MAE, número de observações, path e status `unavailable`, `collecting`, `ready` ou `invalid`.
O registry vazio funciona. Paths são relativos e sem travessia. Nenhum modelo foi criado.

### Resolver e isolamento

`Phase4ModelResolver.resolve(target)` faz lookup exato e retorna `found`, `unavailable`, `invalid` ou
`incompatible`. `ready` exige versões e path completos. As dimensões redundantes da linha são
reconstruídas e comparadas ao pedido, detectando corrupção. Não há fallback entre spec, encounter,
difficulty ou partition.

### Fallback Fases 0–3

Após obter a partition corrente, o pipeline cria o target, resolve a capability e anexa
`phase4_resolution` ao `AnalysisResult`. Não executa inferência nem altera recomendações ou relatório.
Sem resolver ou registro exato, retorna `unavailable` e as Fases 0–3 terminam normalmente.

## Dataset status e Estágio C

O modo global mostra candidatos, ingeridos, faltantes para 5.000, gate e status de modelo por target.
A consulta específica continua exigindo todas as dimensões juntas.

`BackfillPlanner` recebe `BackfillPlan(target=..., required_observations=5000)` e consulta apenas o
warehouse. Calcula candidatos conhecidos, já ingeridos, faltantes, fights/jogadores a processar,
estimativa de pontos e período coberto. O filtro é exato. O planner não recebe client nem
`LogFetcher`, portanto não dispara rede ou coleta. Estágio C não foi executado.

## Exemplos

- `DeathKnight/Unholy/3182/5/3`
- `DeathKnight/Frost/3182/5/3`
- `Priest/Shadow/3179/4/4`

Um registro `ready` do primeiro ID nunca satisfaz os outros. Cada target evolui independentemente.

## Limitações e decisões abertas

- O ranking global usa contagem operacional; o veredito completo, inclusive split temporal e
  rejeições, permanece na consulta específica.
- A estimativa usa 17 pontos por extração conforme o plano; o custo real pode variar.
- Artifact loader e inferência pertencem a T4.2, após gate e T4.1.
- Continua aberta, sujeita a aprovação e validação estatística, a escolha entre modelo por target,
  spec, encounter, global ou hierárquico. Nenhuma generalização foi implementada.
