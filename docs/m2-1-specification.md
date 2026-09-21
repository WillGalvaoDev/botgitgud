# M2.1 — Elegibilidade básica das referências

**Unidade:** M2.1 (macro M2 — Comparabilidade por métrica). **Status:** SPEC_READY.
Data: 2026-09-15. Autoridade normativa: [M0](m0-methodology-contract.md) (C01–C10),
[SPEC M1](m1-specification.md) (MILESTONE_CLOSED), [roadmap M2–M6](methodology-roadmap-m2-m6.md),
[workflow](milestone-workflow.md).

Este documento fecha B03 no que é necessário e suficiente para M2.1. Não altera M0/M1,
não antecipa M2.2/M2.3 e não autoriza decisões metodológicas adicionais ao executor.
O implementador deve persistir este documento verbatim em `docs/m2-1-specification.md`
e registrá-lo no índice `docs/README.md`.

## 1. Objetivo

Decidir, para cada log de referência candidato e de forma reproduzível, se ele é
**basicamente comparável** ao log do jogador analisado, usando exclusivamente
identidade, versão/partição, hotfix e estado da tentativa, a partir dos metadados
já persistidos. A decisão é **condição necessária** de comparação; não é condição
suficiente, não substitui os guardas de M1 e não afirma ajuste estatístico (C07).

## 2. Escopo e fora de escopo

Incluído:

- Módulo puro e determinístico de política de elegibilidade, com versão própria.
- Saída individual por referência: decisão, motivos ordenados, veredito por eixo e
  valores observados dos dois lados.
- Saída de população: listas ordenadas por decisão, mapa de exclusões por motivo,
  limitações declaradas e contagem elegível.
- Testes e pacote de evidências da unidade.

Fora de escopo (não implementar, não preparar “ganchos” especulativos):

- Relaxamento/seleção de covariáveis, bandas de duração, item level, tier, buffs,
  augmentation, talentos — pertencem a M2.2.
- Wiring em `analysis/pipeline.py`, `cohort_match.py`, contrato de relatório e
  persistência de proveniência da política — pertencem a M2.3.
- Grades, materialidade, oportunidades, priorização, texto CLI/Discord.
- Novas queries à API, alteração de fetchers, reescrita de `data/raw`, migração de
  Store, ML, campanha ou treinamento.
- Redefinição de qualquer fórmula, denominador ou estado de M1.

## 3. Decisão B03 — fontes autoritativas

Somente os campos abaixo, já presentes no domínio, são autoridade em v1. Nenhuma
outra fonte pode ser consultada, inferida ou sintetizada.

| Eixo | Campos autoritativos | Observação |
|---|---|---|
| IDENTITY | `fight.encounter_id`, `fight.difficulty`, `build.class_name`, `build.spec_name`, identidade do log/jogador | Comparação exata; strings normalizadas por `strip()` + `casefold()` apenas para comparação, nunca para reescrever o valor observado |
| ATTEMPT_STATE | `fight.kill`, `fight.duration_s` | Referência precisa ser kill; duração precisa ser finita e > 0 dos dois lados |
| PARTITION | `fight.partition` (`int | None`) | Único eixo temporal/de versão observável |
| DAMAGE_SCOPE | `damage_scope` (`DamageScopeVersion`) | Comparabilidade de população de dano; não substitui os guardas de M1 |
| HOTFIX | — | **Não observável.** Ver §5 |

Proibições explícitas de fonte:

- Não derivar versão de jogo, patch ou hotfix de qualquer timestamp (`start_time_ms`
  do warehouse, `startTime` de relatório, ordem de coleta, data do arquivo Parquet).
  Data não é versão de mecanismo (C07).
- Não usar `dps`/`percentile` do WCL, contagem de referências, `item_level`, tier,
  talentos, buffs ou qualquer covariável de M2.2 nesta decisão.
- Não usar ausência de dado como valor default do outro lado.

## 4. Contratos de dados

Nomes de referência; a organização em helpers pode variar, os significados não.
Implementar em `src/botgitgud/analysis/reference_eligibility.py`, com dataclasses
frozen/slots e `StrEnum`, no padrão do repositório.

```text
REFERENCE_ELIGIBILITY_POLICY_VERSION = "reference-eligibility-v1"

EligibilityDecision: ELIGIBLE | INELIGIBLE | INDETERMINATE
EligibilityAxis:     IDENTITY | ATTEMPT_STATE | PARTITION | DAMAGE_SCOPE | HOTFIX

AxisVerdict:
    axis: EligibilityAxis
    decision: EligibilityDecision
    reasons: tuple[str, ...]              # ordenados, códigos fechados de §6
    observed_player: str | None           # valor observado, "None" preservado como None
    observed_reference: str | None

ReferenceEligibility:
    policy_version: str
    reference_id: str                     # mesma identidade de measurement.damage_reference_id
    decision: EligibilityDecision
    reasons: tuple[str, ...]
    axes: tuple[AxisVerdict, ...]         # sempre os cinco eixos, na ordem de EligibilityAxis

ReferenceEligibilityPopulation:
    policy_version: str
    target_id: str
    results: tuple[ReferenceEligibility, ...]   # ordem de entrada preservada
    eligible_ids: tuple[str, ...]
    indeterminate_ids: tuple[str, ...]
    ineligible_ids: tuple[str, ...]
    excluded_reasons: Mapping[str, tuple[str, ...]]   # reference_id -> motivos
    declared_limitations: tuple[str, ...]
    n_eligible: int

evaluate_reference(target: PlayerLog, reference: PlayerLog) -> ReferenceEligibility
evaluate_references(target: PlayerLog, references: Sequence[PlayerLog])
    -> ReferenceEligibilityPopulation
```

`evaluate_reference` é função pura de exatamente dois logs: não recebe, não observa
e não pode depender do conjunto, do seu tamanho, de piso de coorte, de configuração
global, de relógio, de aleatoriedade ou de estado externo.

`evaluate_references` apenas mapeia `evaluate_reference` sobre a sequência e agrega.
Referências com `reference_id` repetido preservam uma entrada por item de entrada;
deduplicação de pull/jogador continua sendo responsabilidade de `cohort_match`.

## 5. Tabela aprovada de decisão

Todos os cinco eixos são sempre avaliados; **não há short-circuit**, para que os
motivos fiquem completos. Agregação: se algum eixo for `INELIGIBLE`, a referência é
`INELIGIBLE`; senão, se algum for `INDETERMINATE`, é `INDETERMINATE`; senão é
`ELIGIBLE`. `INDETERMINATE` nunca é promovido a `ELIGIBLE`.

### 5.1 IDENTITY

| Situação | Veredito | Motivo |
|---|---|---|
| Mesma identidade de jogador/log que o alvo | INELIGIBLE | `SELF_REFERENCE` |
| `encounter_id` diferente | INELIGIBLE | `ENCOUNTER_MISMATCH` |
| `difficulty` diferente | INELIGIBLE | `DIFFICULTY_MISMATCH` |
| `class_name` normalizada diferente | INELIGIBLE | `CLASS_MISMATCH` |
| `spec_name` normalizada diferente | INELIGIBLE | `SPEC_MISMATCH` |
| `class_name` ou `spec_name` vazia/branca em qualquer lado | INDETERMINATE | `IDENTITY_UNKNOWN` |
| Demais casos | ELIGIBLE | — |

### 5.2 ATTEMPT_STATE (kill/wipe)

| Situação | Veredito | Motivo |
|---|---|---|
| `reference.fight.kill` falso | INELIGIBLE | `ATTEMPT_STATE_NOT_KILL` |
| `duration_s` não finita ou <= 0 em qualquer lado | INELIGIBLE | `INVALID_DURATION` |
| Demais casos | ELIGIBLE | — |

Assimetria alvo/referência: quando o **alvo** não é kill, a referência kill **não**
é excluída, mas a população recebe obrigatoriamente a limitação declarada
`TARGET_ATTEMPT_NOT_KILL`. Isso registra explicitamente que não foi estabelecida
equivalência entre o estado do jogador e o das referências (M0 §5); não pode ser
omitido, e M2.3/M5 devem preservá-lo. Não converter wipe do alvo em kill, não
truncar, não reponderar.

### 5.3 PARTITION (eixo temporal/versão)

| Situação | Veredito | Motivo |
|---|---|---|
| Ambas conhecidas e iguais | ELIGIBLE | — |
| Ambas conhecidas e diferentes | INELIGIBLE | `PARTITION_MISMATCH` |
| `partition` `None` em qualquer lado | INDETERMINATE | `PARTITION_UNKNOWN` |

“Log antigo” é definido **exatamente** como partição diferente (INELIGIBLE) ou
partição desconhecida (INDETERMINATE). Idade em tempo de calendário não é critério
admissível em v1. Partição desconhecida jamais assume o valor do alvo nem a partição
default do encontro.

### 5.4 DAMAGE_SCOPE

| Situação | Veredito | Motivo |
|---|---|---|
| `UNRECONCILED` em qualquer lado | INELIGIBLE | `SCOPE_UNRECONCILED` |
| Valores de `damage_scope` diferentes entre alvo e referência | INELIGIBLE | `SCOPE_MISMATCH` |
| Valores iguais e não `UNRECONCILED` | ELIGIBLE | — |

Este eixo decide apenas comparabilidade de população de dano. A elegibilidade
quantitativa de M1 (§6 da SPEC M1: COMPLETE/PARTIAL, legado reconciliado, autoridade
`damage_table_total`) permanece vigente e é aplicada a jusante, sem alteração e sem
duplicação aqui. `ELIGIBLE` em M2.1 nunca torna um log quantitativamente válido.

### 5.5 HOTFIX

Os metadados persistidos não contêm versão de build, patch ou hotfix, nem timestamp
absoluto de luta em `FightRef`. Portanto v1 **abstém-se**: o eixo HOTFIX nunca produz
`INELIGIBLE` nem `ELIGIBLE` afirmativo de equivalência; ele emite obrigatoriamente a
limitação declarada de população `HOTFIX_COMPATIBILITY_UNVERIFIED` sempre que houver
ao menos uma referência `ELIGIBLE`, com veredito de eixo `ELIGIBLE` e motivo
`HOTFIX_NOT_OBSERVABLE` registrado em `AxisVerdict.reasons`.

Justificativa registrada: a igualdade de partição é o controle temporal mais forte
observável; excluir toda referência por falta de prova de hotfix não produziria
nenhuma prova adicional e apenas eliminaria o produto. Declarar a limitação satisfaz
C06/C07 sem fabricar equivalência. Se no futuro existir fonte autoritativa de versão
de jogo, ela exige **nova versão de política** (`reference-eligibility-v2`), nunca
reinterpretação silenciosa de v1.

## 6. Códigos de motivo (lista fechada em v1)

Por referência: `SELF_REFERENCE`, `ENCOUNTER_MISMATCH`, `DIFFICULTY_MISMATCH`,
`CLASS_MISMATCH`, `SPEC_MISMATCH`, `IDENTITY_UNKNOWN`, `ATTEMPT_STATE_NOT_KILL`,
`INVALID_DURATION`, `PARTITION_MISMATCH`, `PARTITION_UNKNOWN`, `SCOPE_UNRECONCILED`,
`SCOPE_MISMATCH`, `HOTFIX_NOT_OBSERVABLE`.

De população (`declared_limitations`): `TARGET_ATTEMPT_NOT_KILL`,
`HOTFIX_COMPATIBILITY_UNVERIFIED`, `INSUFFICIENT_ELIGIBLE_REFERENCES`.

Códigos são ASCII estáveis, em SCREAMING_SNAKE, sem texto livre, sem interpolação de
valores e sem tradução. Ordenação determinística: pela ordem fixa dos eixos e, dentro
do eixo, alfabética. Nenhum código novo pode ser criado pelo implementador.

## 7. Independência do número de referências

- A decisão de uma referência é idêntica avaliada isoladamente, em qualquer conjunto
  e sob qualquer permutação da entrada.
- Não existe parâmetro de piso, cota, alvo de N, “melhor esforço” ou modo permissivo.
- Incompatibilidade obrigatória (`INELIGIBLE`) nunca é revertida por escassez.
- `INDETERMINATE` nunca entra na população elegível.
- Quando `n_eligible < COHORT_MIN_HARD` (8, constante vigente de `analysis/cohort.py`,
  reutilizada e não redefinida), a população registra a limitação declarada
  `INSUFFICIENT_ELIGIBLE_REFERENCES`. Isso é um **estado reportado**, não uma correção:
  M2.1 não levanta exceção, não relaxa nada e não altera o fluxo de produção.

## 8. Determinismo e preservação de estado

- Mesmos metadados de entrada ⇒ mesma saída, byte a byte, em qualquer execução.
- `policy_version` acompanha cada resultado individual e a população.
- Valores observados são preservados como observados: `None` permanece `None`;
  `partition` desconhecida não vira `-1`, `0` ou o valor do alvo; string vazia não
  vira “desconhecida equivalente”; `damage_scope` não é normalizado entre versões.
- Nenhuma mutação de `PlayerLog`, de caches, do Store ou de arquivos históricos.
- Sem I/O, sem rede, sem relógio, sem aleatoriedade, sem variável de ambiente.

## 9. Testes e evidências obrigatórios

A matriz de casos deve ser **escrita antes da implementação** e preservada no pacote
de evidências, com resultado esperado definido por cálculo manual a partir da §5.

1. **Matriz positiva/negativa/desconhecida:** ao menos um caso por código de motivo da
   §6, com decisão, motivos e vereditos por eixo verificados integralmente. Casos
   compostos com múltiplos eixos violados verificam a agregação e a ordem dos motivos.
2. **Abstenções:** partição desconhecida em cada lado; identidade incompleta; hotfix
   não observável com referência elegível; alvo não-kill com referência kill.
3. **Independência de N:** propriedade verificando que a decisão de cada referência é
   invariante a (a) avaliação isolada versus em conjunto, (b) permutação da entrada,
   (c) tamanho do conjunto, incluindo conjunto com uma única referência incompatível.
4. **Ausência de reparo por escassez:** conjunto em que todas as referências são
   `INELIGIBLE`/`INDETERMINATE` produz `n_eligible == 0` e a limitação declarada,
   sem promoção de nenhuma referência.
5. **Determinismo/serialização:** repetição da avaliação e round-trip de uma
   representação canônica (dict ordenado) preservam decisão, motivos, vereditos e
   versão da política.
6. **Replay de metadados reais, somente leitura:** censo sobre os metadados já
   disponíveis no ambiente (fixtures de `tests/fixtures/gate1_scope/` e, se presentes,
   Parquets de `data/raw`), reportando contagem por decisão e por motivo, e hash de
   snapshot antes/depois provando que nada foi escrito. Se o corpus não estiver
   presente no workspace, registrar explicitamente a indisponibilidade e o que foi
   efetivamente coberto — **não fabricar** números nem declarar cobertura ausente.
7. **Estático e suíte:** Ruff (lint e formato) e Pyright sobre os arquivos novos;
   execução da seleção de testes pertinente offline. Nenhuma alteração de teste
   existente é esperada; se alguma regressão aparecer, ela é reportada com baseline,
   não mascarada.

Evidências em `docs/m2-1-review-evidence.md`: matriz critério → teste → resultado,
tabela de casos com esperado definido a priori, censo do replay, comandos exatos,
ambiente, falhas/skips e limitações residuais por marco (M2.2 covariáveis,
M2.3 propagação/persistência, M3–M6 inalterados).

## 10. Critérios de aceite

| ID | Critério |
|---|---|
| AC1 | Cada referência recebe decisão, motivos ordenados e veredito dos cinco eixos, com `policy_version`, reproduzíveis apenas a partir dos metadados de §3 |
| AC2 | Partição desconhecida, log antigo, hotfix e kill/wipe seguem exatamente §5, incluindo as abstenções e as limitações declaradas de população |
| AC3 | Decisão por referência é independente do conjunto, do tamanho e da ordem; incompatibilidade obrigatória nunca é superada por escassez ou por aumento de N |
| AC4 | Estados desconhecidos e valores observados são preservados; nenhuma equivalência temporal é fabricada e nenhuma versão é inferida de timestamp |
| AC5 | Escopo respeitado: sem covariáveis/relaxamento, sem wiring de pipeline, sem novas queries, sem alterar semântica de M1 nem dados históricos |
| AC6 | Pacote de evidências completo, com matriz definida antes da implementação, replay somente-leitura, Ruff e Pyright |

## 11. Limitações residuais declaradas

- Compatibilidade de hotfix dentro da mesma partição permanece não verificada.
- Igualdade de identidade e partição não resolve talentos, equipamento, buffs ou
  duração: isso é M2.2, e sua ausência aqui é deliberada.
- Elegibilidade básica não é suficiência amostral nem prova de ajuste estatístico.
- A política não é consumida por nenhum caminho de produção em M2.1; sua propagação,
  persistência e efeito sobre a população calculada são verificados em M2.3.
