# Closure - 2026-09-22

**MILESTONE_CLOSED** after independent Astra final review. See [closure](m2-2-closure.md). The executor notes below are retained unchanged.

# M2.2 — evidências de seleção da população por métrica

Status: **IMPLEMENTATION_READY** (segunda rodada de correção). Autoridade:
[`m2-2-specification.md`](m2-2-specification.md), spec_sha
`ea35eb66efe593076a1fe8efed9b337731f0a970097877ca72fe33ab19d84968`
(`docs/submilestones/M2.2/spec-v001.md`/`.json`, inalterada em ambas as
rodadas). Escopo: somente M2.2. Primeira rodada corrigiu os achados R1/R2/R3
de
[`docs/submilestones/M2.2/independent-review.md`](submilestones/M2.2/independent-review.md);
esta segunda rodada fecha a única pendência remanescente identificada por
[`docs/submilestones/M2.2/independent-rereview.md`](submilestones/M2.2/independent-rereview.md):
R3/AC6 exigia a tabela sintética de sensibilidade por nível (N e membros)
preservada nas evidências, não apenas referenciada por nome de teste. R1 e
R2 não foram reabertos. Nenhuma decisão metodológica foi alterada, nenhum
escopo foi expandido, M2.3 não foi iniciada. Linha de produto: workspace
`BotGITGUD-M2.1-product`, sobre o commit de fechamento de M2.1
`edba6e62d144811724efe3a2f9bdf0d6023c64ee`.

Esta segunda rodada altera **somente este documento**. `metric_population.py`
e `test_m2_2_metric_population.py` permanecem byte-idênticos aos SHA-256
confirmados pela rerevisão independente (§ abaixo) — a lacuna era
exclusivamente de evidência documental, não de código ou de cobertura de
teste (a rerevisão já havia confirmado que o teste executa a função e
confere parte dos resultados; faltava exportar a tabela em si).

Arquivos alterados no total pela unidade, exatamente os previstos pelo
`write_paths` da SPEC: `src/botgitgud/analysis/metric_population.py`,
`tests/unit/test_m2_2_metric_population.py`, `docs/m2-2-specification.md`
(inalterado), `docs/m2-2-review-evidence.md` (este arquivo),
`docs/README.md`. `git status --short` confirma exatamente esses caminhos
(mais `docs/submilestones/M2.2/`, que já continha a SPEC aceita e, desde a
primeira rodada, os relatórios independentes e seus scripts de reprodução).

SHA-256 desta rodada:
`src/botgitgud/analysis/metric_population.py` =
`8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a`;
`tests/unit/test_m2_2_metric_population.py` =
`daff74b6353ac37eb62d70aa64b883591b0667c18db0f30ac61a82dbee483e41`.

## 0. Correção dos achados bloqueantes

### R1 — P1: colisão de identidade mistura o log e a decisão básica

Causa raiz: `_stage_a` associava o log e a decisão de elegibilidade de forma
**independente** — o primeiro log por `reference_id` (`setdefault`) e a
**última** decisão por `reference_id` (compreensão de dicionário) — de modo
que, sob colisão de ID entre objetos diferentes, o par log↔decisão usado
podia vir de dois objetos físicos distintos.

Correção: `_stage_a` agora agrupa **todos** os logs e **todas** as decisões
de cada `reference_id` e só produz um par quando eles concordam entre si
(mesmo log por igualdade de dataclass, mesma decisão). Quando concordam, a
colisão é inofensiva e colapsa deterministicamente (a escolha entre cópias
idênticas não importa, pois são extensionalmente iguais). Quando **não**
concordam, a função levanta `ValueError` em vez de associar um log a uma
decisão que pode pertencer a outro objeto — a entrega não escolhe qual
representação prevalece; a decisão metodológica sobre deduplicação de
produto permanece com `cohort_match` (M2.3), conforme o próprio M2.1 §4 já
declarava ("deduplicação de pull/jogador continua sendo responsabilidade de
`cohort_match`"). Nenhuma decisão do Opus foi necessária: o defeito era de
associação de dados, não de política.

A mesma correção fecha `covariate_sensitivity`, que compartilha `_stage_a`.

Reprodução do contraexemplo original do revisor, agora com o comportamento
corrigido (script `docs/submilestones/M2.2/independent-probes.py.txt`
reexecutado; as asserções originais descreviam o comportamento do defeito e
por isso falham contra o código corrigido — o script não foi alterado, é
prova documental do estado anterior):

```text
bad=Warrior/Fire, good=Mage/Fire, mesmo reference_id "REPORT:1:Duplicate"
-> select_metric_population(...) levanta ValueError em QUALQUER ordem
   ("reference_id collision with disagreeing data for 'REPORT:1:Duplicate'")

missing=uptime None, observed=uptime 0.5, mesmo reference_id
-> select_metric_population(..., metric="aura_uptime_fraction") levanta
   ValueError em QUALQUER ordem, em vez de produzir N=0 ou N=1 conforme a ordem
```

Testes permanentes adicionados (`tests/unit/test_m2_2_metric_population.py`):
`test_identity_collision_with_disagreeing_class_raises_instead_of_cross_associating`,
`test_identity_collision_with_disagreeing_availability_raises_instead_of_changing_n`,
`test_identity_collision_also_raises_from_covariate_sensitivity`,
`test_harmless_identical_duplicate_collapses_deterministically_without_raising`
(prova de que colisões **sem** desacordo continuam permitidas e determinísticas,
não apenas que colisões com desacordo levantam erro).

### R2 — P2: exclusões não são ordenadas por reference_id

Causa raiz: `_build_excluded_reasons` iterava `id_to_log.items()` na ordem de
inserção (dependente da ordem de entrada de `references`), e a construção de
`common` no aspiracional apenas copiava a base e **acrescentava** as
exclusões próprias do aspiracional ao final, sem reordenar o dicionário
resultante.

Correção: novo helper `_sorted_by_key` reconstrói o dicionário com as chaves
em `sorted()` antes de cada `MetricPopulation` ser construída — tanto para a
população descritiva (`_build_excluded_reasons` agora itera
`sorted(id_to_log)`) quanto para as duas saídas do aspiracional (disponível e
`UNAVAILABLE`). A ordenação é agora uma propriedade da própria saída (ordem
de iteração do dicionário), não algo que só se observa depois de uma
normalização externa como `json.dumps(..., sort_keys=True)`.

Reprodução do contraexemplo original, agora corrigido:

```text
refs = (Z: EXTERNAL_BUFFS_MISMATCH, A: DURATION_BAND_MISMATCH)
-> ordem (Z, A): excluded_reasons.keys() == ['REPORT:1:A', 'REPORT:1:Z']
-> ordem (A, Z): excluded_reasons.keys() == ['REPORT:1:A', 'REPORT:1:Z']  (idêntico)
-> json.dumps(dataclasses.asdict(...)) idêntico nas duas ordens,
   SEM sort_keys=True
```

Testes permanentes adicionados:
`test_excluded_reasons_are_ordered_by_reference_id_regardless_of_input_order`
(população descritiva, comparação de `json.dumps` sem `sort_keys`),
`test_aspirational_excluded_reasons_are_also_ordered_by_reference_id`
(população aspiracional, mistura de exclusões herdadas da descritiva com
exclusões próprias do aspiracional).

### R3 — P2: pacote incompleto para AC6

Três lacunas apontadas, cada uma fechada por testes novos:

1. **§12.9 — sensibilidade sobre metadados reais, tabela preservada.**
   `test_covariate_sensitivity_over_real_gate1_scope_metadata` executa
   `covariate_sensitivity` sobre a mesma fixture real usada no replay de
   M2.1/M2.2 (não apenas sobre fixtures sintéticas). Tabela preservada em
   §4.2 abaixo.
1b. **§12.9 (completado na rerevisão) — tabela sintética por nível, com N e
   membros efetivamente selecionados, preservada.** A rerevisão independente
   apontou que a primeira rodada citava
   `test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor`
   e afirmava "seis linhas", sem preservar as seis linhas em si (N por nível,
   membros). A tabela completa do cenário sintético de duas referências
   compatíveis e seis fora de tier/ilvl/duração está agora preservada em
   §4.0 abaixo, reproduzida diretamente da execução real de
   `covariate_sensitivity` (comando em §4.0). Nenhum código, teste ou SPEC
   mudou nesta rodada — só este documento.
2. **§12.10 — censo por métrica, não só `gross_ability_dps:1`.**
   `test_replay_gate1_scope_rankings_census_covers_all_six_contracted_metrics`
   executa o replay para as seis métricas contratadas e verifica o motivo de
   exclusão de `Rohanlock` (o único `ELIGIBLE` de M2.1) em cada uma. Tabela
   preservada em §4.1 abaixo.
3. **§12.1 — variação isolada de cada covariável para cada métrica.**
   Nova matriz de 24 casos parametrizados (`Covariate isolation matrix` em
   `test_m2_2_metric_population.py`) cobre `EXTERNAL_BUFFS` × 6 métricas,
   `DURATION` × 6 métricas, `ITEM_LEVEL` × (4 métricas de amplitude + 2
   métricas `NOT_ADMITTED`, provando explicitamente que nunca filtra nessas
   duas) e `TIER_PIECES` × (mesma divisão 4+2). Cada caso lê a linha
   estrita (nível 0) de `covariate_sensitivity` — isolando o efeito da
   covariável sobre N e a membresia sem a dinâmica do ladder interferir,
   exatamente o "conjunto em que apenas aquela covariável difere" exigido
   pela SPEC. (Correção de contagem: a rodada anterior deste documento dizia
   "26 casos"; são 24, como a própria seção 5 já discriminava — erro de
   redação não bloqueante apontado pela rerevisão.)

Sobre "não há artefato temporal independente que permita ao revisor
confirmar a cronologia" (matriz escrita antes da implementação): esta é uma
correção manual (Sonnet), não uma execução do orquestrador automatizado —
não existe, nesta sessão, um mecanismo de auditoria por timestamp equivalente
ao `spec-before-implementation.json` de M2.1. Construir esse mecanismo agora
seria ampliar o escopo desta correção (infraestrutura nova, não prevista no
`write_paths`). Registrado aqui como limitação honesta, não como cobertura
fabricada: a garantia disponível nesta rodada é a mesma de M2.1/M2.2 até
aqui — a matriz de casos consta do arquivo de testes versionado e revisável,
não uma prova temporal independente.

## 1. Matriz de casos (definida antes da implementação original; ampliada nesta correção — SPEC §5-§8 e §12)

| Código / comportamento | Eixo | Situação | Resultado | Teste |
|---|---|---|---|---|
| `EXTERNAL_BUFFS_MISMATCH` | covariável | conjunto de buffs ofensivos intersectado difere; REQ não relaxável em toda métrica | excluído em todo nível | `test_external_buffs_mismatch_excludes_at_every_relaxation_level`, `test_external_buffs_is_required_and_non_relaxable_even_for_positional_metric` |
| `EXTERNAL_BUFFS_MISMATCH` × 6 métricas (§12.1) | covariável | isolamento no nível estrito, cada uma das 6 métricas contratadas | N=1, só o compatível admitido | `test_external_buffs_isolation_at_strict_level_for_every_contracted_metric[...]` (×6) |
| `ITEM_LEVEL_BAND_MISMATCH` | covariável | `abs(cand-alvo) > ITEM_LEVEL_BAND` no nível estrito | excluído (sem relaxamento necessário) | `test_item_level_band_mismatch_excludes_amplitude_metric_at_strict_level` |
| `ITEM_LEVEL_UNKNOWN` | covariável | `item_level` desconhecido na referência | excluído, sem default | `test_item_level_unknown_on_reference_excludes_with_closed_reason_not_a_default` |
| `ITEM_LEVEL` isolamento × métricas (§12.1) | covariável | nível estrito, 4 métricas de amplitude (exclui) + 2 `NOT_ADMITTED` (nunca filtra) | N confere em cada uma | `test_item_level_isolation_at_strict_level_for_every_amplitude_metric[...]` (×4), `test_item_level_never_isolates_anyone_for_not_admitted_metrics[...]` (×2) |
| `TIER_PIECES_BAND_MISMATCH` | covariável | `abs(cand-alvo) > TIER_PIECES_BAND` no nível estrito | excluído | `test_tier_pieces_band_mismatch_excludes_amplitude_metric_at_strict_level` |
| `TIER_PIECES_UNKNOWN` | covariável | `tier_pieces` desconhecido na referência | excluído, sem default | `test_tier_pieces_unknown_on_reference_excludes_with_closed_reason_not_a_default` |
| `TIER_PIECES` isolamento × métricas (§12.1) | covariável | nível estrito, 4 + 2 (mesma divisão) | N confere em cada uma | `test_tier_pieces_isolation_at_strict_level_for_every_amplitude_metric[...]` (×4), `test_tier_pieces_never_isolates_anyone_for_not_admitted_metrics[...]` (×2) |
| `NOT_ADMITTED` (item_level/tier_pieces) | matriz §5.3 | `player_casts_per_minute`/`aura_uptime_fraction` | nunca filtra, mesmo com covariável extremamente diferente | `test_item_level_and_tier_pieces_never_filter_the_two_not_admitted_metrics[...]` (×2) |
| `INADMISSIBLE_TARGET_UNKNOWN` + `TARGET_ITEM_LEVEL_UNKNOWN`/`TARGET_TIER_PIECES_UNKNOWN` | §5.4 | valor do alvo desconhecido | covariável nunca filtra; limitação declarada | `test_target_item_level_unknown_makes_covariate_inadmissible_never_a_filter`, `test_target_tier_pieces_unknown_makes_covariate_inadmissible_never_a_filter` |
| `DURATION_BAND_MISMATCH` + `WIDEN_BAND` | covariável/ladder | duração 20% fora do alvo | excluído no nível estrito; admitido só ao alargar até a banda correta (20%, após dois passos de delta zero em tier/ilvl) | `test_duration_outside_strict_band_excludes_then_is_admitted_after_widening` |
| `DURATION_BAND_MISMATCH` × 6 métricas (§12.1) | covariável | isolamento no nível estrito, duração 1000s vs. alvo 300s (fora de qualquer ladder) | N=1, só o compatível admitido, em toda métrica | `test_duration_isolation_at_strict_level_for_every_contracted_metric[...]` (×6) |
| ladder posicional teto | ladder | `player_casts_per_minute`, 20% fora, teto do ladder é 12% | permanece excluído mesmo após esgotar o ladder posicional | `test_player_casts_per_minute_uses_the_positional_ladder_ceiling` |
| membresia exata (AC1) | seleção | 9 compatíveis + 1 com `item_level` incompatível, por métrica | conjunto exato bate com o calculado à mão (incluindo os dois casos `NOT_ADMITTED`) | `test_exact_membership_matches_hand_computed_expectation_per_metric[...]` (×6) |
| sem passo quando piso já atingido | ladder | 8 referências compatíveis | `relaxation_steps == ()` | `test_no_relaxation_step_when_strict_level_already_meets_the_floor` |
| parada após 1 covariável | ladder | só `tier_pieces` bloqueia | 1 passo, piso atingido | `test_ladder_stops_after_tier_pieces_relaxation_alone` |
| parada após 2 covariáveis | ladder | `tier_pieces` e `item_level` bloqueiam | 2 passos, ordem fixa | `test_ladder_stops_after_tier_pieces_and_item_level_relaxation` |
| avanço à duração só após covariáveis esgotadas | ladder | tudo fora de banda | ordem `TIER_PIECES, ITEM_LEVEL, DURATION, DURATION`; piso só aos 20% | `test_ladder_advances_to_duration_widening_only_after_covariates_exhausted` |
| não avança além do piso mesmo com N<15 | AC2 | exatamente 8 compatíveis | `relaxation_steps == ()` mesmo com `n < MIN_N_FOR_GRADING` | `test_ladder_never_advances_past_the_floor_even_with_n_below_grading_threshold` |
| ladder esgotado abaixo do piso | AC2 | nunca cabe em nenhuma banda | `n=0`, último passo com delta zero, `INSUFFICIENT_FOR_COMPARISON` | `test_ladder_exhausted_below_floor_reports_insufficient_without_fabricating_members` |
| passo de delta zero registrado | AC2 | relaxar `tier_pieces` não muda nada | passo presente com `n_before==n_after`, `admitted_ids==()` | `test_zero_delta_relaxation_step_is_recorded_not_omitted` |
| referência `INELIGIBLE` de M2.1 nunca promovida | AC3 | `SPEC_MISMATCH`, ladder forçado a esgotar | excluída em todo nível, motivo `BASIC_ELIGIBILITY_INELIGIBLE` + motivo original | `test_ineligible_reference_stays_excluded_at_every_relaxation_level` |
| referência `INDETERMINATE` de M2.1 nunca promovida | AC3 | `PARTITION_UNKNOWN` | excluída em todo nível, motivo `BASIC_ELIGIBILITY_INDETERMINATE` + motivo original | `test_indeterminate_reference_stays_excluded_at_every_relaxation_level` |
| escassez nunca promove `INELIGIBLE` para atingir o piso | AC3 | 1 elegível, 8 inelegíveis | `n<=1`, `INSUFFICIENT`, todos inelegíveis continuam com o motivo original | `test_scarcity_never_promotes_an_ineligible_reference_to_reach_the_floor` |
| **colisão de identidade com desacordo nunca associa log a decisão de outro objeto (R1)** | AC3/§11 | dois logs, mesmo `reference_id`, classes/uptime divergentes, ambas as ordens | `ValueError`, nunca N dependente de ordem | `test_identity_collision_with_disagreeing_class_raises_instead_of_cross_associating`, `test_identity_collision_with_disagreeing_availability_raises_instead_of_changing_n`, `test_identity_collision_also_raises_from_covariate_sensitivity` |
| **colisão inofensiva (logs idênticos) colapsa sem levantar (R1)** | AC3 | mesmo `reference_id`, logs extensionalmente iguais | colapsa determinística, sem erro | `test_harmless_identical_duplicate_collapses_deterministically_without_raising` |
| aspiracional ⊆ descritiva da mesma métrica | AC4 | 12 + 4 referências de alto DPS | `aspirational_ids <= descriptive_ids`; mesmo `metric_id` | `test_aspirational_is_a_subset_of_descriptive_of_the_same_metric` |
| `dps` não finito nunca vira zero | AC4/C06 | `dps=None`, `dps=nan` | excluído da ordenação com `ASPIRATIONAL_ORDER_UNAVAILABLE`, nunca comparado como `0.0` | `test_members_without_finite_dps_are_excluded_before_ordering_never_treated_as_zero` |
| `< REFERENCE_MIN_N` ordenáveis ⇒ `UNAVAILABLE` | AC4 | 7 referências (todas ordenáveis) | `sufficiency=UNAVAILABLE`, `members=()`, `ASPIRATIONAL_UNAVAILABLE`, sem fabricar cauda | `test_fewer_than_reference_min_n_orderable_members_makes_aspirational_unavailable` |
| aspiracional disponível declara seleção por desfecho | AC4 | 9 referências | `ASPIRATIONAL_SELECTED_ON_OUTCOME` e `ASPIRATIONAL_ORDERED_BY_WCL_DPS` presentes | `test_available_aspirational_declares_outcome_selection_and_wcl_dps_ordering` |
| excluído da cauda superior tem motivo fechado | AC4 | 1 referência de baixo DPS entre 9 altas | se excluída, motivo é `ASPIRATIONAL_NOT_IN_UPPER_TAIL` | `test_orderable_member_excluded_from_upper_tail_gets_the_closed_reason` |
| N não pertence a outra métrica | AC5 | `aura_uptime_fraction` sem observação, mesmas referências de `gross_ability_dps` | `n` e motivos completamente independentes entre `metric_id` | `test_n_and_sufficiency_belong_to_their_own_metric_id_not_borrowed` |
| `spell_id` diferente não empresta N | AC5 | mesmo conjunto de referências, `spell_id=1` vs `spell_id=999` (nunca observado) | populações independentes; 999 tem `n=0` e motivo `PLAYER_MECHANISM_NOT_OBSERVED` | `test_different_spell_ids_on_the_same_references_get_independent_populations` |
| invariância de permutação | determinismo | mesmo conjunto, todas as 720 permutações de 6 referências | membros, exclusões e passos idênticos | `test_membership_and_steps_are_invariant_to_permutation_of_the_input_sequence` |
| invariância de permutação com empate de `dps` | determinismo | 9 referências com `dps` idêntico | seleção aspiracional idêntica sob permutação | `test_aspirational_ordering_is_invariant_to_permutation_including_dps_ties` |
| round-trip canônico | determinismo | mesma entrada avaliada duas vezes | `==` e JSON canônico (`sort_keys=True`) idênticos | `test_repeated_evaluation_and_canonical_dict_round_trip_are_stable` |
| **exclusões ordenadas por reference_id, sem depender de normalização externa (R2)** | §11 | duas referências distintas excluídas por motivos diferentes, ambas as ordens de entrada | chaves idênticas e ordenadas nas duas ordens; `json.dumps` sem `sort_keys` idêntico | `test_excluded_reasons_are_ordered_by_reference_id_regardless_of_input_order`, `test_aspirational_excluded_reasons_are_also_ordered_by_reference_id` |
| métrica desconhecida | contrato | `metric="not_a_real_metric"` | `ValueError` | `test_unknown_metric_raises_value_error_instead_of_guessing` |
| `target_id` divergente | contrato | `eligibility` calculada para outro alvo | `ValueError` | `test_eligibility_target_id_mismatch_raises_value_error` |
| referência ausente da elegibilidade | contrato | referência fora do `eligibility.results` | `ValueError` | `test_reference_missing_from_eligibility_population_raises_value_error` |
| tabela de sensibilidade completa (B04) | §10.3 | mesmo cenário do teste de avanço à duração | 6 linhas (1 estrito + 2 covariáveis + 3 larguras), nunca para no piso | `test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor` |
| gate de disponibilidade fixo entre linhas | §10.3 | referência sem `uptime` observado | `n==0` em toda linha, independente de covariável/duração | `test_covariate_sensitivity_never_advances_metric_availability_gate_row_to_row` |
| **sensibilidade sobre metadados reais, tabela preservada (R3/§12.9)** | §10.3/AC6 | replay `gate1_scope`, `gross_ability_dps:1` | 4 linhas, `n=0` em todas (ver §4.2) | `test_covariate_sensitivity_over_real_gate1_scope_metadata` |
| **censo real cobrindo as seis métricas (R3/§12.10)** | AC6 | replay `gate1_scope`, cada uma das 6 métricas | motivo de `Rohanlock` específico por métrica (ver §4.1) | `test_replay_gate1_scope_rankings_census_covers_all_six_contracted_metrics` |
| fronteira de import (AC6) | — | módulo de população nunca importa pipeline/report/phase4/bot/cli | verificação estrutural via `ast` | `test_metric_population_module_imports_are_limited_to_the_declared_allowlist` |
| configuração de ferramentas (AC6) | — | Ruff `select` e Pyright do `pyproject.toml` real | verificação estrutural via `tomllib` | `test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands` |

## 2. Critério → teste → resultado (AC1–AC6)

| Critério | Teste representativo | Entrada | Esperado | Observado |
|---|---|---|---|---|
| AC1 | `test_exact_membership_matches_hand_computed_expectation_per_metric` (parametrizado nas 6 métricas de §4) | 9 referências compatíveis + 1 só com `item_level` incompatível | conjunto de membros igual ao calculado a priori por métrica | idêntico — 6 execuções passam |
| AC2 | `test_ladder_advances_to_duration_widening_only_after_covariates_exhausted` | 2 compatíveis + 6 totalmente incompatíveis (tier/ilvl/duração) | ordem `TIER_PIECES, ITEM_LEVEL, DURATION, DURATION`, piso (8) só ao alargar a duração a 20% | idêntico — os dois primeiros passos têm delta zero (SPEC exige registrá-los mesmo assim, §6.4) |
| AC3 | `test_identity_collision_with_disagreeing_class_raises_instead_of_cross_associating` **(corrige R1)** | dois logs `Duplicate`, classes Warrior/Mage, ambas as ordens | `ValueError` em toda ordem — nunca associa um log a decisão de outro objeto | idêntico — comportamento anterior (N dependente de ordem) eliminado |
| AC3 | `test_scarcity_never_promotes_an_ineligible_reference_to_reach_the_floor` | 1 referência elegível, 8 inelegíveis (`class_name` diferente) | `n<=1`, `INSUFFICIENT`; nenhuma das 8 inelegíveis é promovida, mesmo faltando 7 para o piso | idêntico — motivo `BASIC_ELIGIBILITY_INELIGIBLE` preservado em todas as 8 |
| AC4 | `test_fewer_than_reference_min_n_orderable_members_makes_aspirational_unavailable` | 7 referências descritivas, todas com `dps` finito | `aspirational.sufficiency=UNAVAILABLE`, `members=()`, `n=0`; todas as 7 excluídas com `ASPIRATIONAL_ORDER_UNAVAILABLE` | idêntico — nenhuma cauda fabricada abaixo do piso de 8 |
| AC5 | `test_n_and_sufficiency_belong_to_their_own_metric_id_not_borrowed` | mesmas 9 referências, `uptime=None` | `gross_ability_dps` tem `n=9`; `aura_uptime_fraction` tem `n=0` com motivo `UPTIME_NOT_OBSERVED` em toda referência | idêntico — nenhum empréstimo de N entre `metric_id` |
| AC6 | `test_metric_population_module_imports_are_limited_to_the_declared_allowlist` | AST real de `metric_population.py` | conjunto de nomes importados == allowlist fechada de 27 entradas; nenhum prefixo de `pipeline`/`report`/`phase4`/`bot`/`cli` | idêntico — o import novo `_sorted_by_key` é interno ao módulo, sem novo import externo |
| AC6 | `test_replay_gate1_scope_rankings_census_covers_all_six_contracted_metrics` **(corrige R3/§12.10)** | replay real, 6 métricas | motivo de `Rohanlock` específico por métrica (§4.1) | idêntico, execução real (não `skip`) |
| AC6 | `test_covariate_sensitivity_over_real_gate1_scope_metadata` **(corrige R3/§12.9, metadados reais)** | replay real, `covariate_sensitivity` | 4 linhas, tabela preservada (§4.2) | idêntico, execução real (não `skip`) |
| AC6 | `test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor` **(corrige R3/§12.9, fixture sintética — pendência da rerevisão)** | 2 compatíveis + 6 fora de banda, `covariate_sensitivity` | 6 linhas com N/membros crescentes, piso (8) só no nível 4 | idêntico — tabela completa agora preservada em §4.0, não apenas referenciada |
| §11 | `test_excluded_reasons_are_ordered_by_reference_id_regardless_of_input_order` **(corrige R2)** | 2 referências, motivos diferentes, ambas as ordens | chaves ordenadas nas duas ordens, `json.dumps` sem `sort_keys` idêntico | idêntico — a saída em si é ordenada, não apenas sob normalização externa |

## 3. Independência entre `metric_id`, colisões e determinismo

- `test_different_spell_ids_on_the_same_references_get_independent_populations`:
  mesmo conjunto de 9 referências, `spell_id=1` (observado) vs `spell_id=999`
  (nunca observado em nenhum log). `gross_ability_dps:1` tem `n=9`;
  `gross_ability_dps:999` tem `n=0`, com `PLAYER_MECHANISM_NOT_OBSERVED` em
  toda referência — motivo de M1, propagado verbatim, não reescrito.
- `test_membership_and_steps_are_invariant_to_permutation_of_the_input_sequence`:
  itera as 720 permutações (`itertools.permutations`) de 6 referências mistas
  (2 compatíveis + 4 com `tier_pieces` incompatível); membros, `excluded_reasons`
  e `relaxation_steps` idênticos em toda permutação.
- `test_repeated_evaluation_and_canonical_dict_round_trip_are_stable`: duas
  avaliações da mesma entrada produzem `MetricPopulationSet` `==` e, após
  `dataclasses.asdict` + `json.dumps(sort_keys=True)`, strings JSON idênticas.
- `test_identity_collision_with_disagreeing_class_raises_instead_of_cross_associating`
  e `test_identity_collision_with_disagreeing_availability_raises_instead_of_changing_n`:
  o contraexemplo original do revisor (§0/R1), agora fechado — `ValueError`
  em ambas as ordens, nunca resultado dependente de ordem.
- `test_harmless_identical_duplicate_collapses_deterministically_without_raising`:
  prova complementar de que a correção de R1 não introduz um bloqueio
  espúrio quando a colisão é genuinamente inofensiva (logs idênticos).

## 4. Sensibilidade e replay — tabelas preservadas

### 4.0 Tabela sintética de sensibilidade por nível (R3/§12.9 — completa a lacuna apontada pela rerevisão)

Cenário exato de
`test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor`:
alvo com `tier_pieces=4`, `item_level=400.0`, `duration_s=300.0`; 2
referências totalmente compatíveis (`Ref1_0`, `Ref1_1`) e 6 totalmente fora
de banda em `tier_pieces` (0), `item_level` (1.0) e `duration_s` (360.0,
20% acima do alvo) (`Ref200_0`..`Ref200_5`); métrica `gross_ability_dps`,
`spell_id=1`. Tabela obtida por execução real de `covariate_sensitivity`
(não calculada à mão nem fabricada) com o comando:

```powershell
.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q -s `
  tests\unit\test_m2_2_metric_population.py::test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor
```

executando, dentro do mesmo cenário do teste, `print(row)` para cada linha
retornada por `covariate_sensitivity` — reprodução equivalente também
disponível via script Python ad-hoc que importa os mesmos construtores
(`_target`, `_refs`) do arquivo de teste. `reference_id` = `report_code:fight_id:character_name`
(`damage_reference_id`); `Ref1_0`/`Ref1_1` vêm de `_refs(2, fight_id_start=1)`,
`Ref200_0..5` de `_refs(6, fight_id_start=200)`.

| `level_index` | `relaxed_so_far` | `duration_band_pct` | `n` | `member_ids` |
|---|---|---|---|---|
| 0 | `()` | 0.07 | 2 | `('REPORT:1:Ref1_0', 'REPORT:2:Ref1_1')` |
| 1 | `(TIER_PIECES,)` | 0.07 | 2 | `('REPORT:1:Ref1_0', 'REPORT:2:Ref1_1')` |
| 2 | `(TIER_PIECES, ITEM_LEVEL)` | 0.07 | 2 | `('REPORT:1:Ref1_0', 'REPORT:2:Ref1_1')` |
| 3 | `(TIER_PIECES, ITEM_LEVEL, DURATION)` | 0.12 | 2 | `('REPORT:1:Ref1_0', 'REPORT:2:Ref1_1')` |
| 4 | `(TIER_PIECES, ITEM_LEVEL, DURATION)` | 0.20 | 8 | `('REPORT:1:Ref1_0', 'REPORT:200:Ref200_0', 'REPORT:201:Ref200_1', 'REPORT:202:Ref200_2', 'REPORT:203:Ref200_3', 'REPORT:204:Ref200_4', 'REPORT:205:Ref200_5', 'REPORT:2:Ref1_1')` |
| 5 | `(TIER_PIECES, ITEM_LEVEL, DURATION)` | 0.35 | 8 | `('REPORT:1:Ref1_0', 'REPORT:200:Ref200_0', 'REPORT:201:Ref200_1', 'REPORT:202:Ref200_2', 'REPORT:203:Ref200_3', 'REPORT:204:Ref200_4', 'REPORT:205:Ref200_5', 'REPORT:2:Ref1_1')` |

Leitura da tabela: os níveis 1-2 (`DROP_FILTER` de `tier_pieces`/`item_level`)
têm delta zero — nenhum dos 6 incompatíveis passa a ser admitido só por essas
covariáveis, porque também estão fora da banda de duração; o nível 3
(`WIDEN_BAND` a 12%) também não muda nada, pois 20% de diferença ainda
excede 12%; o nível 4 (`WIDEN_BAND` a 20%) admite os 6 de uma vez, pois
20% de diferença cabe exatamente na banda de 20% (`abs(360-300)=60 <=
max(300*0.20, DURATION_FLOOR_S)=60`); o nível 5 (35%) não muda nada, já que
o piso (8) já foi atingido no nível 4. `member_ids` está ordenado por
`reference_id` em toda linha (correção R2, ainda válida). Esta é a diferença
entre a tabela de sensibilidade (nunca para no piso, §10.3) e a seleção real
(`select_metric_population`, que teria parado exatamente no nível 4) —
`test_ladder_advances_to_duration_widening_only_after_covariates_exhausted`
verifica essa segunda parte separadamente.

### 4.1 `tests/fixtures/gate1_scope/phase1_rankings.json` — censo por métrica (R3/§12.10)

Mesma fixture de 20 personagens reais que M2.1 usou (`Braska` como alvo,
19 referências, `Rohanlock` o único `ELIGIBLE` em M2.1). Nenhum desses
`PlayerLog` carrega `damage_by_ability`, `cast_timeline` ou `uptimes` — o
carregador do teste preserva exatamente os metadados de ranking disponíveis,
sem fabricar medição por habilidade; `item_level`/`tier_pieces` do alvo são
`None` (só há metadados de ranking, não um snapshot de build). O
`damage_scope` cai no default do domínio (`LEGACY_UNSCOPED`, sem
`measurement_provenance`).

Censo esperado e observado, calculado a partir do próprio caminho de
`metric_observations.observe` para cada métrica (execução real, não
fabricado — `test_replay_gate1_scope_rankings_census_covers_all_six_contracted_metrics`):

| `metric_id` | `descriptive.n` | `descriptive.sufficiency` | motivo de `Rohanlock` |
|---|---|---|---|
| `gross_ability_dps:1` | 0 | `INSUFFICIENT` | `LEGACY_UNSCOPED_SUBTOTAL` |
| `damage_per_event:1` | 0 | `INSUFFICIENT` | `LEGACY_UNSCOPED_SUBTOTAL` |
| `damage_events_per_second:1` | 0 | `INSUFFICIENT` | `LEGACY_UNSCOPED_SUBTOTAL` |
| `gross_damage_share_pct:1` | 0 | `INSUFFICIENT` | `LEGACY_UNSCOPED_SUBTOTAL` |
| `player_casts_per_minute:1` | 0 | `INSUFFICIENT` | `CAST_COVERAGE_UNKNOWN` |
| `aura_uptime_fraction:1` | 0 | `INSUFFICIENT` | `UPTIME_NOT_OBSERVED` |

As quatro primeiras compartilham o motivo porque todas passam primeiro por
`account_damage`, que retorna `PARTIAL`/`LEGACY_UNSCOPED_SUBTOTAL` para
`damage_scope=LEGACY_UNSCOPED` antes mesmo de consultar a habilidade — esse é
o resultado honesto desta fixture específica (sem proveniência de medição),
não uma fabricação. `player_casts_per_minute` e `aura_uptime_fraction`
seguem caminhos de `observe` independentes (proveniência de casts ausente;
`uptimes` vazio) e por isso têm motivos próprios. As demais 18 referências
mantêm `BASIC_ELIGIBILITY_INELIGIBLE` (propagado de M2.1) em toda métrica.
`aspirational.sufficiency` é `UNAVAILABLE` em toda métrica (0 membros
ordenáveis).

Prova de não-escrita: `snapshot_directory`/`diff_snapshots`
(`tests/fixtures/dir_snapshot.py`, hash sha256 por arquivo) antes e depois da
leitura; `diff_snapshots(before, after) == []` exigido pelo teste.

### 4.2 Tabela de sensibilidade sobre metadados reais (R3/§12.9)

`test_covariate_sensitivity_over_real_gate1_scope_metadata` executa
`covariate_sensitivity(target, references, metric="gross_ability_dps", spell_id=1, ...)`
sobre a mesma fixture. Como `item_level`/`tier_pieces` do alvo são
desconhecidos, os dois eixos são `INADMISSIBLE_TARGET_UNKNOWN` e nunca
entram no ladder (nenhuma linha de `DROP_FILTER`) — só a duração alarga:

| `level_index` | `relaxed_so_far` | `duration_band_pct` | `n` | `member_ids` |
|---|---|---|---|---|
| 0 | `()` | 0.07 | 0 | `()` |
| 1 | `(DURATION,)` | 0.12 | 0 | `()` |
| 2 | `(DURATION,)` | 0.20 | 0 | `()` |
| 3 | `(DURATION,)` | 0.35 | 0 | `()` |

`n=0` em toda linha porque a exclusão real (18 `INELIGIBLE` de M2.1 + 1
`LEGACY_UNSCOPED_SUBTOTAL` para `Rohanlock`) nunca depende de covariável nem
de duração — a tabela demonstra exatamente isso: relaxar/alargar não move
`n`, sem inventar uma tabela sintética favorável. Prova de não-escrita:
mesma técnica de `snapshot_directory`/`diff_snapshots` do teste acima.

### 4.3 `data/raw` (indisponibilidade declarada, não citada como prova de aceite)

`test_replay_over_data_raw_corpus_if_present` chama `discover_corpus_paths()`;
a lista vem vazia neste workspace (`0` arquivos `.parquet` sob `data/raw`,
confirmado nesta sessão). O teste executa `pytest.skip(...)` com mensagem
explícita apontando para os replays reais acima como prova de cobertura desta
rodada. Um teste pulado nunca é citado como prova de aceite em nenhuma
entrada da seção 2.

## 5. Comandos, ambiente, Ruff, Pyright, suíte completa

Ambiente: Windows 11, `.venv` do repositório, Python conforme `pyproject.toml`.
Variáveis de ambiente necessárias para a suíte completa (settings do bot
exigem essas cinco; sem elas, testes de CLI de experimento falham por
`pydantic_core.ValidationError`, não relacionado a M2.2) — os mesmos cinco
placeholders públicos usados pelo CI (`.github/workflows/ci.yml`):

```powershell
$env:DISCORD_TOKEN = "ci-placeholder-not-a-secret"
$env:WCL_CLIENT_ID = "ci-placeholder-not-a-secret"
$env:WCL_CLIENT_SECRET = "ci-placeholder-not-a-secret"
$env:BLIZZARD_CLIENT_ID = "ci-placeholder-not-a-secret"
$env:BLIZZARD_CLIENT_SECRET = "ci-placeholder-not-a-secret"
```

Comandos executados nesta rodada de correção, com resultado observado:

```powershell
.venv\Scripts\python.exe -B docs\submilestones\M2.2\independent-probes.py.txt
# ValueError na primeira asserção (R1): script não foi alterado, é prova do
# comportamento ANTERIOR; reprodução manual equivalente registrada em §0/R1.

.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q tests\unit\test_m2_2_metric_population.py
# 80 passed, 1 skipped in 0.87s

ruff check src\botgitgud\analysis\metric_population.py tests\unit\test_m2_2_metric_population.py
ruff format --check src\botgitgud\analysis\metric_population.py tests\unit\test_m2_2_metric_population.py
.venv\Scripts\python.exe -m pyright src\botgitgud\analysis\metric_population.py tests\unit\test_m2_2_metric_population.py
# All checks passed / 2 files already formatted / 0 errors, 0 warnings, 0 informations

ruff check .
ruff format --check .
.venv\Scripts\python.exe -m pyright
# All checks passed / 331 files already formatted / 0 errors, 0 warnings, 0 informations

.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q
# 1 failed, 2690 passed, 47 skipped, 1 deselected, 337 warnings in 243.39s
```

A única falha ampla continua sendo
`tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`,
offender `src/botgitgud/orchestrator/__main__.py` — a mesma dívida de
baseline preexistente já documentada e aceita no fechamento de M2.1 (fora do
`write_paths` desta unidade, sem relação com a política M2.2, e não citada
pela revisão independente como achado novo). Não é mascarada nem descartada.

Comparado à rodada anterior desta unidade (2658 passed, 47 skipped), esta
correção soma exatamente 32 novas provas (80 - 48 = 32 nós de teste no
arquivo do módulo): 4 de colisão de identidade (R1), 2 de ordenação de
exclusões (R2), 2 de replay sobre metadados reais (R3/§12.9-§12.10) e 24 da
matriz de isolamento por covariável × métrica (R3/§12.1, 6 funções
parametrizadas). Os `passed` da suíte ampla sobem na mesma medida
(2690 = 2658 + 32); nenhum teste existente mudou de resultado; o skip do
corpus real ausente permanece único (47 = 47).

### 5.1 Revalidação da segunda rodada (fechamento de R3/§12.9)

Esta segunda rodada não alterou `metric_population.py` nem
`test_m2_2_metric_population.py` (SHA-256 idênticos aos confirmados pela
rerevisão, §topo deste documento) — apenas preencheu a lacuna de evidência
(§4.0). As validações abaixo foram reexecutadas para confirmar que nada
regrediu com a edição deste documento:

```powershell
.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q tests\unit\test_m2_2_metric_population.py tests\unit\test_m2_1_reference_eligibility.py
# 119 passed, 2 skipped in 0.96s

ruff check .
ruff format --check .
.venv\Scripts\python.exe -m pyright
# All checks passed / 331 files already formatted / 0 errors, 0 warnings, 0 informations

.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q
# 1 failed, 2690 passed, 47 skipped, 1 deselected, 337 warnings in 157.27s
```

Números idênticos à rodada anterior em todos os totais (2690 passed, 47
skipped, 1 deselected, 1 failed — a mesma falha de baseline conhecida). A
tabela sintética de §4.0 foi verificada por reexecução direta de
`covariate_sensitivity` sobre o cenário exato do teste, campo a campo
(`level_index`, `relaxed_so_far`, `duration_band_pct`, `n`, `member_ids`),
não copiada de memória nem estimada.

## 6. Limitações residuais por marco

- **M2.2 (esta unidade):** compatibilidade de hotfix segue não verificada,
  herdada de M2.1 e preservada (`HOTFIX_COMPATIBILITY_UNVERIFIED`). Relaxar
  uma covariável não é ajustar por ela — toda população relaxada carrega
  `COVARIATE_ADJUSTMENT_UNVERIFIED`. Setup/talentos permanecem não casados por
  decisão de fronteira (`SETUP_NOT_MATCHED`, sempre que a população é não
  vazia). Censo real sobre `data/raw` fica pendente até que Parquets existam
  neste workspace (§4.3); a cobertura real desta rodada vem dos replays sobre
  `tests/fixtures/gate1_scope/` (§4.1-4.2). Colisão de `reference_id` com
  dados divergentes agora levanta `ValueError` em vez de produzir resultado
  dependente de ordem — a política de deduplicação de produto (qual log
  "vence" quando M2.3 precisar decidir) permanece explicitamente fora desta
  unidade, exatamente como M2.1 §4 já declarava.
- **M2.3:** wiring em `analysis/pipeline.py`/`cohort_match.py`, contrato de
  relatório e persistência de proveniência da política permanecem pendentes;
  nenhum desses arquivos foi tocado nesta rodada. A reconciliação declarada
  entre o piso de M2.2 e o estágio de crescimento "low bias" de `match_cohort`
  acima do piso (SPEC §6.1) é trabalho explícito de M2.3, não desta unidade.
  M2.3 também deverá decidir se/como deduplicar referências antes de chamar
  M2.2, já que M2.2 agora recusa colisões ambíguas em vez de resolvê-las.
- **M3–M6:** inalterados; nenhuma interpretação estatística de grade/quantil
  (B06), materialidade, oportunidade temporal, apresentação ou validação de
  dataset foi antecipada.
- **Fora de escopo de M2.2, registrado como baseline conhecido:** a falha
  preexistente `test_no_raw_print_calls_anywhere_in_src` (§5) não pode ser
  corrigida por esta unidade sem violar seu `write_paths`.
- **Auditoria temporal desta rodada (R3):** esta correção foi feita
  manualmente (Sonnet, sem orquestrador), portanto não há artefato
  independente de timestamp provando que a matriz precedeu o código nesta
  rodada específica — registrado honestamente em §0, não fabricado.
