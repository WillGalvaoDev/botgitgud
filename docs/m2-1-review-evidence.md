# M2.1 — evidências de elegibilidade básica das referências

Status: **IMPLEMENTATION_READY**. Resubmissão corrigindo o formato do pacote de
evidências apontado pela revisão de attempt-00004 (`EXECUTOR_EVIDENCE_DEFECT`).
Autoridade: [`m2-1-specification.md`](m2-1-specification.md), spec_sha
`721dbbaaa725962ef874ebe6b2e686edaa63ee2439c4853619e4115cd1e8b77a`. Escopo: somente M2.1.

## 0. O que mudou nesta rodada

A lógica de `src/botgitgud/analysis/reference_eligibility.py` **não mudou** em relação
à entrega anterior — apenas o pacote de evidências e a suíte de testes foram
corrigidos:

1. **AC5** tinha apenas prosa de auditoria estática ("auditoria de imports", sem node
   de pytest). Agora há um teste automatizado real,
   `test_reference_eligibility_module_imports_are_limited_to_the_declared_allowlist`,
   que faz `ast.parse` do arquivo real e compara o conjunto de nomes importados contra
   uma allowlist fechada de 10 entradas, sem depender de leitura humana do código.
2. **AC2/AC3/AC4** concatenavam múltiplos nós de teste com ponto e vírgula numa única
   entrada de evidência. Cada entrada abaixo (seção 2) agora cita exatamente um nó de
   teste completo.
3. **AC6** citava apenas os comandos do gate mecânico como se fossem o teste. Os
   comandos continuam documentados em prosa na seção 5, mas a evidência de AC6 agora
   cita dois testes reais: o replay sobre `tests/fixtures/gate1_scope/` (que executa de
   fato, não é pulado) e `test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands`,
   que lê `pyproject.toml` via `tomllib` e ancora os comandos documentados à
   configuração real do repositório.
4. `test_replay_over_data_raw_corpus_if_present` continua pulado explicitamente quando
   `data/raw` está ausente (mensagem de skip atualizada para apontar ao teste que de
   fato prova cobertura) e **nunca** é citado como prova de aceite de nenhum critério —
   um teste pulado não serve como prova de aceite aprovada.

> Os gates mecânicos (pytest, Ruff, Pyright) não foram executados nesta sessão de
> autoria — esta rodada não dispõe de ferramenta de execução. As seções abaixo
> registram a matriz definida antes da implementação e os resultados esperados,
> derivados manualmente da lógica pura e determinística implementada em
> `src/botgitgud/analysis/reference_eligibility.py`, mais os dois testes estruturais
> novos (AST de imports; leitura de `pyproject.toml` real via `tomllib`, cujos valores
> foram conferidos diretamente no arquivo do repositório: `select = ["E", "F", "I",
> "UP", "B", "SIM", "RUF", "ANN", "PTH"]`, `typeCheckingMode = "standard"`,
> `venvPath = "."`, `venv = ".venv"`). Os comandos da seção 5 são os que o gate
> mecânico deve executar após esta submissão, antes da revisão do Astra.

## 1. Matriz de casos (definida antes da implementação, SPEC §6 e §9.1–9.2)

| Código | Eixo | Situação | Veredito | Teste |
|---|---|---|---|---|
| `SELF_REFERENCE` | IDENTITY | mesma identidade (`report_code:fight_id:character_name`) do alvo | INELIGIBLE | `test_self_reference_is_ineligible` |
| `ENCOUNTER_MISMATCH` | IDENTITY | `encounter_id` diferente | INELIGIBLE | `test_encounter_mismatch_is_ineligible` |
| `DIFFICULTY_MISMATCH` | IDENTITY | `difficulty` diferente | INELIGIBLE | `test_difficulty_mismatch_is_ineligible` |
| `CLASS_MISMATCH` | IDENTITY | `class_name` normalizada diferente | INELIGIBLE | `test_class_mismatch_is_ineligible` |
| `SPEC_MISMATCH` | IDENTITY | `spec_name` normalizada diferente | INELIGIBLE | `test_spec_mismatch_is_ineligible` |
| `IDENTITY_UNKNOWN` | IDENTITY | `class_name`/`spec_name` vazia ou branca em qualquer lado | INDETERMINATE | `test_blank_class_name_is_identity_unknown_and_never_promoted_to_eligible`, `test_blank_spec_name_on_target_side_is_identity_unknown` |
| `ATTEMPT_STATE_NOT_KILL` | ATTEMPT_STATE | `reference.fight.kill` falso | INELIGIBLE | `test_reference_not_kill_is_ineligible_even_when_target_is_kill` |
| `INVALID_DURATION` | ATTEMPT_STATE | `duration_s` não finita ou <= 0 em qualquer lado (0, negativa, NaN, inf) | INELIGIBLE | `test_invalid_reference_duration_is_ineligible`, `test_invalid_target_duration_is_ineligible` (parametrizados) |
| `PARTITION_MISMATCH` | PARTITION | ambas conhecidas e diferentes | INELIGIBLE | `test_partition_mismatch_is_ineligible` |
| `PARTITION_UNKNOWN` | PARTITION | `partition` `None` em qualquer lado | INDETERMINATE | `test_reference_partition_none_is_indeterminate_and_not_defaulted`, `test_target_partition_none_is_indeterminate_and_not_defaulted_to_reference` |
| `SCOPE_UNRECONCILED` | DAMAGE_SCOPE | `UNRECONCILED` em qualquer lado | INELIGIBLE | `test_reference_unreconciled_scope_is_ineligible`, `test_target_unreconciled_scope_is_ineligible` |
| `SCOPE_MISMATCH` | DAMAGE_SCOPE | valores diferentes, nenhum `UNRECONCILED` | INELIGIBLE | `test_scope_mismatch_between_legacy_and_v1_is_ineligible` |
| `HOTFIX_NOT_OBSERVABLE` | HOTFIX | sempre (abstenção obrigatória) | ELIGIBLE (eixo) | `test_hotfix_axis_is_always_eligible_with_abstention_reason_regardless_of_other_axes`, `test_fully_compatible_reference_is_eligible_with_only_hotfix_abstention` |
| `TARGET_ATTEMPT_NOT_KILL` | população | alvo não é kill | limitação declarada | `test_target_not_kill_declares_limitation_without_excluding_kill_reference` |
| `HOTFIX_COMPATIBILITY_UNVERIFIED` | população | >=1 referência ELIGIBLE | limitação declarada | `test_hotfix_compatibility_unverified_present_only_when_some_reference_eligible` |
| `INSUFFICIENT_ELIGIBLE_REFERENCES` | população | `n_eligible < COHORT_MIN_HARD` (8) | limitação declarada | `test_insufficient_eligible_references_threshold_matches_cohort_min_hard` |
| caso feliz | todos | nenhuma violação | ELIGIBLE, `reasons == ("HOTFIX_NOT_OBSERVABLE",)` | `test_fully_compatible_reference_is_eligible_with_only_hotfix_abstention` |
| composto | IDENTITY+PARTITION | `CLASS_MISMATCH`+`ENCOUNTER_MISMATCH`+`PARTITION_MISMATCH` simultâneos | INELIGIBLE, ordem fixa por eixo depois alfabética | `test_composite_violation_orders_reasons_by_fixed_axis_order_then_alphabetically` |
| normalização | IDENTITY | `class_name`/`spec_name` diferindo só em caixa/espaço | ELIGIBLE, valor observado bruto preservado | `test_class_and_spec_name_comparison_ignores_case_and_surrounding_whitespace` |
| import boundary | — (AC5) | módulo de política nunca importa pipeline/cohort_match/report/phase4/bot/cli | N/A (verificação estrutural) | `test_reference_eligibility_module_imports_are_limited_to_the_declared_allowlist` |
| tooling config | — (AC6) | Ruff select e Pyright typeCheckingMode/venvPath/venv do repositório real | N/A (verificação estrutural) | `test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands` |

## 2. Critério → teste → resultado (AC1–AC6)

Uma entrada de evidência por nó de teste — nenhuma entrada cita mais de um teste.

| Critério | Teste | Entrada | Esperado | Observado (derivado manualmente) |
|---|---|---|---|---|
| AC1 | `test_fully_compatible_reference_is_eligible_with_only_hotfix_abstention` | alvo e referência idênticos exceto `character_name` | `decision=ELIGIBLE`, `reasons=("HOTFIX_NOT_OBSERVABLE",)`, `policy_version="reference-eligibility-v1"`, 5 eixos na ordem de `EligibilityAxis` | idêntico — os quatro primeiros eixos não geram motivo pois os campos comparados são iguais; `_evaluate_hotfix` sempre retorna `ELIGIBLE` com o motivo fixo |
| AC1 | `test_composite_violation_orders_reasons_by_fixed_axis_order_then_alphabetically` | `class_name="Warrior"`, `encounter_id+1`, `partition+1` simultâneos | `reasons==("CLASS_MISMATCH","ENCOUNTER_MISMATCH","PARTITION_MISMATCH","HOTFIX_NOT_OBSERVABLE")` | idêntico — ordem fixa dos eixos (IDENTITY, PARTITION, HOTFIX), e dentro de IDENTITY `sorted(set(reasons))` dá `CLASS_MISMATCH`<`ENCOUNTER_MISMATCH` |
| AC2 | `test_reference_partition_none_is_indeterminate_and_not_defaulted` | `reference.fight.partition=None`, alvo com `partition=4` | eixo PARTITION `INDETERMINATE`, `reasons=("PARTITION_UNKNOWN",)`, `observed_reference=None` | idêntico — `_evaluate_partition` detecta `ref_partition is None` sem fallback para o valor do alvo |
| AC2 | `test_hotfix_axis_is_always_eligible_with_abstention_reason_regardless_of_other_axes` | referência com `class_name="Warrior"` (força IDENTITY `INELIGIBLE`) | eixo HOTFIX sempre `ELIGIBLE` com `reasons=("HOTFIX_NOT_OBSERVABLE",)`, independentemente do veredito geral `INELIGIBLE` | idêntico — `_evaluate_hotfix` ignora ambos os logs e retorna sempre o mesmo `AxisVerdict` fixo |
| AC2 | `test_target_not_kill_declares_limitation_without_excluding_kill_reference` | alvo com `kill=False`, referência com `kill=True` | `TARGET_ATTEMPT_NOT_KILL` em `declared_limitations`; eixo ATTEMPT_STATE da referência permanece `ELIGIBLE` | idêntico — `evaluate_references` verifica apenas o estado do alvo para a limitação; `_evaluate_attempt_state` verifica apenas `reference.fight.kill` |
| AC3 | `test_decision_is_invariant_to_isolated_vs_batch_evaluation` | conjunto misto de 4 referências (ELIGIBLE/INELIGIBLE/INDETERMINATE) | `tuple(evaluate_reference(target, r) for r in references) == evaluate_references(target, references).results` | idêntico — `evaluate_references` apenas mapeia `evaluate_reference` sem transformação adicional |
| AC3 | `test_decision_is_invariant_to_permutation_of_the_input_sequence` | as 24 permutações do mesmo conjunto de 4 referências mistas | mapa `reference_id -> resultado` idêntico em toda permutação | idêntico — `evaluate_reference` é função pura de `(target, reference)`, nunca lê posição/índice |
| AC3 | `test_decision_is_invariant_to_size_of_the_reference_set_including_one_incompatible` | 1 referência incompatível isolada vs. cercada por 15 referências elegíveis de preenchimento | o `ReferenceEligibility` da referência incompatível é `==` nos dois casos | idêntico — `evaluate_reference` não recebe nem consulta as demais referências do conjunto |
| AC3 | `test_insufficient_eligible_references_threshold_matches_cohort_min_hard` | 7 vs. 8 referências elegíveis (`COHORT_MIN_HARD=8`, importado de `analysis/cohort.py`, não redefinido) | com 7: `n_eligible==7` e `INSUFFICIENT_ELIGIBLE_REFERENCES` presente; com 8: `n_eligible==8` e a limitação ausente | idêntico — nenhuma referência é promovida a `ELIGIBLE` por escassez |
| AC4 | `test_target_partition_none_is_indeterminate_and_not_defaulted_to_reference` | alvo com `partition=None`, referência com `partition=4` | `observed_player is None` (nunca herda o valor 4 da referência); `observed_reference=="4"` | idêntico — `_evaluate_partition` lê `target.fight.partition` diretamente, sem fallback para `ref_partition` |
| AC4 | `test_class_and_spec_name_comparison_ignores_case_and_surrounding_whitespace` | referência com `class_name=" mage "`, `spec_name=" FIRE"` | eixo IDENTITY `ELIGIBLE`, `reasons==()`; `observed_reference` contém os literais brutos `" mage "`/`" FIRE"` | idêntico — a comparação usa cópias normalizadas locais; `_identity_snapshot` usa `repr()` sobre o valor bruto original, nunca reescrevendo o observado |
| AC5 | `test_reference_eligibility_module_imports_are_limited_to_the_declared_allowlist` | AST (`ast.parse`) do arquivo real `src/botgitgud/analysis/reference_eligibility.py`, lido do disco nesta rodada | o conjunto de nomes importados é exatamente igual à allowlist fechada de 10 entradas; nenhum nome começa com `botgitgud.analysis.pipeline`/`cohort_match`/`botgitgud.report`/`phase4`/`bot`/`cli` | idêntico — o cabeçalho real do módulo contém exatamente esses 9 imports (um com 2 nomes de `collections.abc`) e nenhum outro; teste pytest real (parse de AST + comparação de conjuntos), não auditoria em prosa |
| AC6 | `test_replay_gate1_scope_rankings_census_matches_manual_expectation` | `tests/fixtures/gate1_scope/phase1_rankings.json`: 20 personagens reais de uma única luta (encontro 3181, dificuldade 5, partição 4, kill, duração 366.097s); alvo=Braska (Warlock/Destruction), 19 referências | `n_eligible=1` (Rohanlock, único outro Warlock/Destruction); `len(ineligible_ids)=18`; `indeterminate_ids=()`; `declared_limitations=={HOTFIX_COMPATIBILITY_UNVERIFIED, INSUFFICIENT_ELIGIBLE_REFERENCES}`; `diff_snapshots(before,after)==[]` | idêntico, calculado à mão a partir da lista completa dos 20 personagens da fixture; este teste EXECUTA de fato (não é `skip`) e é a prova de cobertura real exigida por AC6 nesta rodada |
| AC6 | `test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands` | `pyproject.toml` real deste repositório, lido via `tomllib` nesta rodada | `config['tool']['ruff']['lint']['select'] == {'E','F','I','UP','B','SIM','RUF','ANN','PTH'}`; `config['tool']['pyright']['typeCheckingMode']=='standard'`; `venvPath=='.'`; `venv=='.venv'` | idêntico — conferido diretamente contra o `pyproject.toml` real; ancora os comandos documentados na seção 5 à configuração real, em vez de citar os comandos como se fossem o teste |

## 3. Independência de N, determinismo e ausência de reparo por escassez

- `test_decision_is_invariant_to_isolated_vs_batch_evaluation`: compara `evaluate_reference` chamado isoladamente contra `evaluate_references(...).results` sobre o mesmo conjunto de 4 referências mistas; esperado e observado: tuplas idênticas, porque `evaluate_references` só mapeia e agrega sem estado compartilhado entre itens.
- `test_decision_is_invariant_to_permutation_of_the_input_sequence`: itera as 24 permutações (`itertools.permutations`) do mesmo conjunto de 4; esperado e observado: o mapa `reference_id -> ReferenceEligibility` é idêntico em toda permutação.
- `test_decision_is_invariant_to_size_of_the_reference_set_including_one_incompatible`: avalia uma referência incompatível isolada e depois cercada por 15 referências elegíveis de preenchimento; esperado e observado: o resultado da referência incompatível é byte-a-byte idêntico nos dois casos.
- `test_all_incompatible_population_reports_zero_eligible_without_promotion`: 3 referências (INELIGIBLE, INDETERMINATE, INELIGIBLE); esperado e observado: `n_eligible == 0`, `eligible_ids == ()`, `INSUFFICIENT_ELIGIBLE_REFERENCES` presente, nenhuma promoção.
- `test_insufficient_eligible_references_threshold_matches_cohort_min_hard`: 7 vs 8 referências elegíveis (`COHORT_MIN_HARD=8`, importado de `analysis/cohort.py`, não redefinido); esperado e observado: limitação presente com 7, ausente com 8.
- `test_repeated_evaluation_and_canonical_dict_round_trip_are_stable` e `test_population_repeated_evaluation_is_stable_via_canonical_dict`: duas chamadas com a mesma entrada produzem dataclasses `==` e, após `dataclasses.asdict` + `json.dumps(sort_keys=True)`, strings JSON idênticas — nenhuma dependência de relógio, hash de objeto ou ordem de iteração de `set`/`dict` não determinística (a agregação usa `sorted(set(reasons))`, nunca itera um `set` diretamente para saída).

## 4. Replay somente-leitura de metadados reais

### 4.1 `tests/fixtures/gate1_scope/phase1_rankings.json` (disponível neste workspace)

Fonte: relatório `PhNt3RFYW2dcf8vD`, luta 38, encontro 3181 ("Crown of the Cosmos"),
dificuldade 5, partição 4, kill, duração 366097 ms (366.097 s), 20 personagens reais
de uma única luta (2 tanks, 3 healers, 15 dps). `test_replay_gate1_scope_rankings_census_matches_manual_expectation`
constrói um `PlayerLog` por personagem (identidade/classe/spec reais; `fight`
compartilhado por serem a mesma luta; `damage_scope` no default de domínio
`LEGACY_UNSCOPED`, já que esta fixture não contém reconciliação de dano — não é
fabricado, é o default do próprio `PlayerLog`).

Alvo escolhido: `Braska` (Warlock/Destruction). Das 19 referências restantes, apenas
`Rohanlock` compartilha classe e especialização exatamente (`Warlock`/`Destruction`);
as outras 18 diferem em classe e/ou especialização (`CLASS_MISMATCH`/`SPEC_MISMATCH`).
Como todos os 20 personagens pertencem à mesma luta, `encounter_id`, `difficulty`,
`partition`, `kill` e `duration_s` são idênticos para todos — nenhuma divergência de
ATTEMPT_STATE/PARTITION/DAMAGE_SCOPE é observada nesta amostra.

Censo esperado e observado (calculado à mão a partir da lista de 20 personagens):

| Métrica | Valor |
|---|---|
| `n_eligible` | 1 (`Rohanlock`) |
| `len(ineligible_ids)` | 18 (`CLASS_MISMATCH` e/ou `SPEC_MISMATCH`) |
| `len(indeterminate_ids)` | 0 |
| `declared_limitations` | `{HOTFIX_COMPATIBILITY_UNVERIFIED, INSUFFICIENT_ELIGIBLE_REFERENCES}` (sem `TARGET_ATTEMPT_NOT_KILL`, pois o alvo é kill) |

Prova de não-escrita: `snapshot_directory`/`diff_snapshots` (`tests/fixtures/dir_snapshot.py`,
content hash sha256 por arquivo, não `mtime`) antes e depois da leitura de
`tests/fixtures/gate1_scope/`; `diff_snapshots(before, after) == []` exigido pelo teste.
A sessão também está sob o guard global `autouse` de `tests/conftest.py` que protege
`data/raw` e `data/logs` durante toda a suíte.

### 4.2 `data/raw` (indisponibilidade declarada, não citada como prova de aceite)

`test_replay_over_data_raw_corpus_if_present` chama `discover_corpus_paths()`
(`tests/fixtures/real_corpus.py`); se a lista de Parquets vier vazia, o teste executa
`pytest.skip(...)` com mensagem explícita apontando para
`test_replay_gate1_scope_rankings_census_matches_manual_expectation` como a prova de
cobertura real desta rodada e para este documento (§4). Isso é declarado explicitamente,
não fabricado nem escondido. Um teste pulado **nunca** é citado como prova de aceite em
nenhuma entrada da seção 2 — a evidência de AC6 cita exclusivamente testes que executam
de fato (§2). Se um ambiente futuro tiver Parquets em `data/raw`, o mesmo teste passa a
executar um censo real (`evaluate_references` sobre os `PlayerLog` decodificados) em vez
de pular.

## 5. Comandos, ambiente, Ruff, Pyright

Ambiente declarado pelo projeto (não medido nesta sessão, sem ferramenta de execução):
Windows 11, Python >=3.11 (`pyproject.toml`), venv em `.venv`, dependências de
desenvolvimento `pytest>=8`, `ruff>=0.5`, `pyright>=1.1` (extra `dev`).

Desde esta rodada, os valores usados pelos comandos abaixo — `select` do Ruff lint e
`typeCheckingMode`/`venvPath`/`venv` do Pyright — também são verificados por
`test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands`,
que lê `pyproject.toml` real via `tomllib`; os comandos permanecem documentados em
prosa aqui, não como evidência isolada.

Comandos que o gate mecânico deve executar após esta submissão, antes da revisão do
Astra (nenhum resultado abaixo foi observado nesta sessão de autoria):

```powershell
.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q tests/unit/test_m2_1_reference_eligibility.py
ruff check src/botgitgud/analysis/reference_eligibility.py tests/unit/test_m2_1_reference_eligibility.py
ruff format --check src/botgitgud/analysis/reference_eligibility.py tests/unit/test_m2_1_reference_eligibility.py
.venv\Scripts\python.exe -m pyright src/botgitgud/analysis/reference_eligibility.py tests/unit/test_m2_1_reference_eligibility.py
.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q
```

A última linha é a suíte completa offline, para comprovar ausência de regressão em
M0/M1 e nos demais consumidores existentes. Apenas
`src/botgitgud/analysis/reference_eligibility.py`,
`tests/unit/test_m2_1_reference_eligibility.py`, `docs/m2-1-specification.md`,
`docs/m2-1-review-evidence.md` e `docs/README.md` foram alterados por esta rodada;
nenhuma regressão de M2.1 é esperada na suíte completa.

Uma falha preexistente e não relacionada a M2.1 é conhecida neste repositório:
`tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src` detecta um
`print(` real em `src/botgitgud/orchestrator/__main__.py`. Esse arquivo está fora do
`write_paths` desta unidade (M2.1 só escreve os cinco arquivos listados acima); a falha
é registrada aqui como limitação de baseline conhecida e não relacionada, não mascarada
nem descartada — corrigi-la pertence a uma unidade que tenha `src/botgitgud/orchestrator/`
no seu próprio `write_paths`.

## 6. Limitações residuais por marco

- **M2.1 (esta unidade):** compatibilidade de hotfix dentro da mesma partição
  permanece não verificada por construção (§5.5 da SPEC); censo real sobre `data/raw`
  fica pendente até que Parquets existam neste workspace (§4.2) — a cobertura real
  desta rodada vem do replay sobre `tests/fixtures/gate1_scope/` (§4.1), que executa
  de fato.
- **M2.2:** relaxamento/seleção de covariáveis (item level, tier, buffs,
  augmentação, talentos, bandas de duração) permanece inteiramente pendente;
  `evaluate_reference`/`evaluate_references` não são consumidas por nenhum caminho
  de produção e não antecipam essa decisão.
- **M2.3:** wiring em `analysis/pipeline.py`/`cohort_match.py`, contrato de
  relatório e persistência de proveniência da política permanecem pendentes; nenhum
  desses arquivos foi tocado nesta rodada.
- **M3–M6:** inalterados; nenhuma decisão de materialidade, inferência estatística,
  oportunidade temporal, apresentação ou validação de dataset foi antecipada.
- **Fora de escopo de M2.1, registrado como baseline conhecido:** a falha preexistente
  `test_no_raw_print_calls_anywhere_in_src` (§5) não pode ser corrigida por esta
  unidade sem violar seu `write_paths`.
