# M2.2 — Seleção da população por métrica

**Unidade:** M2.2 (macro M2 — Comparabilidade por métrica). **Status:** SPEC_READY.
Data: 2026-09-22. Autoridade normativa: [M0](m0-methodology-contract.md) (C01–C10),
[SPEC M1](m1-specification.md) (MILESTONE_CLOSED), [SPEC M2.1](m2-1-specification.md)
(MILESTONE_CLOSED), [roadmap M2–M6](methodology-roadmap-m2-m6.md),
[workflow](milestone-workflow.md).

Este documento fecha B04 no que é necessário e suficiente para M2.2. Não altera
M0/M1/M2.1, não antecipa M2.3 e não autoriza decisões metodológicas adicionais ao
executor. O implementador deve persistir este documento verbatim em
`docs/m2-2-specification.md` e registrá-lo no índice `docs/README.md`.

Linha de produto: workspace `BotGITGUD-M2.1-product`, commit de fechamento de M2.1
`edba6e62d144811724efe3a2f9bdf0d6023c64ee`. É a única árvore que contém a interface
`analysis/reference_eligibility.py` exigida como dependência.

## 1. Objetivo

Para cada **métrica contratada em M1 §4.2**, selecionar de forma reproduzível a
população de referências efetivamente usada por aquela métrica, a partir das
referências **basicamente elegíveis** por M2.1, das covariáveis admitidas nesta SPEC
e de um ladder de relaxamento explícito; e distinguir a população **descritiva** da
**aspiracional**, com identidades, finalidades, N, exclusões e suficiência próprios.

A seleção é condição necessária de comparação. Ela **não** é ajuste estatístico,
não é prova de causalidade e não torna comparável uma medida bloqueada por M1 (C05,
C07). Uma lista de covariáveis relaxadas não é, em nenhuma hipótese, prova de
controle de confundimento.

## 2. Escopo e fora de escopo

Incluído:

- Módulo puro e determinístico de seleção de população por métrica, com versão própria.
- Matriz métrica × covariável (§5), ladder de relaxamento (§6) e política de
  suficiência (§8), todos fechados nesta SPEC.
- Saída por métrica: população descritiva, população aspiracional, membros, exclusões
  por motivo, passos de relaxamento aplicados, limitações declaradas e estados de
  suficiência.
- Artefato de sensibilidade de B04 (§10.3), somente leitura.
- Testes e pacote de evidências da unidade.

Fora de escopo (não implementar, não preparar “ganchos” especulativos):

- Wiring em `analysis/pipeline.py`, `cohort_match.py`, `benchmark_reference.py`,
  contrato de relatório e persistência de proveniência da política — **M2.3**.
- Alteração de comportamento de `match_cohort` em produção, de `CohortCriteria`,
  de `cohort_id()` ou de qualquer cache/pool existente.
- Interpretação estatística de grade, quantil, CI, FDR ou confiança — **M3 / B06**.
- Materialidade, priorização, oportunidades, texto CLI/Discord, ML.
- Causalidade, calibração de grades e novo desenho de aquisição de dados
  (fora de escopo explícito do roadmap para M2.2).
- Novas queries à API, alteração de fetchers, reescrita de `data/raw`, migração de
  Store, campanha ou treinamento.
- Redefinição de qualquer fórmula, denominador, estado ou constante de M1/M2.1.
- Streams e medidas ainda não contratadas por métrica (`active_time_pct`, `deaths`,
  `downtime_s`, `resource_waste`, procs, setup) — **M3.1**.

## 3. Dependências de interface

M2.2 consome, sem alterar:

| Origem | Interface consumida | Uso |
|---|---|---|
| M2.1 | `evaluate_references(target, references) -> ReferenceEligibilityPopulation` | Estágio A: admissão básica |
| M1 | `metric_observations.observe(log, sid, metric, catalog) -> MetricObservation` | Estágio C: disponibilidade da métrica e razão de exclusão |
| M1 | `measurement.damage_reference_id(log)` | Identidade única de referência |
| T0.8 | `analysis/cohort.py`: `COHORT_MIN_HARD`, `POSITIONAL_BAND_PCT`, `SANITY_BAND_PCT` | Piso e bandas; **reutilizados, nunca redefinidos** |
| T0.8 | `analysis/grading.py`: `MIN_N_FOR_GRADING` | Limiar de suficiência para grade |
| T2.1 | `analysis/cohort_match.py`: `ITEM_LEVEL_BAND`, `TIER_PIECES_BAND`, `DURATION_BANDS_PCT`, `DURATION_FLOOR_S` | Bandas de covariável; **reutilizadas, nunca redefinidas** |
| T2.1 | `domain/external_buffs.EXTERNAL_OFFENSIVE_IDS` | Conjunto autoritativo de buffs externos ofensivos |
| SA/EB | `analysis/benchmark_reference.select_benchmark_reference`, `REFERENCE_MIN_N`, `REFERENCE_FLOOR_PERCENTILE` | Estágio D: ordenação e corte da cauda superior |

Nenhuma dessas funções pode ser modificada, copiada com semântica divergente ou
reimplementada em M2.2. Se uma delas se mostrar insuficiente, o executor registra
`TECH_BLOCK`; não escolhe uma interpretação alternativa.

## 4. Métricas contratadas

Exatamente as seis métricas de M1 §4.2, tal como declaradas em
`metric_observations.UNITS`. A população é definida por `metric_id`
(`f"{metric}:{spell_id}"`), nunca por métrica agregada e nunca por luta.

| `metric` | Natureza | Classe de sensibilidade |
|---|---|---|
| `gross_ability_dps` | Taxa de amplitude (`D_a/T`) | AMPLITUDE |
| `damage_per_event` | Amplitude por evento (`D_a/hits_a`) | AMPLITUDE |
| `damage_events_per_second` | Taxa de frequência (`hits_a/T`) | FREQUÊNCIA |
| `player_casts_per_minute` | Taxa posicional de cast (`60·casts/T`) | POSICIONAL |
| `aura_uptime_fraction` | Fração temporal em [0,1] | TEMPORAL |
| `gross_damage_share_pct` | Composicional (`100·D_a/D_bruto`) | COMPOSICIONAL |

A classe de sensibilidade é o único fundamento admitido da matriz §5. Ela descreve
por qual mecanismo observável a medida se desloca; não afirma efeito causal (C05).

## 5. Decisão B04 — matriz métrica × covariável

### 5.1 Covariáveis admitidas como critério de inclusão

Somente as quatro abaixo, todas já presentes no domínio:

| Covariável | Campos autoritativos | Predicado de igualdade/banda |
|---|---|---|
| `duration` | `fight.duration_s` (ambos os lados) | `abs(cand - alvo) <= max(alvo * pct, DURATION_FLOOR_S)`, com `pct` do ladder §6.2 |
| `item_level` | `build.item_level` | `abs(cand - alvo) <= ITEM_LEVEL_BAND` |
| `tier_pieces` | `build.tier_pieces` | `abs(cand - alvo) <= TIER_PIECES_BAND` |
| `external_buffs` | `build.external_buffs & EXTERNAL_OFFENSIVE_IDS` | Igualdade exata dos conjuntos intersectados |

### 5.2 Covariáveis explicitamente **não** admitidas como inclusão

| Covariável | Decisão | Fundamento |
|---|---|---|
| `talent_cluster` / `build.setup` | Nunca filtra, nunca relaxa | Fronteira aprovada EC.3/M4: a coorte de execução é independente do setup do jogador; setup é análise separada. M2.2 preserva a decisão, não a reabre |
| `has_augmentation` | Covariável **declarada de ajuste**, nunca filtro | Mesma fronteira EC.3/M4; a presença de augmentation ofensiva já é observada dentro de `EXTERNAL_OFFENSIVE_IDS` |
| `dps`, `percentile` | Proibida como inclusão | Seleção pelo desfecho. Só a população aspiracional (§7) pode ordenar por desfecho, e só com a declaração obrigatória correspondente |
| Idade de calendário, `start_time_ms`, ordem de coleta | Proibida | C07 e M2.1 §3: data não é versão de mecanismo. O eixo temporal admissível é `partition`, já decidido em M2.1 |
| `role`, `server`, `character_name` | Proibida | Não são covariáveis de comparabilidade de execução |
| Qualquer campo não listado em §5.1 | Proibida | Lista fechada em v1 |

Ampliar o conjunto exige **nova versão de política** (`metric-population-v2`), nunca
reinterpretação silenciosa de v1.

### 5.3 Matriz normativa

`REQ` = exigida como inclusão no nível estrito; `REL` = exigida e relaxável pelo
ladder §6; `NA` = não admitida para essa métrica (nunca filtra, nunca é relaxada,
nunca aparece em `relaxed`).

| `metric` | `duration` | `external_buffs` | `item_level` | `tier_pieces` |
|---|---|---|---|---|
| `gross_ability_dps` | REQ (ladder amplo) | REQ, não relaxável | REL | REL |
| `damage_per_event` | REQ (ladder amplo) | REQ, não relaxável | REL | REL |
| `damage_events_per_second` | REQ (ladder amplo) | REQ, não relaxável | REL | REL |
| `player_casts_per_minute` | REQ (ladder posicional) | REQ, não relaxável | NA | NA |
| `aura_uptime_fraction` | REQ (ladder amplo) | REQ, não relaxável | NA | NA |
| `gross_damage_share_pct` | REQ (ladder amplo) | REQ, não relaxável | REL | REL |

Fundamentos registrados, por célula não trivial:

1. **`duration` é REQ em todas as métricas.** Mesmo as métricas que não são taxas
   (`damage_per_event`, `gross_damage_share_pct`, `aura_uptime_fraction`) mudam de
   composição com o número de fases e de janelas de cooldown percorridas. A banda
   larga exclui apenas kills estruturalmente diferentes; não pretende “aproximar”
   execuções (docstring de `analysis/cohort.py`).
2. **`player_casts_per_minute` usa o ladder posicional (§6.2).** É a métrica cujo
   valor está atado a *quando* na luta as coisas acontecem e ao número de janelas
   disponíveis; `POSITIONAL_BAND_PCT` existe exatamente para ela e é o teto.
3. **`external_buffs` é REQ e não relaxável em todas.** Buffs externos ofensivos
   deslocam tanto amplitude (dano) quanto frequência (haste, procs). `cohort_match`
   já registra a decisão de produto de que buffs externos permanecem casados mesmo
   ao custo de parar no piso; M2.2 preserva essa decisão.
4. **`item_level`/`tier_pieces` são NA em `player_casts_per_minute` e
   `aura_uptime_fraction`.** Nenhuma das duas é medida de amplitude: contagem de
   casts do jogador e fração de presença de aura não escalam com o valor do
   equipamento pelo mecanismo que a banda de ilvl observa. Exigi-las reduziria N sem
   remover confundimento identificável.
5. **`item_level`/`tier_pieces` são REL nas demais.** São covariáveis de amplitude
   medidas e de viés reconhecidamente menor que buffs e duração, e por isso são as
   primeiras a ceder no ladder — mesma ordem relativa já aprovada em
   `DEGRADATION_ORDER_V2`.

Esta matriz é política de **comparabilidade declarada**, versionada e revisável por
evidência. Ela não afirma que a população selecionada está ajustada para as
covariáveis, nem que as covariáveis NA são irrelevantes: afirma que, em v1, elas não
são critério de inclusão para aquela métrica, e isso é declarado ao consumidor.

### 5.4 Valores ausentes de covariável (C06)

Sem defaulting, em nenhum sentido.

| Situação | Efeito |
|---|---|
| Covariável REQ/REL com valor desconhecido **na referência** | Referência não admitida no nível estrito, com motivo `ITEM_LEVEL_UNKNOWN` / `TIER_PIECES_UNKNOWN`. Volta a ser admissível somente se essa covariável for relaxada pelo ladder |
| Covariável REQ/REL com valor desconhecido **no alvo** | A covariável é **inadmissível** nesta análise: não filtra ninguém, não entra em `matched`, não entra em `relaxed`, e a população declara `TARGET_ITEM_LEVEL_UNKNOWN` / `TARGET_TIER_PIECES_UNKNOWN` |
| `duration_s` não finita ou `<= 0` em qualquer lado | Já é `INELIGIBLE` por M2.1 (`INVALID_DURATION`); M2.2 não reavalia nem repara |
| `external_buffs` vazio | Conjunto vazio é um valor **observado**, não desconhecido; compara normalmente |

Abstenção (covariável inadmissível) e relaxamento (passo de política) são estados
distintos e nunca podem ser reportados um pelo outro.

## 6. Ladder de relaxamento

### 6.1 Regra de parada

- O ladder existe **exclusivamente** para alcançar o piso de viabilidade
  `COHORT_MIN_HARD` (8) da própria métrica, contado conforme §8.1.
- Assim que `n >= COHORT_MIN_HARD`, o ladder para. **Nunca** se relaxa para alcançar
  `MIN_N_FOR_GRADING` (15), nem qualquer alvo de N maior.
- Se, esgotado o ladder, `n < COHORT_MIN_HARD`, a população é entregue como está,
  com o estado de suficiência correspondente. Não há modo permissivo, cota, “melhor
  esforço” nem exceção.
- Divergência declarada para M2.3: `match_cohort` possui, acima do piso, um estágio
  de crescimento “low bias” até `COHORT_STRETCH_N`. M2.2 **não** o reproduz, porque
  gastar covariável por N acima do piso troca viés por tamanho sem justificativa por
  métrica. A reconciliação entre as duas políticas é trabalho explícito de M2.3 e
  deve ser resolvida lá, não silenciosamente em nenhum dos lados.

### 6.2 Ladder de duração

Derivado das constantes vigentes, sem novos números:

- Ladder amplo: `DURATION_BANDS_PCT + (SANITY_BAND_PCT,)` = `(0.07, 0.12, 0.20, 0.35)`.
- Ladder posicional: `tuple(b for b in DURATION_BANDS_PCT if b <= POSITIONAL_BAND_PCT)`
  = `(0.07, 0.12)`.

A duração **alarga** por passos do ladder; nunca é descartada como critério e nunca
ultrapassa o último elemento do ladder da sua métrica.

### 6.3 Ordem de degradação

Ordem fixa, aplicada um passo por vez, reavaliando a população inteira após cada passo:

1. `tier_pieces` (se REL e admissível)
2. `item_level` (se REL e admissível)
3. `duration`: um alargamento de banda por passo, até o fim do ladder da métrica

`external_buffs` nunca é relaxada. Covariáveis `NA` e inadmissíveis nunca entram.
A ordem preserva a ordem relativa já aprovada em `DEGRADATION_ORDER_V2`
(`tier_pieces` antes de `item_level`, duração por último).

### 6.4 Registro obrigatório por passo

Cada passo aplicado registra, sem texto livre: a covariável, a regra aplicada
(`DROP_FILTER` ou `WIDEN_BAND` com o índice do ladder), `n` antes, `n` depois e os
`reference_id` admitidos por aquele passo. Passos que não alteram a população são
registrados com delta zero; não são omitidos.

O relaxamento é **irreversível dentro da avaliação** e sua sequência é função apenas
da política e dos dados, nunca do efeito sobre o resultado da métrica. É proibido
escolher nível de relaxamento por comparação de resultados (M1 D-M1-01).

## 7. Populações descritiva e aspiracional

Identidades, finalidades e números **separados**. Nunca reutilizar o N, a média, os
membros ou as exclusões de uma como se fossem da outra (M1 D-M1-01).

### 7.1 DESCRIPTIVE

- Definição: todas as referências admitidas para o `metric_id` no nível final do
  ladder, cuja observação daquela métrica é `AVAILABLE` (§8.1).
- Finalidade: descrever o campo comparável da própria métrica.
- É a única população admitida como distribuição da métrica para quantil/grade a
  jusante. A interpretação desse quantil permanece decisão de M3/B06; M2.2 não a antecipa.

### 7.2 ASPIRATIONAL

- Definição: subconjunto da população DESCRIPTIVE **da mesma métrica**, obtido por
  `select_benchmark_reference(target, descriptive_members)`, reutilizado sem alteração
  (cauda superior condicional, piso `REFERENCE_FLOOR_PERCENTILE`, fallback
  `max(REFERENCE_MIN_N, ceil(n/3))`).
- Ordenação: pelo **desfecho** (`log.dps`), nunca pelo valor da própria métrica.
  Ordenar pela própria métrica produziria por construção um déficit; é proibido.
- Guarda obrigatória antes da chamada: `select_benchmark_reference` trata `dps is
  None` como `0.0`. M2.2 **deve** excluir previamente todo membro sem `dps` finito,
  com motivo `ASPIRATIONAL_ORDER_UNAVAILABLE`, e nunca passar tais membros adiante.
- Se restarem menos de `REFERENCE_MIN_N` (8) membros ordenáveis, a população
  aspiracional é `UNAVAILABLE`, com `members = ()`, `n = 0` e limitação
  `ASPIRATIONAL_UNAVAILABLE`. Não fabricar cauda, não reduzir o piso, não reaproveitar
  a descritiva.
- Limitações sempre declaradas quando disponível: `ASPIRATIONAL_SELECTED_ON_OUTCOME`
  e `ASPIRATIONAL_ORDERED_BY_WCL_DPS`. A segunda registra que a ordenação usa uma
  medida WCL distinta das medidas reconciliadas de M1 (C01).
- A população aspiracional **não** fornece quantil, grade, N ou média para a
  descritiva, e não é usada para fechar nenhuma contabilidade de M1.

## 8. Política de suficiência

### 8.1 O N é da própria métrica

`n` de uma população é o número de referências que, simultaneamente:

1. são `ELIGIBLE` na avaliação M2.1 (nem `INELIGIBLE` nem `INDETERMINATE`);
2. satisfazem as covariáveis da métrica no nível final do ladder;
3. possuem observação `AVAILABLE` daquele `metric_id` por `metric_observations.observe`.

Nenhum outro N pode ser reportado como N da métrica. É proibido usar contagem de
logs casados, N de outra métrica, N de outro `spell_id` ou N da coorte global.

### 8.2 Estados reportados

| Condição | Estado | Limitação declarada |
|---|---|---|
| `n < COHORT_MIN_HARD` (8) | `INSUFFICIENT` | `INSUFFICIENT_FOR_COMPARISON` |
| `COHORT_MIN_HARD <= n < MIN_N_FOR_GRADING` (15) | `SUFFICIENT_FOR_COMPARISON` | `INSUFFICIENT_FOR_GRADING` |
| `n >= MIN_N_FOR_GRADING` | `SUFFICIENT_FOR_GRADING` | — |

São **estados reportados**, não correções: M2.2 não levanta exceção, não relaxa além
do ladder, não altera fluxo de produção e não converte insuficiência em ausência de
dado. Disponibilidade da medida e suficiência de amostra permanecem independentes
(M1 §4.2). N elevado não é prova de ajuste estatístico.

## 9. Contratos de dados

Nomes de referência; a organização em helpers pode variar, os significados não.
Implementar em `src/botgitgud/analysis/metric_population.py`, com dataclasses
frozen/slots e `StrEnum`, no padrão do repositório.

```text
METRIC_POPULATION_POLICY_VERSION = "metric-population-v1"

PopulationKind:      DESCRIPTIVE | ASPIRATIONAL
Covariate:           DURATION | EXTERNAL_BUFFS | ITEM_LEVEL | TIER_PIECES
CovariateRole:       REQUIRED | RELAXABLE | NOT_ADMITTED | INADMISSIBLE_TARGET_UNKNOWN
SufficiencyState:    INSUFFICIENT | SUFFICIENT_FOR_COMPARISON | SUFFICIENT_FOR_GRADING
                     | UNAVAILABLE            # apenas populações aspiracionais vazias
RelaxationRule:      DROP_FILTER | WIDEN_BAND

RelaxationStep:
    covariate: Covariate
    rule: RelaxationRule
    band_index: int | None          # WIDEN_BAND: índice no ladder da métrica
    band_pct: float | None
    n_before: int
    n_after: int
    admitted_ids: tuple[str, ...]   # ordenados

MetricPopulation:
    policy_version: str
    eligibility_policy_version: str        # propagado de M2.1, não recalculado
    metric_id: str                         # "<metric>:<spell_id>"
    kind: PopulationKind
    target_id: str
    members: tuple[str, ...]               # ordenados por reference_id
    n: int
    sufficiency: SufficiencyState
    matched_covariates: tuple[Covariate, ...]
    relaxed_covariates: tuple[Covariate, ...]
    declared_covariates: tuple[Covariate, ...]     # ajuste declarado, nunca filtro
    relaxation_steps: tuple[RelaxationStep, ...]
    final_duration_band_pct: float
    excluded_reasons: Mapping[str, tuple[str, ...]]    # reference_id -> motivos
    declared_limitations: tuple[str, ...]

MetricPopulationSet:
    policy_version: str
    metric_id: str
    descriptive: MetricPopulation
    aspirational: MetricPopulation

select_metric_population(
    target: PlayerLog,
    references: Sequence[PlayerLog],
    *,
    metric: str,
    spell_id: int,
    eligibility: ReferenceEligibilityPopulation,
    catalog: SpellCatalog | None,
) -> MetricPopulationSet

select_metric_populations(
    target: PlayerLog,
    references: Sequence[PlayerLog],
    *,
    eligibility: ReferenceEligibilityPopulation,
    catalog: SpellCatalog | None,
    metric_ids: Sequence[tuple[str, int]] | None = None,
) -> Mapping[str, MetricPopulationSet]     # metric_id -> conjunto
```

`eligibility` é entrada obrigatória e não é recomputada dentro de M2.2: a decisão
básica pertence a M2.1 e deve ser propagada, inclusive `policy_version` e
`declared_limitations`. Se `eligibility.target_id` divergir do alvo recebido, a
função levanta `ValueError`; não concilia por suposição.

Pureza: sem I/O, sem rede, sem relógio, sem aleatoriedade, sem variável de ambiente,
sem estado global, sem mutação de `PlayerLog`, caches, Store ou arquivos históricos.

## 10. Códigos fechados

### 10.1 Exclusão por referência (`excluded_reasons`)

Estágio A (propagados de M2.1, sem reescrita): `BASIC_ELIGIBILITY_INELIGIBLE`,
`BASIC_ELIGIBILITY_INDETERMINATE`, acompanhados dos motivos originais de M2.1 §6,
preservados verbatim e na ordem original.

Estágio B (covariáveis): `DURATION_BAND_MISMATCH`, `EXTERNAL_BUFFS_MISMATCH`,
`ITEM_LEVEL_BAND_MISMATCH`, `ITEM_LEVEL_UNKNOWN`, `TIER_PIECES_BAND_MISMATCH`,
`TIER_PIECES_UNKNOWN`.

Estágio C (disponibilidade da métrica): o motivo é o código de
`MetricObservation.reasons` de M1, **propagado verbatim** (por exemplo
`UPTIME_NOT_OBSERVED`, `PARTIAL_CAST_COLLECTION`, `PLAYER_MECHANISM_NOT_OBSERVED`).
É proibido criar um código M2.2 equivalente, traduzir ou agrupar. Observação com
status diferente de `AVAILABLE` e sem razão é erro de M1, não é reparado aqui.

Estágio D (aspiracional): `ASPIRATIONAL_ORDER_UNAVAILABLE`,
`ASPIRATIONAL_NOT_IN_UPPER_TAIL`.

### 10.2 Limitações de população (`declared_limitations`)

Herdadas de M2.1 e **sempre preservadas**: `TARGET_ATTEMPT_NOT_KILL`,
`HOTFIX_COMPATIBILITY_UNVERIFIED`, `INSUFFICIENT_ELIGIBLE_REFERENCES`.

Próprias de M2.2: `INSUFFICIENT_FOR_COMPARISON`, `INSUFFICIENT_FOR_GRADING`,
`RELAXATION_APPLIED`, `COVARIATE_ADJUSTMENT_UNVERIFIED`, `SETUP_NOT_MATCHED`,
`AUGMENTATION_NOT_MATCHED`, `TARGET_ITEM_LEVEL_UNKNOWN`, `TARGET_TIER_PIECES_UNKNOWN`,
`ASPIRATIONAL_SELECTED_ON_OUTCOME`, `ASPIRATIONAL_ORDERED_BY_WCL_DPS`,
`ASPIRATIONAL_UNAVAILABLE`.

Emissão obrigatória:

- `RELAXATION_APPLIED` e `COVARIATE_ADJUSTMENT_UNVERIFIED` sempre que ao menos um
  passo do ladder tiver sido aplicado. A segunda é a declaração explícita de que
  relaxar não é ajustar (C07).
- `SETUP_NOT_MATCHED` sempre que a população for não vazia: a política v1 nunca casa
  setup/talentos, por decisão de fronteira, e isso é limite permanente declarado.
- `AUGMENTATION_NOT_MATCHED` quando ao menos um membro tiver `build.has_augmentation`
  diferente do alvo.

Códigos são ASCII estáveis, em SCREAMING_SNAKE, sem texto livre, sem interpolação de
valores e sem tradução. Ordenação determinística: estágios A→D e, dentro do estágio,
alfabética. Nenhum código novo pode ser criado pelo implementador.

### 10.3 Artefato de sensibilidade de B04

Função pura adicional, somente leitura, usada por testes e evidências:

```text
covariate_sensitivity(target, references, *, metric, spell_id, eligibility, catalog)
    -> tuple[SensitivityRow, ...]

SensitivityRow: level_index, relaxed_so_far, duration_band_pct, n, member_ids
```

A tabela reporta, para cada nível do ladder, N e membros da métrica. Ela é artefato
de transparência: **é proibido** consumi-la para escolher nível, ordem ou conjunto,
e é proibido incluir nela qualquer agregado do resultado da métrica que possa induzir
escolha por efeito. O nível final é sempre o primeiro que atinge o piso, ou o último
do ladder.

## 11. Determinismo e invariantes

- Mesmas entradas ⇒ mesma saída, byte a byte, em qualquer execução.
- **Invariância à permutação:** permutar `references` não altera membros, N,
  exclusões, passos do ladder nem seleção aspiracional. Toda saída ordenada por
  `reference_id`; `RelaxationStep.admitted_ids` idem.
- **Invariância à escassez:** referência `INELIGIBLE` em M2.1 permanece excluída em
  **todos** os níveis de relaxamento. Nenhum passo do ladder toca eixo de M2.1.
- **Não empréstimo:** a população de um `metric_id` nunca contém membro cuja
  observação daquele `metric_id` não seja `AVAILABLE`, e nunca herda N, média,
  quantil ou exclusões de outro `metric_id`.
- **Separação de identidade:** `descriptive` e `aspirational` têm `kind`, `members`,
  `n`, `sufficiency`, `excluded_reasons` e `declared_limitations` próprios;
  `aspirational.members` é subconjunto próprio ou impróprio de `descriptive.members`,
  nunca o contrário.
- Valores observados são preservados como observados: `None` permanece `None`;
  `item_level`/`tier_pieces` desconhecidos nunca viram `0` ou o valor do alvo;
  `dps` ausente nunca vira `0.0` em M2.2.
- `policy_version` e `eligibility_policy_version` acompanham cada população.

## 12. Testes e evidências obrigatórios

A matriz de casos deve ser **escrita antes da implementação** e preservada no pacote
de evidências, com resultado esperado definido por cálculo manual a partir de §5–§8.

1. **Fixtures com covariável variando isoladamente:** para cada covariável admitida e
   cada métrica, um conjunto em que apenas aquela covariável difere, verificando
   admissão/exclusão, motivo e efeito sobre N. Inclui os casos `NA` provando que a
   covariável não filtra naquela métrica.
2. **Comparação dos membros selecionados:** para cada métrica contratada, o conjunto
   de `reference_id` selecionados coincide exatamente com o esperado definido a priori
   (AC1), não apenas o N.
3. **Ladder:** casos que param no nível estrito, em cada passo intermediário e no
   último nível; caso que esgota o ladder abaixo do piso; caso que prova que o ladder
   não avança após atingir o piso, mesmo com N < 15.
4. **Invariância à escassez (AC3):** conjunto onde a única forma de atingir o piso
   seria admitir uma referência `INELIGIBLE`/`INDETERMINATE` de M2.1 — ela permanece
   excluída em todos os níveis, e a população fica `INSUFFICIENT`.
5. **Separação descritiva/aspiracional (AC4):** prova de que a aspiracional é
   subconjunto da descritiva da MESMA métrica; de que membros sem `dps` finito são
   excluídos com `ASPIRATIONAL_ORDER_UNAVAILABLE` e não afundam a ordenação; de que
   `n < REFERENCE_MIN_N` produz `UNAVAILABLE`; e de que nenhum número da aspiracional
   aparece como número da descritiva.
6. **Não empréstimo entre métricas (AC5):** conjunto em que duas métricas do mesmo
   `spell_id` têm populações e N diferentes, e um `spell_id` cuja métrica é
   indisponível no alvo — verificando que N, exclusões e insuficiência pertencem a
   cada `metric_id`.
7. **Permutação da entrada:** propriedade verificando saída idêntica sob permutação,
   incluindo empates de `dps` na seleção aspiracional.
8. **Ausentes (C06):** `item_level`/`tier_pieces` desconhecidos na referência e no
   alvo, provando a distinção entre exclusão, abstenção (`TARGET_*_UNKNOWN`) e
   relaxamento, e a ausência de defaulting.
9. **Análise de sensibilidade de B04:** `covariate_sensitivity` executada sobre as
   fixtures e sobre os metadados reais disponíveis, com a tabela por nível preservada
   nas evidências.
10. **Replay somente leitura:** censo sobre os metadados já disponíveis no ambiente
    (fixtures de `tests/fixtures/gate1_scope/` e, se presentes, Parquets de
    `data/raw`), reportando por métrica N, estado de suficiência e contagem por
    motivo, com hash de snapshot antes/depois provando que nada foi escrito. Se o
    corpus não estiver presente, registrar explicitamente a indisponibilidade e o que
    foi efetivamente coberto — **não fabricar** números.
11. **Estático e suíte:** Ruff (lint e formato) e Pyright sobre os arquivos novos;
    execução da seleção de testes pertinente offline e da suíte ampla com baseline.
    Nenhuma alteração de teste existente é esperada; regressão é reportada com
    baseline, não mascarada. A dívida preexistente
    `test_no_raw_print_calls_anywhere_in_src` permanece registrada como baseline e
    não é corrigida nesta unidade.

Evidências em `docs/m2-2-review-evidence.md`: matriz critério → teste → resultado,
tabela de casos com esperado definido a priori, tabela de sensibilidade, censo do
replay, comandos exatos, ambiente, falhas/skips e limitações residuais por marco.

## 13. Critérios de aceite

| ID | Critério |
|---|---|
| AC1 | Para cada métrica contratada de §4, a população selecionada coincide exatamente com a esperada nas fixtures, em membros e em N, reproduzível apenas a partir de §5–§8 |
| AC2 | Cada relaxamento aplicado identifica covariável, regra e efeito sobre a seleção (`n` antes/depois e IDs admitidos), na ordem fixa de §6.3, sem passo omitido |
| AC3 | Referências incompatíveis em M2.1 permanecem excluídas em todos os níveis de relaxamento; nenhum ladder, piso ou escassez reverte incompatibilidade obrigatória |
| AC4 | Populações descritiva e aspiracional mantêm identidades, finalidades, membros, N, exclusões e limitações separados; a aspiracional declara seleção por desfecho e nunca alimenta a descritiva |
| AC5 | N, exclusões e insuficiência pertencem à população do próprio `metric_id`, contados conforme §8.1, sem empréstimo de outra distribuição, de outra métrica ou da coorte global |
| AC6 | Escopo respeitado e pacote de evidências completo: sem wiring de pipeline/`cohort_match`/relatório, sem nova query, sem alterar M1/M2.1, com matriz definida antes da implementação, sensibilidade de B04, replay somente leitura, Ruff e Pyright |

## 14. Limitações residuais declaradas

- Relaxar covariável não é ajustar por ela: a população selecionada permanece não
  ajustada, e `COVARIATE_ADJUSTMENT_UNVERIFIED` registra isso explicitamente.
- Setup, talentos e equipamento detalhado permanecem não casados por decisão de
  fronteira; sua influência sobre a execução continua não controlada.
- A compatibilidade de hotfix segue não verificada, herdada de M2.1 e preservada.
- A população aspiracional é selecionada pelo desfecho e ordenada por uma medida WCL
  distinta das medidas reconciliadas de M1; não é prova de melhor execução nem de
  ganho recuperável (C05).
- Atingir o piso de 8 não é suficiência estatística, e `MIN_N_FOR_GRADING` continua
  sendo descrição, não calibração de confiança — interpretação é B06/M3.
- A política não é consumida por nenhum caminho de produção em M2.2. Propagação,
  persistência, proveniência e a reconciliação com o estágio de crescimento de
  `match_cohort` acima do piso são trabalho explícito de M2.3.
