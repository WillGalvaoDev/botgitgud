# M2.3 — Integração e closure da comparabilidade

**Unidade:** M2.3 (macro M2 — Comparabilidade por métrica; unidade de
integration/closure). **Status:** SPEC_READY. **Versão:** v002
(2026-09-23), substitui v001 (2026-09-22).

**Correção v002.** Resolve o finding R1 de
`docs/submilestones/M2.3/independent-review.md`: v001 exigia ao mesmo tempo
saída de `match_cohort` idêntica para qualquer entrada (§2, §4.1, §8.2) e
invariância da proveniência à permutação dos logs buscados (§8.6). Com
duas representações divergentes do mesmo jogador no mesmo pull, empatadas
na chave de deduplicação, `min` escolhe pela ordem de chegada e as duas
exigências não podem valer juntas. A decisão D-M23-07 (§4.0) resolve o
conflito sem alterar `match_cohort` e sem escolher representação.
Alterados: §2, §3, §4 (novo §4.0 e invariante), §7.1, §8.2, §8.6, §9.1,
§9.8, §10 (AC2), §11. Demais cláusulas são idênticas às de v001. As
evidências produzidas contra v001 não valem para o fechamento desta versão.

Autoridade normativa: [M0](m0-methodology-contract.md) (C01–C10),
[SPEC M1](m1-specification.md) (MILESTONE_CLOSED),
[SPEC M2.1](m2-1-specification.md) (MILESTONE_CLOSED),
[SPEC M2.2](m2-2-specification.md) (MILESTONE_CLOSED —
`docs/submilestones/M2.2/final-independent-review.md`),
[roadmap M2–M6](methodology-roadmap-m2-m6.md), [workflow](milestone-workflow.md).

Este documento conecta a elegibilidade básica (M2.1) e a seleção por métrica
(M2.2) ao caminho de análise, ao contrato de relatório e à proveniência
persistida. Não altera M0/M1/M2.1/M2.2, não cria seletor novo, não
redesenha texto e não antecipa M3. O implementador deve persistir este
documento verbatim em `docs/m2-3-specification.md` e registrá-lo em
`docs/README.md`.

Linha de produto: workspace `BotGITGUD-M2.1-product`, sobre
`edba6e62d144811724efe3a2f9bdf0d6023c64ee` mais a entrega M2.2 fechada
(`metric_population.py` SHA-256
`8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a`).

## 1. Objetivo

Fazer com que toda comparação produzida pelo caminho ativo de análise use
exatamente a população declarada para ela, com N, exclusões, relaxamentos e
versões de política sobrevivendo até o contrato e à persistência; fazer com
que insuficiência ou incompatibilidade bloqueiem apenas a comparação
dependente, mantendo as observações independentes válidas; e preservar as
identidades contábeis de M1 sobre a população efetivamente aceita.

Com o fechamento desta unidade, o macro M2 fecha (workflow, regra de closure).

## 2. Escopo e fora de escopo

Incluído:

- Ordem de estágios da seleção no `run_analysis` (§4).
- Roteamento de população por consumidor (§5).
- Comportamento de insuficiência (§6).
- Formato mínimo de proveniência de comparabilidade, sua propagação ao
  contrato e sua persistência aditiva (§7) — pendência do Opus registrada no
  roadmap para esta unidade.
- Testes de integração e pacote de evidências (§9).

Fora de escopo (roadmap M2.3 e SPECs anteriores):

- Novo seletor, nova covariável, nova política ou nova versão de
  `reference-eligibility-v1`/`metric-population-v1`. `reference_eligibility.py`
  e `metric_population.py` ficam **byte-idênticos**.
- Qualquer mudança nas regras de higiene/deduplicação, no ladder de
  `match_cohort` ou nas constantes existentes (além da extração sem mudança
  de comportamento descrita em §4). A quarentena de §4.0 não altera nenhuma
  dessas regras: é uma etapa separada do caminho de produção, anterior à
  higiene, que `match_cohort` e `hygienic_candidates` não chamam.
- Qualquer desempate novo entre representações divergentes, inclusive por
  conteúdo, por elegibilidade M2.1, por disponibilidade de métrica ou por
  ordem de chegada canonizada.
- Fórmulas contábeis de M1, denominadores, limiares de grade, materialidade.
- Redesign de texto CLI/Discord, novas frases, novos canais (M5).
- Interpretação estatística de grades (M3/B06), streams restantes (M3.1),
  oportunidades (M4), ML (M6).
- Novas queries à API, alteração de fetchers, reescrita de `data/raw`,
  reprocessamento de runs históricos.

## 3. Decisões resolvidas nesta SPEC

O roadmap determina que nenhuma decisão metodológica nova fique para o
closure. As decisões abaixo são **derivações obrigatórias** de decisões já
aprovadas, ou a única pendência atribuída ao Opus para esta unidade:

| ID | Decisão | Fundamento |
|---|---|---|
| D-M23-01 | Ordem: higiene → M2.1 → (ledger por `match_cohort` sobre os elegíveis) e (populações M2.2 sobre o conjunto higiênico) | M2.1 §1/§4 (condição necessária, dedup pertence a `cohort_match`); M2.2 §3/§9 (consome a elegibilidade propagada) |
| D-M23-02 | As seis métricas contratadas usam a população DESCRIPTIVE de M2.2; os demais consumidores continuam na população do ledger, agora restrita aos elegíveis M2.1 | M2.2 §7.1 (única distribuição admitida para as seis métricas); M1 §4.2 (ledger e grade são populações separadas); política de matching v2 existente preservada (EC.3/M4) |
| D-M23-03 | A divergência declarada em M2.2 §6.1 (estágio "low bias" de `match_cohort` acima do piso) é resolvida por escopo: vale só para o ledger, nunca para as seis métricas | Delegada a esta unidade pela SPEC M2.2 aceita; manter o comportamento existente do ledger não cria política |
| D-M23-04 | Insuficiência de população não aborta a análise; bloqueia só os consumidores dependentes | AC3 do roadmap; M1 §6 ("abaixo disso, disponibilizar accounting... estado público `INSUFFICIENT_REFERENCES`") |
| D-M23-05 | Aspiracional do ledger aplica a guarda de `dps` finito e o piso de ordenáveis | C06; M2.2 §7.2 (mesma função, mesma finalidade) |
| D-M23-06 | Formato mínimo de proveniência `comparability-provenance-v1` | Pendência do Opus para M2.3 no roadmap |
| D-M23-07 | Representações divergentes empatadas na chave de deduplicação são postas em quarentena (todas excluídas, contadas e identificadas na proveniência) antes da higiene; nenhuma é escolhida | `dedup_priority` proíbe desempate por ordem de chegada; M2.2 §4/`_stage_a` recusa escolher entre representações divergentes do mesmo id; C07/M0: insuficiência nunca vira valor; esta SPEC §4.2 proíbe escolher duplicata por elegibilidade. Resolve R1 da revisão independente |

Nenhuma outra escolha metodológica é delegada ao executor. Ambiguidade
encontrada na implementação volta ao Opus (workflow, routing 5).

## 4. Ordem de estágios (D-M23-01)

Dentro de `run_analysis`, após `fetch_cohort_logs`, em ordem fixa:

0. **Quarentena de representações divergentes** (§4.0), sobre a lista
   buscada inteira. As etapas seguintes recebem somente a saída dela.
1. **Higiene.** Extrair de `match_cohort` a etapa já existente de higiene
   (exclusão de self por `player_identity`, exclusão de não-kill,
   uma referência por pull, uma por jogador, `dedup_priority`) para uma
   função pública `hygienic_candidates(target, candidates) -> (list[PlayerLog], HygieneReport)`,
   e a etapa de covariáveis para `match_covariates(target, hygienic, *, min_n, matching_policy_version) -> (list[PlayerLog], MatchReport)`.
   `match_cohort(t, c, ...)` passa a ser exatamente
   `match_covariates(t, hygienic_candidates(t, c)[0], ...)` com os contadores
   de higiene preenchidos no `MatchReport`, **com saída idêntica** à atual
   para qualquer entrada. Nenhuma regra de higiene ou de covariável muda.
   No caminho de produção, `hygienic_candidates` recebe a saída da etapa 0;
   `match_cohort` e `hygienic_candidates` não chamam a quarentena.
2. **M2.1.** `evaluate_references(player_log, hygienic)` sobre o conjunto
   higiênico inteiro. A higiene precede M2.1 por decisão já vigente: a
   escolha entre duplicatas nunca é feita pelo resultado de elegibilidade.
3. **Ledger.** `match_covariates(player_log, eligible, min_n=COHORT_TARGET_N, matching_policy_version=criteria.matching_policy_version)`,
   onde `eligible` são os logs higiênicos cujo `reference_id` está em
   `eligibility.eligible_ids`, na ordem do conjunto higiênico. Resultado:
   `R_log` (a população do ledger) e seu `MatchReport`.
4. **M2.2.** `select_metric_populations(player_log, hygienic, eligibility=..., catalog=deps.catalog, metric_ids=None)`
   sobre o conjunto higiênico inteiro (não sobre `R_log`: M2.2 precisa ver
   as referências antes de qualquer filtro de covariável para que o próprio
   ladder seja o único a relaxá-las).

Invariante: após a higiene há no máximo uma referência por
`(report_code, fight_id)`, logo nenhuma colisão de `reference_id` pode chegar
a M2.2. Um `ValueError` de colisão em produção é defeito e deve propagar; não
pode ser capturado, silenciado ou convertido em insuficiência.

Invariante de ordem: após a etapa 0, todo empate restante nos `min` da
higiene ocorre entre logs iguais por `==`; portanto o conjunto higiênico,
e tudo o que dele deriva, não depende da ordem da lista buscada.

### 4.0 Quarentena de representações divergentes (D-M23-07)

Função pública nova em `cohort_match.py`, pura, sem I/O e sem exceção por
tamanho:

```text
quarantine_conflicting_duplicates(target, candidates)
    -> (list[PlayerLog], DuplicateConflictReport)

DuplicateConflictReport (frozen/slots):
    n_input: int            # len(candidates)
    n_output: int           # len(saída)
    excluded_logs: int      # n_input - n_output
    conflicting_ids: tuple[str, ...]
        # damage_reference_id de cada log removido, sem repetição, ordenados
```

Regra, completa e fechada:

1. **Domínio.** Consideram-se somente os candidatos `c` com
   `player_identity(c) != player_identity(target)` e `c.fight.kill`. Os
   demais passam intactos; a higiene os exclui depois com os contadores de
   sempre (`excluded_self`, `excluded_non_kill`), sem dupla contagem.
2. **Chave de empate.**
   `tie_key(c) = (c.fight.report_code, c.fight.fight_id, player_identity(c), dedup_priority(c))`.
   É exatamente o conjunto de campos em que um `min` da higiene pode
   empatar: na etapa por pull a chave é `(dedup_priority, player_identity)`
   dentro do mesmo `(report_code, fight_id)`; na etapa por jogador, depois de
   uma referência por pull, `dedup_priority` já contém
   `(report_code, fight_id)` e não há empate possível.
3. **Conflito.** Um grupo com a mesma `tie_key` está em conflito quando
   contém dois logs que não são iguais pela igualdade do dataclass
   `PlayerLog` (`!=`). A igualdade é a única comparação: nenhum campo é
   inspecionado, pontuado ou priorizado. Um grupo de logs todos iguais não
   está em conflito e passa intacto; a higiene o colapsa como hoje, e
   qualquer escolha devolve o mesmo valor.
4. **Exclusão.** Todos os logs de um grupo em conflito são removidos. Nenhum
   é escolhido, mesclado, reparado ou substituído por outra representação,
   nem por elegibilidade M2.1, disponibilidade de métrica, conteúdo ou ordem.
5. **Saída.** Os candidatos restantes, na ordem relativa de entrada.

A pertença a um grupo em conflito depende só do multiconjunto de
candidatos, não da ordem. Uma referência em quarentena não entra em M2.1,
no ledger nem em nenhuma população M2.2. Sua exclusão reduz o N disponível
e aparece na proveniência (§7.1), nunca como valor. Nenhum texto novo é
criado (§6.4).

Fundamento: `dedup_priority` declara que o desempate nunca usa a ordem de
chegada; M2.2 (`_stage_a`) já recusa escolher entre representações
divergentes do mesmo id; esta SPEC proíbe escolher duplicata pelo resultado
de elegibilidade (§4.2). Quando a regra canônica não distingue duas
representações que divergem, não existe evidência para preferir uma delas.
Excluir o grupo e declarar a exclusão é a única saída que não inventa
desempate e preserva a higiene.

## 5. Roteamento de população por consumidor (D-M23-02, D-M23-03)

| Consumidor atual | População em M2.3 |
|---|---|
| `compare_metrics` dentro de `analyze_dps_gap` (seis métricas, grades por habilidade, materialidade que lê `gross_ability_dps`) | DESCRIPTIVE de M2.2 do próprio `metric_id` |
| `_build_uptime_findings` em `performance_features` (grade de `aura_uptime_fraction`) | DESCRIPTIVE de M2.2 de `aura_uptime_fraction:<sid>`; o gate de relevância `presence` (T3.1) continua calculado sobre `R_log`, sem mudança de regra |
| `compare_damage` (ledger, `DamageComparison`, `AbilityGap`, suporte, residual), mediana de DPS medido, `build_conclusion` | `R_log`, e dentro dele a elegibilidade quantitativa de M1 sem alteração |
| `compare_damage` aspiracional (`aspirational_comparison`) | `select_benchmark_reference` sobre `R_log` com a guarda de §6.3 |
| Demais grades de `analyze_performance_features` (active time, mortes, downtime, desperdício), `build_cd_reference_profile`, `compare_all_spells`, `_report_ability_sections` (núcleo, procs, contexto externo), duração min/max do header | `R_log` |
| Populações ASPIRATIONAL por métrica de M2.2 | Nenhum consumidor novo; propagadas somente na proveniência (§7) |

Regras do roteamento:

1. Nenhum consumidor recebe referência `INELIGIBLE` ou `INDETERMINATE` em M2.1.
2. `compare_metrics` ganha um modo de população: recebe
   `Mapping[str, MetricPopulationSet]` e, para cada `metric_id`, usa como
   referências **exatamente** `descriptive.members`, sem nenhum filtro
   adicional (conflito de identidade e escopo já são decididos por higiene e
   M2.1). O conjunto de chaves de saída é exatamente o do mapping. O modo
   legado (sem populações) permanece inalterado para chamadores diretos; o
   caminho de produção usa sempre o modo de população.
3. `MetricComparison` ganha o campo aditivo `population: MetricPopulationSet | None = None`.
   No modo de população: `reference_ids == population.descriptive.members`;
   `reference_values` são os valores `AVAILABLE` desses membros na mesma
   ordem; `excluded_references[rid]` é o primeiro código de
   `population.descriptive.excluded_reasons[rid]` (o código de estágio
   dominante, pela ordem A→D de M2.2 §10.1), e a tupla completa permanece em
   `population`. Nenhum código é criado, traduzido ou concatenado.
4. Uma grade (`finding`) só existe quando
   `population.descriptive.sufficiency is SUFFICIENT_FOR_GRADING`; as fórmulas
   e o limiar de `grade_scalar` não mudam.
5. `buffs_relaxed`, `relaxed_covariates` e `matched_covariates` do header e
   de `build_findings`/`build_conclusion` continuam descrevendo o ledger
   (`MatchReport` de `R_log`), sem mudança de significado.

## 6. Insuficiência e incompatibilidade (D-M23-04, D-M23-05)

### 6.1 Remoção do aborto por política M2

`run_analysis` deixa de levantar `InsufficientCohort` quando a população do
ledger fica abaixo do piso. `InsufficientCohort` continua existindo apenas
onde é levantado hoje fora do matching (pool insuficiente em
`ingest/rankings.py`), sem mudança. `CohortNotReady`, `ScopeRejected` e demais
erros ficam inalterados.

### 6.2 Estado do ledger

`ledger_state = "SUFFICIENT"` se `len(R_log) >= COHORT_MIN_HARD`, senão
`"INSUFFICIENT_REFERENCES"` (mesmo código público de M1 §6). O
`accounting_status` de `DpsGapReport` mantém seus valores e regras de M1
(incluindo `NO_REFERENCES` para `R_log` vazio), sem mudança; `ledger_state`
é o estado da população, não substitui o estado contábil.

Quando `INSUFFICIENT_REFERENCES`:

| Sempre computado | Não computado (valor ausente já admitido pelo tipo) |
|---|---|
| `DamageAccounting` do jogador e `analyze_dps_gap` sobre `R_log` (o próprio M1 já publica `INSUFFICIENT_REFERENCES` e não publica comparação quantitativa) | `analyze_performance_features` → `performance=None` |
| `metric_comparisons` das seis métricas, cada uma pela sua população M2.2 e pela sua suficiência própria | `build_cd_reference_profile`/`compare_all_spells` → `comparisons=()` |
| `setup_analysis`, `phase4_resolution` e demais campos independentes de referências | `_report_ability_sections` → `core_abilities=()`, `proc_analysis=None`, `external_dps_context=()` |
| `build_findings`, remediações, materialidade, `build_conclusion`, observação positiva, com as entradas disponíveis | aspiracional do ledger → comparação sobre `()` |

Com `ledger_state == "SUFFICIENT"`, todos os consumidores rodam como hoje,
com as populações de §5.

Uma métrica cuja população M2.2 é insuficiente não gera grade (§5.4) nem
bloqueia outras métricas; uma métrica suficiente não é bloqueada pela
insuficiência do ledger. Nenhuma função pode levantar exceção por tamanho de
população; nenhum consumidor pode receber lista vazia onde isso produz
valor fabricado.

### 6.3 Aspiracional do ledger

Antes de `select_benchmark_reference`, excluir de `R_log` todo log sem `dps`
finito; se restarem menos de `REFERENCE_MIN_N` ordenáveis, o aspiracional do
ledger é indisponível (`compare_damage(player, ())`) e a proveniência registra
`ASPIRATIONAL_UNAVAILABLE`. Nunca ordenar `None` como `0.0`. A ordenação por
`dps` WCL, o piso percentual e o fallback de `select_benchmark_reference` não
mudam.

### 6.4 Apresentação

Nenhum texto novo é criado. CLI e Discord, com o contrato resultante de
§6.2, devem renderizar sem exceção e sem exibir número de comparação
dependente não computada. Frases existentes que citam o N do ledger ao lado
de uma grade por métrica cuja população é outra são dívida de apresentação
de M5.2 (C09): o executor as inventaria nas evidências, sem corrigi-las aqui.

## 7. Proveniência mínima (D-M23-06)

### 7.1 Conteúdo — `comparability-provenance-v1`

Novo módulo `src/botgitgud/analysis/comparability_provenance.py`, dataclasses
frozen/slots:

```text
COMPARABILITY_PROVENANCE_VERSION = "comparability-provenance-v1"

ComparabilityProvenance:
    provenance_version: str
    reference_eligibility_policy_version: str     # de ReferenceEligibilityPopulation
    metric_population_policy_version: str         # de MetricPopulationSet
    ledger_matching_policy_version: str           # criteria.matching_policy_version
    target_id: str
    hygiene: HygieneSummary      # n_input (lista buscada, antes da quarentena), n_output,
                                 # excluded_conflicting_duplicates, conflicting_duplicate_ids,
                                 # excluded_self, excluded_non_kill, deduped_pull, deduped_player
    eligibility: EligibilitySummary
        # n_evaluated, eligible_ids, indeterminate_ids, ineligible_ids (ordenados),
        # excluded_reasons: id -> tupla verbatim de M2.1, declared_limitations
    ledger: LedgerSummary
        # state ("SUFFICIENT" | "INSUFFICIENT_REFERENCES"), member_ids (R_log, ordenados), n,
        # matched_covariates, relaxed_covariates, adjustment_covariates, cohort_level,
        # accepted_ids (DamageComparison.reference_ids), accounting_exclusions
        # (DamageComparison.excluded_references), aspirational_member_ids,
        # aspirational_limitations
    metrics: Mapping[str, MetricPopulationSummary]    # chave metric_id, ordenada
        # por população (descriptive e aspirational):
        #   kind, n, sufficiency, member_ids, final_duration_band_pct,
        #   matched/relaxed/declared_covariates,
        #   relaxation_steps (covariate, rule, band_index, band_pct, n_before, n_after),
        #   exclusion_counts: código -> contagem (sobre o primeiro código por id),
        #   declared_limitations
```

Regras:

- Toda sequência de IDs ordenada por `reference_id`; todo mapping com chaves
  ordenadas; enums serializados pelo valor; nenhum campo derivado de texto.
- `hygiene.excluded_conflicting_duplicates` e
  `hygiene.conflicting_duplicate_ids` são copiados do
  `DuplicateConflictReport` (§4.0); os demais contadores vêm do
  `HygieneReport` da etapa 1. Vale
  `n_input == n_output + excluded_conflicting_duplicates + excluded_self + excluded_non_kill + deduped_pull + deduped_player`.
  Nenhum id de `conflicting_duplicate_ids` aparece em `eligibility`,
  `ledger` ou `metrics`. `comparability-provenance-v1` é definido por esta
  versão da SPEC; nenhum artefato de v001 foi aceito ou persistido em
  produção.
- `accepted_ids ⊆ member_ids ⊆ eligibility.eligible_ids` (ledger) e
  `aspirational.member_ids ⊆ descriptive.member_ids` (por métrica).
- `declared_limitations` de M2.1 (inclui `TARGET_ATTEMPT_NOT_KILL` e
  `HOTFIX_COMPATIBILITY_UNVERIFIED`) aparecem verbatim.
- O resumo por métrica omite `admitted_ids` dos passos e os IDs por motivo;
  preserva contagens por código. Isso limita o tamanho persistido sem perder
  N, exclusões, relaxamentos e versões (AC2).

### 7.2 Propagação

- `AnalysisResult` ganha `comparability: ComparabilityProvenance | None = None`
  (aditivo; `None` só em construções legadas/diretas).
- `ConfidenceSummary` do `ReportContract` ganha
  `comparability: ComparabilityProvenance | None = None`, copiado sem
  transformação por `build_report_contract`.
- `matched_cohort_members`, `matched_reference_n`, `matched_covariates` e
  `relaxed_covariates` continuam significando o ledger (`R_log`).

### 7.3 Persistência

- `RunManifest` ganha, aditivamente: `reference_eligibility_policy_version`,
  `metric_population_policy_version`, `ledger_matching_policy_version`,
  `comparability_provenance_version` (default `"unknown"`) e
  `comparability_provenance_json: str | None = None`.
- `runs` recebe as colunas correspondentes por `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`,
  no mesmo padrão idempotente de M1. Linhas antigas ficam `NULL` e são lidas
  como versão desconhecida/legada, nunca como a versão corrente.
- Serialização canônica: `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`.
  Um decodificador puro reconstrói `ComparabilityProvenance` igual (`==`) ao
  original. Versão desconhecida no decode levanta `ValueError`.
- Nenhuma linha histórica é regravada; nenhum Parquet/log bruto muda.

## 8. Invariantes

1. `metric_population.py` e `reference_eligibility.py` byte-idênticos.
2. `match_cohort` produz saída idêntica à anterior para qualquer entrada,
   inclusive com representações divergentes empatadas (a função não chama
   a quarentena). A invariância de ordem é propriedade do caminho de
   produção (§4.0 + §4), não de `match_cohort`.
3. Nenhum consumidor recebe referência rejeitada por M2.1.
4. Para cada `metric_id` em produção:
   `MetricComparison.reference_ids == population.descriptive.members`.
5. `DamageComparison.reference_ids ⊆ R_log` e as identidades de M1
   (soma por habilidade + suporte + residual = delta total; shares sobre
   bruto; mesmo `T`) valem sobre `DamageComparison.reference_ids`.
6. Mesmas entradas ⇒ mesmo `AnalysisResult.comparability` e mesmo JSON, byte a byte;
   permutar a ordem dos logs buscados não muda a proveniência nem seu JSON,
   para qualquer lista buscada,
   inclusive com representações divergentes empatadas na chave de
   deduplicação (resolvidas por §4.0, sem pré-condição sobre a entrada).
7. Insuficiência nunca é convertida em zero, em grade, em comparação
   publicada ou em ausência silenciosa de estado.

## 9. Testes e evidências obrigatórios

Casos com resultado esperado definido antes da implementação, em
`tests/unit/test_m2_3_comparability_integration.py`, oráculos calculados
independentemente das funções sob teste:

1. **Equivalência do refactor:** `match_cohort` antes/depois igual nas
   fixtures de `test_cohort_match.py` e em casos sintéticos com higiene ativa,
   incluindo um caso com representações divergentes empatadas em cada ordem
   (o oráculo é a escolha por ordem de chegada do código anterior; a
   quarentena não pode alterar `match_cohort`).
2. **Replay integrado com populações diferentes entre métricas** por
   `run_analysis` com dependências falsas existentes: pelo menos uma métrica
   com população M2.2 maior e uma menor que `R_log`, e uma referência
   `INELIGIBLE` em M2.1 que antes entrava no matching; verificar AC1 por IDs.
3. **Ledger insuficiente com métrica suficiente:** a análise retorna; ledger
   `INSUFFICIENT_REFERENCES`; os campos da coluna direita de §6.2 ausentes;
   a métrica suficiente mantém comparação; setup presente.
4. **Aspiracional do ledger** com `dps=None` e com menos de 8 ordenáveis.
5. **Round-trip de proveniência:** encode/decode igual; Store write → leitura
   da linha → decode igual; banco criado antes da migração continua legível e
   linhas antigas decodificam como legadas.
6. **Regressões contábeis M1:** suítes M0/M1 existentes passam; fechamento da
   decomposição verificado no replay de (2).
7. **Contrato e renderização:** `build_report_contract` preserva
   `comparability` verbatim; CLI e payload Discord renderizam os cenários (2)
   e (3) sem exceção e sem números de comparação não computada.
8. **Determinismo e permutação** de (2), e **quarentena (§4.0)**:
   - unidade de `quarantine_conflicting_duplicates`, com oráculo escrito à
     mão: grupo divergente removido inteiro; grupo de duplicatas iguais
     intacto; mesmo pull com percentis diferentes intacto (a higiene decide);
     self e não-kill fora do domínio; `conflicting_ids` ordenados e sem
     repetição; mesma saída como multiconjunto e mesmo relatório em todas as
     permutações de uma lista pequena;
   - por `run_analysis`, nas duas ordens, os dois contraexemplos de R1:
     mesmo `REPORT:501:<nome>` com `uptime` ausente vs. `0.5`, e mesmo id
     com classe divergente. Esperado: proveniência e JSON byte-idênticos
     entre as ordens; o id em `hygiene.conflicting_duplicate_ids`,
     `excluded_conflicting_duplicates == 2`; o id ausente de
     `eligibility`, `ledger.member_ids` e de todas as populações;
   - por `run_analysis`, duplicata idêntica nas duas ordens: colapsada pela
     higiene como antes (`deduped_pull == 1`), sem quarentena, id presente.
9. **Replay somente-leitura** sobre `tests/fixtures/gate1_scope/` (e
   `data/raw` se presente, com ausência declarada), hash antes/depois.
10. **Estruturais:** imports dos módulos M2.1/M2.2 inalterados; SHA-256 dos
    dois módulos iguais aos fechados; o caminho de produção chama
    `compare_metrics` só no modo de população.
11. **Estático e suíte:** Ruff, formato e Pyright; suíte offline completa com
    os placeholders públicos do CI, comparada à baseline M2.2
    (2690 passed, 47 skipped, 1 deselected, 1 failed preexistente).

Testes existentes só podem ser alterados onde esta SPEC muda comportamento
contratado, citando a cláusula (por exemplo, os que esperam
`InsufficientCohort` do matching, §6.1). O snapshot
`tests/golden/__snapshots__/test_new_pipeline_output.ambr` só pode ser
atualizado com cada diferença explicada nas evidências por mudança de
população desta SPEC; atualização em massa sem justificativa é proibida.

Evidências em `docs/m2-3-review-evidence.md`: matriz critério → teste →
resultado, casos definidos antes, tabela por consumidor com N antes/depois
no replay (2), inventário de §6.4, comandos, ambiente, falhas/skips e
limitações.

## 10. Critérios de aceite

| ID | Critério |
|---|---|
| AC1 | Os IDs usados em cada cálculo coincidem com os da população declarada para ele: DESCRIPTIVE de M2.2 para as seis métricas; `R_log` (e seus aceitos M1) para o ledger e consumidores de §5; nenhum ID rejeitado por M2.1 em qualquer consumidor |
| AC2 | N, exclusões (incluindo a quarentena de §4.0), relaxamentos e versões de política (M2.1, M2.2, matching do ledger, proveniência) sobrevivem análise → contrato → persistência, com round-trip exato e leitura legada explícita; a proveniência é invariante à ordem dos logs buscados, inclusive com representações divergentes empatadas |
| AC3 | Insuficiência ou incompatibilidade impedem apenas a comparação dependente (§6.2); a análise não aborta por política M2; observações independentes válidas permanecem no resultado e no contrato |
| AC4 | As identidades contábeis de M1 valem sobre a população efetivamente aceita, com regressões M0/M1 passando |
| AC5 | Escopo respeitado: sem seletor/política nova, módulos M2.1/M2.2 byte-idênticos, `match_cohort` com saída idêntica, sem texto novo, sem query nova, sem reescrita histórica |
| AC6 | Pacote de evidências completo (§9), com replay integrado, round-trip, inventário de dívida de apresentação, Ruff, Pyright e suíte ampla |

## 11. Limitações residuais declaradas

- O ledger continua usando a política de matching v2 existente, inclusive o
  crescimento acima do piso; as seis métricas não. Populações distintas por
  finalidade são o contrato de C07, não uma inconsistência.
- O gate de relevância de uptime (`presence`) continua medido sobre o ledger.
- Representações divergentes do mesmo jogador no mesmo pull, empatadas na
  chave de deduplicação, são excluídas por inteiro (§4.0). Isso pode reduzir
  N; a causa da divergência na aquisição não é investigada nem corrigida
  aqui (sem query ou fetcher novo). Chamadores diretos de `match_cohort`
  mantêm o comportamento anterior, dependente da ordem nesse caso.
- Populações aspiracionais por métrica não têm consumidor até decisão
  posterior; ficam só na proveniência.
- Hotfix segue não verificado; relaxar não é ajustar; setup não é casado.
- Frases que associem N do ledger a grades por métrica são dívida de M5.2.
- Interpretação das grades (B06) e streams restantes permanecem em M3.
