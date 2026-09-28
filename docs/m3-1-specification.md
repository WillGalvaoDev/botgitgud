# M3.1 — Disponibilidade dos streams restantes (SPEC v001)

Unidade local de M3 ([`roadmap.md`](roadmap.md) §M3.1). Política nova:
**`stream-availability-v1`**. Depende dos contratos de disponibilidade de M1
([`methodology.md`](methodology.md) §1, §2.4, §2.5). Não altera nenhum contrato de M1 ou M2.

Estado: SPEC v001, **CLOSED** (implementada, testada e revisada; interface em
`analysis/stream_availability.py`). M3.2+ fora.

## 1. Objetivo e fronteira

M3.1 entrega **na interface de stream** os estados e a proveniência das auras e dos recursos
que o produto já coleta e consome. Um valor derivado desses streams passa a dizer se é zero
observado, desconhecido, parcial ou inválido, e com qual cobertura foi obtido.

M3.1 é local: **nenhum consumidor existente é religado**. `metric_observations.observe`,
M2.2, `performance_features`, findings, remediação, relatório e fase 4 continuam lendo os
campos atuais, com saída idêntica. A troca dos consumidores para a interface nova é M3.4
(§7 lista os consumidores e a lacuna de cada um).

Fora: novos sinais, motor de oportunidades (M4), semântica das grades (M3.2), materialidade e
elegibilidade de recomendação (M3.3), features experimentais (M6.1).

## 2. Auditoria do estado atual

Referências ao código em `main` @ `4bcefe3`. Corpus local `data/raw` (1361 logs) lido só como
evidência.

### 2.1 O que já existe e não precisa ser reintroduzido

- M1 já contrata estados e prova mínima para **dano e casts** (`CollectionProvenance`
  `COMPLETE/PARTIAL/UNKNOWN` com razões e intervalo, `measurement-input-v1`). Esses dois streams
  estão fora de M3.1.
- `MetricStatus` (`AVAILABLE/PARTIAL/UNKNOWN/NOT_APPLICABLE/INVALID`) e as invariantes de
  `MetricObservation` (`AVAILABLE` exige valor finito e nenhuma razão; `UNKNOWN`/`NOT_APPLICABLE`/
  `INVALID` exigem `None` e razão; `PARTIAL` pode carregar valor parcial) já existem e são
  reutilizados.
- `observe(..., "aura_uptime_fraction")` já não transforma aura ausente em zero
  (`UNKNOWN`/`UPTIME_NOT_OBSERVED`) e marca uptime fora de `[0, 1]` como `INVALID`.
- A paginação de eventos (`ingest/performance_fetch.py::_paginate_events`) já calcula
  `CollectionProvenance` para qualquer `events(...)`, inclusive recursos.
- `aura_uptime_fraction` da distribuição de uptime usa só referências que têm a aura (EC.1),
  nunca `.get(spell_id, 0.0)`.

### 2.2 Lacunas encontradas

| # | Onde | Lacuna | Critério do roadmap |
|---|---|---|---|
| F1 | `ingest/log_fetcher_aux.py::fetch_buffs_and_debuffs` | Falha da tabela Buffs ou Debuffs é engolida (`ApiError` → dict vazio). Tabela falha e aura não aplicada ficam indistinguíveis; nada é persistido sobre a cobertura. | 1, 3 |
| F2 | idem | Buffs e Debuffs escrevem no mesmo `uptimes[spell_id]`; a mesma aura nas duas tabelas é sobrescrita pela última (Debuffs), sem registro. | 3 |
| F3 | `ingest/performance_parsing.py::parse_aura_uptimes` | `totalTime` da tabela é o denominador, mas nunca é conferido com a duração da luta, embora `UNITS` declare `FIGHT_DURATION_SECONDS`. | 2, 3 |
| F4 | `ingest/performance_fetch.py::fetch_resource_waste` | A proveniência da paginação é descartada (`events, _ = ...`). Paginação parcial produz `resource_waste` com aparência integral. | 2, 3 |
| F5 | idem + `domain/models.py` | `resource_waste` é indexado pelo **rótulo localizado** (`"Fúria (Rage)"`, `"recurso #16"`); a identidade do tipo (`resourceChangeType`) só sobrevive em `resource_waste_by_ability`. | 3, 4 |
| F6 | `analysis/performance_features.py::_build_waste_findings` | Referência sem o tipo recebe `0.0` (`.get(resource_type, 0.0)`): zero fabricado. Totais absolutos são comparados entre lutas de duração diferente. | 1, 4 |
| F7 | `analysis/performance_features.py::_build_uptime_findings` | O gate de presença (70%) conta referência com tabela falhada como "sem a aura". | 5 |
| F8 | `ingest/log_fetcher_aux.py` | `external_buffs` e `has_augmentation` derivam da tabela Buffs; tabela falhada vira `frozenset()`/`False` ("nenhum buff externo"), que é covariável obrigatória de M2.2. | 1, 5 |
| F9 | `phase4/experimental_dataset.py` | `c_mean_uptime` e `waste_total` somam recursos de tipos diferentes. | 4 (M6.1) |

Fatos do corpus (somente leitura):

- nenhum log tem `measurement_provenance`; nenhum tem proveniência de aura ou recurso;
- 1274 logs são anteriores a `aura_details` (vazio) e têm `resource_waste_by_ability` vazio;
- 76 logs têm `resource_waste` vazio (sem distinção entre "sem eventos" e "falha");
- 45 entradas de uptime valem exatamente 0 e 196 entradas de desperdício valem exatamente 0;
- 1 uptime é negativo (`-0.0071`), hoje `INVALID` em `observe`;
- 280 logs têm mais de um tipo de recurso (ex.: Unholy com `Energia` e `Poder Rúnico`);
- nas tabelas gravadas, `startTime/endTime` da tabela são do report inteiro; `totalTime` é a
  duração da luta (Zarad: `345146` ms, igual a `schema_confirmado.md` §12).

## 3. Decisões

### D-M31-01 — Lista finita de streams e valores derivados

| Stream | Fonte WCL | Valores derivados cobertos |
|---|---|---|
| `AURA_BUFFS` | `table(dataType: Buffs, sourceID)` | uptime por aura, `aura_details` (usos, bandas), `external_buffs`, `has_augmentation` |
| `AURA_DEBUFFS` | `table(dataType: Debuffs, sourceID)` | uptime por aura, `aura_details` |
| `RESOURCES` | `events(dataType: Resources)` paginado, filtrado a `sourceID == jogador` | desperdício por tipo, desperdício por tipo e habilidade |

Nenhum outro stream entra em M3.1. Dano e casts continuam em M1. Mortes, tempo ativo e downtime
não são auras nem recursos e ficam fora.

### D-M31-02 — Estados e razões (lista fechada)

Reutiliza `MetricStatus`. Razões de `stream-availability-v1`:

| Estado | Razão | Significado |
|---|---|---|
| `AVAILABLE` | — | valor integral com cobertura provada (inclui zero observado) |
| `UNKNOWN` | `STREAM_UNAVAILABLE` | a resposta do stream falhou ou não veio |
| `UNKNOWN` | `STREAM_COVERAGE_UNRECORDED` | log histórico sem proveniência de stream |
| `UNKNOWN` | `AURA_NOT_LISTED` | tabela completa, aura ausente; aplicabilidade não provada |
| `UNKNOWN` | `RESOURCE_TYPE_NOT_OBSERVED` | eventos completos, nenhum evento do tipo; aplicabilidade não provada |
| `PARTIAL` | `STREAM_PARTIAL` | paginação interrompida (`API_ERROR`, `INVALID_RESPONSE`, `INVALID_EVENT`, `INVALID_CURSOR`…); valor, se houver, é explicitamente parcial |
| `PARTIAL` | `AURA_TABLE_TOTAL_TIME_MISMATCH` | `totalTime` da tabela ≠ duração da luta |
| `INVALID` | `INVALID_UPTIME` | uptime não finito ou fora de `[0, 1]` |
| `INVALID` | `AURA_TABLE_CONFLICT` | mesma aura em Buffs e Debuffs com uptimes diferentes |
| `INVALID` | `LEGACY_RESOURCE_LABEL_UNMAPPABLE` | rótulo histórico que não volta a um `resourceChangeType` |
| `INVALID` | `INVALID_DURATION` | duração da luta não finita ou ≤ 0 |

`NOT_APPLICABLE` **não é produzido** por M3.1 para estes streams: nenhuma fonte disponível
prova ausência de mecanismo por build ou versão (D-28, B05). O estado continua reservado para
quando uma fonte assim existir. Nenhum caminho produz zero a partir de ausência.

### D-M31-03 — Prova mínima de cobertura por stream

- **`AURA_BUFFS` / `AURA_DEBUFFS`** (uma resposta, agregada no servidor): `COMPLETE` quando a
  resposta tem `data` dict com `auras` lista e `totalTime` inteiro > 0 **igual** a
  `fight.endTime − fight.startTime` (ms, igualdade exata). `ApiError` ou resposta sem essa forma →
  `UNKNOWN`/`STREAM_UNAVAILABLE`. `totalTime` diferente → `PARTIAL`/`AURA_TABLE_TOTAL_TIME_MISMATCH`
  (os valores da tabela ficam registrados, mas nenhum vira `AVAILABLE`).
- **`RESOURCES`**: a `CollectionProvenance` que `_paginate_events` já calcula, agora persistida.
  `COMPLETE` → `COMPLETE`; `PARTIAL` → `PARTIAL`/`STREAM_PARTIAL` com as razões originais;
  intervalo inválido → `UNKNOWN`/`STREAM_UNAVAILABLE`.

Os dois streams de aura são independentes: a falha de um não afeta o estado do outro nem o de
`RESOURCES` (critério 5).

### D-M31-04 — Aplicabilidade e zero observado

- **Aura** listada numa tabela `COMPLETE` está provada aplicável naquele log; `totalUptime = 0`
  é **zero observado** (`AVAILABLE`, valor 0). Aura não listada numa tabela `COMPLETE` é
  `UNKNOWN`/`AURA_NOT_LISTED` — o WCL omite auras nunca aplicadas, e isso não prova que o
  mecanismo existia.
- A resposta de uptime de uma aura considera as duas tabelas: listada em ao menos uma tabela
  `COMPLETE` sem conflito → `AVAILABLE`; não listada e ao menos uma tabela não `COMPLETE` →
  herda o pior estado dessa tabela; não listada e as duas `COMPLETE` → `AURA_NOT_LISTED`.
- **Tipo de recurso** `T` está provado aplicável quando há ao menos um `resourcechange` do
  jogador com `resourceChangeType = T` numa coleta `COMPLETE`. Soma de `waste` = 0 é zero
  observado. Nenhum evento do tipo numa coleta `COMPLETE` → `UNKNOWN`/`RESOURCE_TYPE_NOT_OBSERVED`.
  Coleta vazia e `COMPLETE` (zero eventos do jogador) é um estado de stream válido: todos os tipos
  ficam `RESOURCE_TYPE_NOT_OBSERVED`.

### D-M31-05 — Normalização

- **Identidade do recurso** é o inteiro `resourceChangeType` (`Enum.PowerType`). O rótulo de
  `domain/resource_types.py` é só apresentação. Logs históricos indexados por rótulo voltam ao
  inteiro pelo inverso exato de `POWER_TYPE_LABELS` e pelo padrão `"recurso #<n>"`; qualquer
  outro rótulo é `INVALID`/`LEGACY_RESOURCE_LABEL_UNMAPPABLE`. Esta é resolução de **identidade**,
  independente do portão de cobertura de D-M31-07: mapear o rótulo de volta ao inteiro nunca
  prova que a paginação histórica registrou cobertura completa para ele, então
  `observe_resource_waste` continua `STREAM_COVERAGE_UNRECORDED` para todo log histórico,
  resolvível ou não.
- **Unidade do recurso**: a unidade bruta do WCL para aquele tipo, sem conversão de escala (ex.:
  Fragmentos de Alma chegam ×10, `schema_confirmado.md` §12). Como não há conversão, valores só
  são comparáveis dentro do mesmo tipo.
- **Observação de desperdício**: `resource_waste_per_minute:<T> = 60 · W_T / T_luta`, unidade
  `"<T> units/min"`, denominador `FIGHT_DURATION_MINUTES` (C02, C07: totais absolutos de lutas
  com durações diferentes não têm a mesma exposição). O total bruto `W_T` fica na proveniência.
- **Uptime**: `totalUptime / totalTime`, só aceito quando `totalTime` é igual à duração da luta
  (D-M31-03). Assim o denominador declarado `FIGHT_DURATION_SECONDS` passa a ser verificado.
- **Sem agregação entre tipos**: a interface não oferece soma, média ou total de recursos de
  tipos diferentes, nem de uptimes de auras diferentes (critério 4).

### D-M31-06 — Conflito Buffs × Debuffs

Mesma aura nas duas tabelas `COMPLETE`: uptimes iguais (`==`) → `AVAILABLE`, fonte `BOTH`;
diferentes → `INVALID`/`AURA_TABLE_CONFLICT`. Nunca escolhe uma das tabelas. O campo legado
`uptimes` continua com o comportamento atual (última escrita) até M3.4.

### D-M31-07 — Logs históricos

Logs sem proveniência de stream (todo o corpus atual) têm cobertura `LEGACY_UNRECORDED`:

- uptime de aura **presente** → `AVAILABLE`. É um valor de uma resposta de tabela única e
  agregada; é exatamente o que M1 já aceita como "entrada explícita". A proveniência declara
  `LEGACY_UNRECORDED` e que `totalTime` não foi verificado;
- aura ausente → `UNKNOWN`/`STREAM_COVERAGE_UNRECORDED`;
- desperdício de recurso, presente ou ausente → `UNKNOWN`/`STREAM_COVERAGE_UNRECORDED`. A
  paginação histórica não registrou cobertura, então não há prova de integralidade (mesmo
  precedente de M1 para casts sem proveniência: `CAST_COVERAGE_UNKNOWN`).

Consequência declarada: quando M3.4 religar os consumidores, referências históricas deixam de
alimentar comparações de desperdício até o log ser recoletado. Nenhum dado bruto é reescrito
(C10).

### D-M31-08 — Persistência

- Campo aditivo `PlayerLog.stream_provenance: StreamProvenance | None` (default `None`), com
  `schema_version = "stream-availability-v1"`. `measurement_provenance` e
  `measurement-input-v1` não mudam.
- `StreamProvenance` guarda, por stream: `CollectionProvenance` (status, razões, intervalo
  pedido); para cada tabela de aura, `total_time_ms` e o mapa `spell_id → (totalUptime ms,
  totalUses)` da própria tabela; para recursos, o número de eventos do jogador e, por tipo
  inteiro, `event_count` e `W_T`.
- Parquet: coluna aditiva `stream_provenance_json` (JSON canônico: chaves ordenadas,
  separadores compactos, ASCII, sem NaN). Coluna ausente ou `NULL` → `None` (histórico). Versão
  desconhecida no decode → `ValueError`.
- `RunManifest`/tabela `runs`: coluna aditiva `stream_availability_version`; linhas antigas
  ficam `NULL` e são lidas como versão desconhecida.
- Os campos atuais (`uptimes`, `aura_details`, `resource_waste`, `resource_waste_by_ability`,
  `external_buffs`, `has_augmentation`) continuam gravados como hoje, com o mesmo conteúdo para
  as mesmas respostas.

### D-M31-09 — Interface

Módulo novo `analysis/stream_availability.py`, funções puras e deterministas:

```text
stream_state(log, stream)                  -> CollectionProvenance   # com LEGACY_UNRECORDED
observe_aura_uptime(log, spell_id)         -> MetricObservation       # "aura_uptime_fraction:<sid>"
observe_resource_waste(log, resource_type) -> MetricObservation       # "resource_waste_per_minute:<T>"
external_buffs_state(log)                  -> MetricStatus + razões    # disponibilidade de F8
```

`source_identity` é `damage_reference_id(log)`. A cobertura usada vem de `stream_state`, e o
objeto retornado permite recuperar stream, status e razões do stream (critério 3). Nenhuma
função lê outro stream para decidir o estado de um stream (critério 5).

## 4. Critérios de aceite

| AC | Critério do roadmap | Prova exigida |
|---|---|---|
| AC1 | 1 — zero, desconhecido, incompleto e não aplicável distintos | Para cada stream: aura/tipo presente com valor 0 → `AVAILABLE` 0; ausente em coleta completa → `AURA_NOT_LISTED`/`RESOURCE_TYPE_NOT_OBSERVED`; stream falhado → `STREAM_UNAVAILABLE`; histórico → `STREAM_COVERAGE_UNRECORDED`; nenhum caso produz `NOT_APPLICABLE` ou zero por ausência. |
| AC2 | 2 — parcial não vira integral | Recursos com erro na página ≥ 2 → `PARTIAL`/`STREAM_PARTIAL`, valor parcial marcado, nunca `AVAILABLE`; `totalTime` divergente → `PARTIAL`. |
| AC3 | 3 — cobertura e proveniência | Toda observação recupera stream, status, razões, intervalo, `total_time_ms`/contagem de eventos; round-trip Parquet preserva `StreamProvenance` por igualdade; histórico lê `None`; versão desconhecida levanta `ValueError`. |
| AC4 | 4 — unidades diferentes não se somam | Identidade por inteiro; mapeamento de rótulos históricos (inclusive `recurso #16`) e rótulo não mapeável → `INVALID`; um log com dois tipos produz duas observações independentes; a interface não expõe agregado entre tipos (teste estrutural por AST). |
| AC5 | 5 — um stream não comprova outro | Matriz de falha isolada: cada um dos três streams falhando sozinho muda só o seu próprio estado; `external_buffs_state` segue só `AURA_BUFFS`. |
| AC6 | Preservação | Para as mesmas respostas, `uptimes`, `aura_details`, `resource_waste`, `resource_waste_by_ability`, `external_buffs`, `has_augmentation` e todo `AnalysisResult`/`ReportContract` são idênticos aos de `4bcefe3`; golden e snapshots inalterados; `reference_eligibility.py` e `metric_population.py` byte-idênticos; nenhum teste existente enfraquecido. |

## 5. Evidência

- **Respostas gravadas**: as 50 tabelas de aura e as 26 páginas de recursos já gravadas em
  `tests/fixtures/cassettes/` são o caso completo. Os casos vazio, parcial e ausente são
  derivados dessas mesmas respostas reais (tabela com `auras: []`; paginação com `ApiError` na
  segunda página; tabela ausente), nunca de números inventados.
- **Valores esperados** calculados no teste direto do JSON bruto (soma própria de `waste` por
  tipo, `totalUptime/totalTime`), sem chamar o código de produção.
- **Round-trip** de todos os estados por Parquet.
- **Censo prévio obrigatório** (primeiro passo da implementação): conferir `totalTime` contra a
  duração da luta em todas as tabelas de aura gravadas. Se alguma divergir, a implementação para
  com `TECH_BLOCK` e a SPEC é revista antes de qualquer código (a regra de igualdade exata de
  D-M31-03 depende disso).

## 6. Invariantes

- Nenhum valor `AVAILABLE` sem stream `COMPLETE` (ou, só para uptime presente, histórico
  declarado por D-M31-07).
- Nenhuma ausência produz 0; nenhum `NOT_APPLICABLE` em M3.1.
- Estados e razões são listas fechadas; ampliá-las exige `stream-availability-v2`.
- Saída determinística e independente da ordem das auras, eventos e páginas.

## 7. Consumidores (lista finita, integração em M3.4)

| # | Consumidor | Stream | Lacuna a resolver em M3.4 |
|---|---|---|---|
| K1 | `metric_observations.observe` (`aura_uptime_fraction`) → populações M2.2 de uptime | auras | razões de ausência são uma só (`UPTIME_NOT_OBSERVED`); trocar exige `metric-population-v2` porque M2.2 propaga razões literalmente |
| K2 | `performance_features._build_uptime_findings` (gate de presença 70%) | auras | denominador do gate inclui tabela falhada (F7) |
| K3 | `performance_features._build_waste_findings` | recursos | zero fabricado, total absoluto, sem cobertura (F6) |
| K4 | `findings` (`WASTE`) → `remediation` (`AGGREGATE_RESOURCE_WASTE`) → `coaching_answer` | recursos | herda K3 |
| K5 | `report/performance_text` (seções de uptime e desperdício) | ambos | herda K2/K3 |
| K6 | `pipeline._report_ability_sections` + `feature_availability` (`UPTIME`) | auras | presença usada como sinal, sem estado de stream |
| K7 | `proc_analysis` (`aura_details`) | auras | `aura_details` vazio é tratado como indisponível sem razão de stream |
| K8 | `ability_classification`, `canonical_role`, `core_ability_set` | auras | presença de id de aura usada como evidência de identidade |
| K9 | `external_buffs`/`has_augmentation` → matching e covariável M2.2 | `AURA_BUFFS` | tabela falhada vira "nenhum buff" (F8); trocar exige nova política de matching e `metric-population-v2` |
| K10 | `phase4/experimental_dataset` | ambos | soma entre tipos (F9) — M6.1, não M3.4 |

M3.1 não altera nenhum deles.

## 8. Fora de escopo e limitações

- Religar consumidores (M3.4); materialidade (M3.3); semântica de grades (M3.2).
- Prova de aplicabilidade por build, cooldowns e disponibilidade temporal (B05, M4).
- Conversão de escala entre tipos de recurso.
- Semântica de tipos observados em specs onde não são o recurso principal (ex.: `Energia` em
  Unholy) não é validada; a comparação continua só dentro do mesmo tipo.
- Recoleta de logs históricos ou backfill de proveniência (M6.2).
