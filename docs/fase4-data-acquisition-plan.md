# Plano de aquisição de dados para a Fase 4

**Data:** 2026-08-20
**Status:** proposta — **nada implementado**, aguardando aprovação do usuário.
**Escopo:** desbloquear o pré-requisito de dados da Fase 4 (`≥ 5.000 logs ingeridos para a
spec/encontro alvo`, `docs/implementacao.md` linha 1614). O requisito **não é reduzido, contornado
nem removido** neste documento. T4.1–T4.4 **não** foram iniciadas.

Todos os números marcados **[medido]** vêm de sondagens ao vivo contra a API real feitas na
elaboração deste documento (2026-08-20). Custo total das sondagens: **530 pontos** de um orçamento
de 3.600/hora — o piso de 1.000 (`api_points_floor`) nunca foi alcançado. Nenhuma coleta massiva
foi executada.

---

## 1. Estado atual da persistência

### 1.1 O que é persistido hoje

`ingest/store.py` grava em duas camadas, ambas sob `settings.data_dir` (default `data/`,
`.gitignore`-ado):

| Camada | Conteúdo |
|---|---|
| `data/warehouse.duckdb` → tabela `logs` | 21 colunas planas e indexáveis + ponteiro `parquet_path` |
| `data/raw/encounter_id=<E>/difficulty=<D>/partition=<P>/<code>_<fight>_<player>_<ts>.parquet` | o `PlayerLog` completo, 1 arquivo por log |

Colunas de `logs` (`_CREATE_LOGS_TABLE`, `store.py:63`): `report_code, fight_id, player_name,
server, encounter_id, difficulty, partition, class_name, spec_name, role, duration_s, dps,
percentile, item_level, talent_hash, tier_pieces, active_time_pct, deaths, downtime_s,
parquet_path, ingested_at`.

O Parquet (`ingest/parquet_codec.py:19`) carrega **todos** os campos do `PlayerLog`, com os campos
aninhados serializados como strings JSON: `cast_timeline_json`, `damage_by_ability_json`,
`uptimes_json`, `resource_waste_json`, `avg_targets_per_cast_json`, `talent_pairs_json`,
`phase_intervals_json`, `phase_cast_timeline_json`, mais `external_buffs` (lista) e
`has_augmentation`.

Outras tabelas: `cohort_candidates` (pool por `cohort_id`, D-25), `runs` (auditoria de relatórios,
T1.5), `spells` (criada e **nunca usada**), `jobs` (fila do T1.8).

### 1.2 Features da T3.1 reconstruíveis a partir do warehouse

**Todas.** Verificado [medido] lendo dois logs reais recém-ingeridos:

| Feature T3.1 | Origem no warehouse | OK? |
|---|---|---|
| `active_time_pct` | coluna `logs` + Parquet | ✅ |
| `damage_by_ability` | `damage_by_ability_json` (26 e 23 habilidades nos 2 logs) | ✅ |
| `uptimes` | `uptimes_json` (88 e 91 auras) | ✅ |
| `resource_waste` | `resource_waste_json` | ✅ (ver ressalva 1.4-d) |
| `deaths` / `downtime_s` | colunas `logs` + Parquet | ✅ |
| `avg_targets_per_cast` | `avg_targets_per_cast_json` | ✅ |
| `cast_timeline` (base de contagens de cast) | `cast_timeline_json` (18 spells) | ✅ |
| `phase_cast_timeline` | `phase_cast_timeline_json` | ✅ |
| Covariáveis (ilvl, tier, buffs externos, augmentation, talentos, duração) | colunas + Parquet | ✅ |
| Alvo `percentile` | coluna `logs` | ⚠️ ver 1.4-b |

O **score de alinhamento** (item `CONTROLLABLE` da T4.1) não é persistido, mas é derivável
offline: `analysis/alignment.py` o calcula a partir de `cast_timeline` + um perfil de referência,
ambos reconstruíveis do próprio warehouse. Não é um gap de persistência.

### 1.3 O schema atual suporta milhares de logs sem alteração?

**Sim.** [medido] DuckDB lê o layout hive diretamente, sem migração:

```sql
SELECT character_name, spec_name, dps, percentile, active_time_pct
FROM read_parquet('data/raw/**/*.parquet', hive_partitioning = true);
```

E os campos JSON são consultáveis **in place**, sem desempacotar em Python:

```sql
SELECT character_name,
       json_array_length(json_keys(uptimes_json))          AS n_uptimes,
       json_array_length(json_keys(damage_by_ability_json)) AS n_abilities
FROM read_parquet('data/raw/**/*.parquet', hive_partitioning = true);
-- -> ('Kílama', 88, 26), ('Peepers', 91, 23)
```

Ou seja: **a tabela de features da T4.1 pode ser materializada com SQL puro sobre o que já existe.
Nenhuma mudança de schema é necessária para a T4.1 em si.**

Tamanho [medido]: **50.960 bytes/log** em média → **~255 MB para 5.000 logs** (+ o `.duckdb`).
Aceitável. A única ressalva é o *small-file problem*: 5.000 arquivos Parquet de ~50 KB são
ineficientes de varrer; um passo de compactação (`COPY ... TO ... (FORMAT PARQUET)` por
encounter/partition) é desejável mas **não** bloqueante.

### 1.4 Gaps concretos encontrados

**(a) 🔴 `partition` nunca é populado — bloqueante para a T4.1.**
`LogFetcher._fetch_from_api` (`ingest/log_fetcher.py:257`) constrói o `FightRef` **sem** o
argumento `partition=`, então o default `None` prevalece sempre. Consequência [medido]:

```
logs.partition          -> [(None,)]
caminho do parquet      -> raw/encounter_id=3179/difficulty=5/partition=unknown/...
```

A T4.1 exige a spec/encontro **alvo**, e o próprio projeto proíbe misturar partitions
(`§1.5`, citado em `ingest/rankings.py:33`). Sem essa coluna não há como declarar o gate.
Correção é barata: `Report.rankings` devolve `partition` por fight de graça (§4.3).

**(b) 🟠 `percentile` vem de uma fonte fraca.**
`fetch_percentile` (`log_fetcher_aux.py:39`) usa
`characterData.character.encounterRankings` e casa `report.code`+`fightID`. [medido] num fight
mítico real, **só 2 de 5** jogadores DPS casaram — os outros 3 voltaram `None`. Como `percentile`
é o **alvo** da T4.1, uma taxa de perda de ~60% inviabilizaria o dataset. Existe fonte melhor
(§4.3): cobertura **169/169 = 100%** [medido].

**(c) 🟠 Duplicatas: `logs` é insert-only por design.**
D-12(c) removeu deliberadamente a PRIMARY KEY: reingerir o mesmo
`(report_code, fight_id, player_name)` **insere outra linha**. `read_log`/`has_log` lidam com
isso (`ORDER BY ingested_at DESC LIMIT 1`), mas um `SELECT * FROM logs` ingênuo para montar o
dataset de treino **contaria em dobro**. Não é um bug — é um contrato que a T4.1 precisa respeitar
explicitamente com `QUALIFY row_number() OVER (PARTITION BY report_code, fight_id, player_name
ORDER BY ingested_at DESC) = 1`.

**(d) 🟡 Chaves de `resource_waste` são strings localizadas.**
`resource_type_label()` (`domain/resource_types.py:33`) devolve `"Fragmentos de Alma"`,
`"Poder Astral"`, `"Energia"`… [medido nos logs reais]. Como nomes de coluna de ML isso é frágil
(depende de locale e de edição do dicionário). A T4.1 deve chavear pelo `resourceChangeType`
numérico e usar o label só na renderização.

**(e) 🟡 Nenhum custo é compartilhado entre jogadores do mesmo fight.**
`fetch_damage_and_targets`/`fetch_resource_waste`/`fetch_cast_timelines` já baixam os eventos
**do fight inteiro** e filtram client-side — mas `LogFetcher.fetch()` refaz esse download para
cada jogador. [medido] jogador 1 = **17,01 pontos**; jogador 2 do **mesmo fight** = **17,01
pontos**. Para ingestão em lote isso é desperdício direto (§8).

**(f) 🟡 Não existe nenhuma tabela de descoberta/checkpoint**, e `cli.py backfill` é um stub
documentado que imprime "ainda não implementado" e retorna 1 (D-13).

**(g) 🟡 `logs` não tem `kill`, `boss_name`, nem marca de origem** (interativo vs. backfill).
`kill` existe no Parquet; os outros dois não são críticos, mas a origem importa para auditoria.

### 1.5 Uma análise real feita hoje já conta como observação futura?

**Parcialmente — e hoje, na prática, não.** Três condições:

1. **Durabilidade** ✅ — `cli.py:58` e o bot usam `Store(settings.data_dir)` = `data/`, persistente.
   Cada `!analisar` grava o jogador analisado **e todos os logs de referência da coorte**
   (`LogFetcher.fetch_many` → `write_log`).
2. **Alvo presente** ⚠️ — `percentile` só nos ~40% em que a fonte fraca casa (1.4-b).
3. **Partition presente** ❌ — sempre `NULL` (1.4-a). **Isso sozinho desqualifica toda linha
   ingerida até hoje** para um gate que é definido por `(spec, encounter, difficulty, partition)`.

Corrigidos (a) e (b), toda análise interativa passa a contribuir observações válidas
retroativamente para as *novas*; as antigas precisariam de reingestão (barato: `force=True` só
recalcula, mas custa os mesmos 17 pts/log).

---

## 2. Gap entre os dados atuais e o dataset exigido pela T4.1

| Dimensão | Exigido pela T4.1 | Hoje | Gap |
|---|---|---|---|
| Volume para a spec/encontro alvo | **≥ 5.000** | **0** | 5.000 |
| Linhas persistidas no total | — | **0** (`data/` não existe no repo) | — |
| Features T3.1 por linha | todas | todas ✅ | nenhum |
| Covariáveis (ilvl/tier/buffs/duração/composição) | todas | todas ✅ | nenhum |
| Alvo `percentile` | obrigatório | ~40% de cobertura | trocar a fonte |
| `partition` | necessário para definir "alvo" | sempre `NULL` | **bloqueante** |
| Split temporal (treino < corte < validação) | obrigatório | — | precisa de spread temporal, não só de volume |
| Separação `CONTROLLABLE`/`NON_CONTROLLABLE` | declarada em código | não existe | é tarefa da T4.1, fora deste plano |

**Ponto não óbvio:** o gate não é só "5.000 linhas". A T4.1 exige **split temporal, nunca
aleatório**. 5.000 linhas todas da mesma semana satisfariam o número e seriam inúteis. O gate
precisa de um critério secundário de dispersão temporal (§10).

---

## 3. Viabilidade de acumulação orgânica

**Veredito: inviável como caminho único para o gate. Necessária como complemento.**

Como funciona hoje: cada `!analisar` persiste 1 log do jogador + até `COHORT_MAX=100` logs de
referência — mas o pool real de `characterRankings` para um `(encounter, spec, difficulty)` tem
**9–26 entradas** (`docs/schema_confirmado.md` §8, reconfirmado várias vezes nesta sessão).

Consequência aritmética: para uma dada `(spec, encounter)`, a acumulação orgânica **satura em
~26 logs de referência** (depois disso é tudo cache hit, custo zero e crescimento zero) + **1 log
novo por jogador distinto que pedir análise**. Chegar a 5.000 exigiria ~5.000 pedidos de análise
distintos daquela spec exata naquele encontro exato. Para um bot de Discord isso é da ordem de
anos, se acontecer.

O que precisa existir para que os logs orgânicos sejam **reutilizáveis** depois:

1. `partition` populado (1.4-a) — sem isso nenhuma linha orgânica entra no gate.
2. `percentile` da fonte forte (1.4-b) — senão ~60% viram linhas sem alvo.
3. Uma marca de origem e a garantia de dedup na leitura (1.4-c).

**Como contar observações válidas por `(spec, encounter, partition)`** — a consulta é a mesma do
gate (§10), sobre a view deduplicada.

**Como evitar duplicatas:** a chave natural é `(report_code, fight_id, player_name)`.
`LogFetcher.fetch()` já consulta `Store.read_log` antes de qualquer chamada de rede, então o
custo de uma duplicata é zero pontos de API; o problema é só de *contagem*, resolvido pela view
deduplicada por `ingested_at` mais recente (1.4-c).

---

## 4. Viabilidade de backfill histórico

**Veredito: tecnicamente viável.** Existe um caminho de descoberta em escala que o projeto nunca
sondou. Ele **não** é `characterRankings`.

### 4.1 `reportData.reports` — descoberta em escala ✅ [medido]

Argumentos confirmados por introspecção: `zoneID, gameZoneID, startTime, endTime, guildID,
guildName, guildServerSlug, guildServerRegion, guildTagID, userID, limit, page`.

```graphql
reportData { reports(zoneID: 46, limit: 100, page: 1) {
  from to has_more_pages data { code startTime endTime title } } }
```

| Fato | Valor [medido] |
|---|---|
| Funciona só com `zoneID` (sem guild/user) | ✅ sim |
| `total` / `last_page` | **`-1`** — a API não computa o total; não dá para saber o tamanho de antemão |
| `has_more_pages` | confiável |
| **Página máxima** | **25** — além disso: `"The maximum allowed page is 25 until the performance of paginated queries can be improved."` |
| Teto por combinação de filtro | 25 × 100 = **2.500 reports** |
| Filtro `startTime`/`endTime` | ✅ respeitado (todos os `startTime` retornados caem na janela) |
| Ordenação | **estável** entre chamadas idênticas, mas **não** ordenada por `startTime` |
| Densidade zona 46, janela de **1 dia** | **1.988 reports únicos**, esgotados em 20 páginas |
| Densidade zona 46, janela de **7 dias** | bate no teto de 2.500 (25 páginas, `has_more=true`) |

**Conclusão estrutural:** o teto de página 25 torna o janelamento temporal **obrigatório**. Janelas
diárias cabem sob o teto (1.988 < 2.500) mas com pouca folga; **janelas de 12 h** dão margem
segura, com fallback de subdivisão automática se uma janela esgotar as 25 páginas.

### 4.2 `worldData.encounter.fightRankings` — barato, mas **insuficiente e enviesado** [medido]

```graphql
worldData { encounter(id: 3179) { fightRankings(difficulty: 5, page: 1, partition: 3) } }
```
Devolve 50 kills/página com `report{code,fightID}`, `duration`, `guild`, `bracketData`,
`tanks/healers/melee/ranged`. Custo **~1 ponto/página** = 0,02 pt/kill — praticamente grátis.

**Mas o pool é limitado:** última página não vazia ≈ **20–23** para *todas* as dificuldades
testadas (3, 4 e 5) → **~1.000–1.150 kills** por `(encounter, difficulty, partition)`.

Com a melhor frequência de spec medida (1,40/kill), o teto absoluto por essa via é
**~1.600 observações** — e para Demonology (0,70/kill), **~800**. **Não chega a 5.000 para
nenhuma spec.**

Pior: é um *leaderboard*. Treinar a T4.2 nele reintroduziria o **achado 3.5 (viés de
sobrevivência)**, 🔴 que o projeto já corrigiu. Recomendação: usar `fightRankings` apenas como
fonte de *smoke test* barata, marcada em coluna própria, **nunca** como o corpo do dataset.

### 4.3 `reportData.report.rankings` — a descoberta decisiva ✅ [medido]

Campo nunca sondado pelo projeto. **Uma query, 2 pontos, por fight**:

```graphql
reportData { report(code: "...") { rankings(fightIDs: [21]) } }
```

Devolve, no nível do fight: `fightID, partition, encounter{id,name}, difficulty, size, kill,
duration, bracketData, bracket, deaths, guild, speed, execution`.
E, em `roles.dps.characters[]`, **para cada jogador**: `id, name, server{name,region}, class,
spec, amount (DPS), bracketData, bracket, rank, best, totalParses, bracketPercent,
**rankPercent**`.

| Fato | Valor [medido] |
|---|---|
| Custo | **2,0 pontos** por fight, independente do nº de jogadores |
| Cobertura de `rankPercent` | **169/169 = 100%** em 10 kills míticos amostrados |
| Comparação com a fonte atual (`characterData…encounterRankings`) | **2/5** num fight, 1 pt **por jogador** |
| `partition` | ✅ vem de graça — resolve o gap 1.4-a |
| Diversidade de performance | ✅ `rankPercent` observado de **18 a 94** num único fight |

Isto resolve simultaneamente: o alvo da T4.1, a `partition`, a identificação de `spec`/`class`
por jogador (sem baixar o log), e a triagem "este fight tem a spec alvo?" **antes** de gastar
17 pontos numa extração completa.

⚠️ Ressalva: `rankPercent` aqui é **inteiro** (48, 88, 94), enquanto `characterData` devolve float
(93,71). Para uma regressão em percentil a granularidade inteira é suficiente, mas é uma perda de
precisão que deve ser registrada, não descoberta depois.

### 4.4 Custo medido por tipo de query

Todos **líquidos** (já descontado 1,0 pt da própria chamada `rateLimitData` de medição):

| Query | Pontos |
|---|---|
| `rateLimitData` | 1,0 |
| `events(Casts)` 1 página (limit 5000) | 1,0 |
| `events(Resources)` 1 página | 1,0 |
| `table(Buffs)` / `table(Debuffs)` | 1,0 |
| `characterData…encounterRankings` (percentil, 1 jogador) | 1,0 |
| `fightRankings` 1 página (50 kills) | 1,0 |
| `reports(limit:100)` só códigos | 1,0 |
| **`report.rankings(fightIDs:[N])`** | **2,0** |
| `events(DamageDone)` 1 página (10.005 eventos) | 2,65 |
| **`QUERY_PLAYER_META`** (Summary+Casts+DamageDone+masterData) | **5,0** |
| `reports(limit:N){fights{…}}` | **~1,04 × N** (26 pts para 25 reports) |
| **`LogFetcher.fetch()` ponta a ponta, 1 jogador** | **17,0** |

⚠️ **Correção a `docs/schema_confirmado.md` §11.** Aquela seção afirma "~2,0 pontos por
requisição, uniforme entre tipos". Isso está **errado em dois sentidos**: (i) uma query simples
custa **1,0**, não 2,0 — a medição original somou o próprio `rateLimitData` de aferição; (ii) o
custo **não é uniforme** — escala com a complexidade (`META` = 5,0; `reports{fights}` = ~1 pt por
*report* atravessado). Filtrar `fights` no servidor (`encounterID`, `difficulty`, `killType`)
**não reduz o custo**: [medido] 26,0 pts nos dois casos, retornando 267 fights vs. 1 fight.

### 4.5 Oferta real (quantos kills existem)

[medido] varrendo 100 reports reais da zona 46, encontro 3179:

| Dificuldade | kills | kills/report |
|---|---|---|
| 3 (Normal) | 5 | 0,050 |
| 4 (Heroic) | 6 | 0,060 |
| 5 (Mythic) | 5 | 0,050 |
| **total** | **16** | **0,160** |

Encontros mais densos na mesma zona [medido em 50 reports]: `enc=3183 diff=0` → **0,36/report**;
`enc=3182 diff=5` → **0,26/report**; `enc=3183 diff=5` → 0,20; `enc=3456/3457/3458 diff=5` → 0,18.

Com ~2.000 reports/dia na zona 46, um encontro de densidade 0,26 rende **~520 kills por dia de
calendário** de atividade. A zona está viva há meses → a oferta histórica é da ordem de dezenas de
milhares de kills. **A oferta não é o gargalo. O orçamento de API é.**

### 4.6 Frequência de spec por kill [medido]

10 kills míticos de 3179, 169 slots de DPS, `rankPercent` em 169/169:

| Spec | por kill | | Spec | por kill |
|---|---|---|---|---|
| Evoker/Augmentation | **2,40** | | Hunter/BeastMastery | 0,90 |
| DeathKnight/Unholy | **1,40** | | Warrior/Arms | 0,90 |
| DemonHunter/Devourer | **1,30** | | Monk/Windwalker | 0,80 |
| Priest/Shadow | **1,20** | | Rogue/Subtlety | 0,70 |
| Paladin/Retribution | 1,00 | | **Warlock/Demonology** | **0,70** |
| Shaman/Elemental | 1,00 | | | |
| Mage/Frost | 1,00 | | média DPS/kill | **16,9** |

Augmentation lidera, mas `docs/implementacao.md` (linha 209) o coloca **fora de escopo como
sujeito** — deve ser excluído como alvo piloto.

---

## 5. Limitações reais encontradas na API

1. **`reports` trava na página 25.** Erro explícito do servidor. Teto duro de 2.500 reports por
   combinação de filtros → janelamento temporal obrigatório.
2. **`reports.total` e `last_page` devolvem `-1`.** Impossível estimar o tamanho do trabalho antes
   de executá-lo; o progresso só pode ser medido pelo que já foi descoberto.
3. **`fightRankings` tem teto de ~1.000–1.150 kills** por `(encounter, difficulty, partition)`, e é
   leaderboard (enviesado).
4. **`characterRankings` continua sendo leaderboard** com 9–26 entradas (§8) — irrelevante para
   volume, confirmado de novo.
5. **Custo não é uniforme** e escala com complexidade; filtro server-side em `fights` não economiza.
6. **`table(dataType: DamageDone, sourceID:)` muda o formato** (§5) → a agregação por pet continua
   exigindo baixar os eventos do fight inteiro (7 páginas no fixture, ~4 no fight de 230 s medido).
7. **`filterExpression` continua com sintaxe não determinada** (§11) — nenhuma economia disponível
   por aí.
8. **Introspecção de `LeaderboardRank` devolve "Internal server error"** e passar `leaderboard:`
   causou HTTP 400 nas três tentativas — argumento existe no schema mas não foi utilizável; não
   depender dele.
9. **Orçamento é da conta inteira** (§2): 3.600 pts/hora compartilhados entre backfill, bot
   interativo e qualquer outra coisa.
10. **Só reports públicos** aparecem em `reports` — o dataset será, por construção, uma amostra de
    raides logadas publicamente. Isso é a população certa para esta ferramenta, mas precisa ser
    declarado na T4.3.

---

## 6. Estratégia recomendada

**Coleta lenta, janelada, resumível, em três estágios desacoplados**, cada um com checkpoint
próprio, na ordem barato → caro:

```
Estágio A  DESCOBERTA      reports(zoneID, janela 12 h, páginas 1..25)
           ~1 pt/report    -> discovery_reports (código, janela, timestamps)

Estágio B  TRIAGEM         report.rankings(fightIDs:[...]) por fight candidato
           2 pts/fight     -> discovery_fights   (encounter, difficulty, PARTITION, kill, duração)
                           -> discovery_targets  (player, class, spec, rankPercent, amount, bracket)
                              ^ o alvo da T4.1 e a partition chegam AQUI, antes de qualquer extração

Estágio C  EXTRAÇÃO        LogFetcher, só para os jogadores da spec alvo
           17 pts/jogador  -> logs + parquet (o caminho de ingestão já existente, sem mudanças)
              (compartilhando as páginas de evento entre jogadores do mesmo fight)
```

Por que nesta ordem: o Estágio B custa 2 pontos e responde "este fight tem a spec alvo, na
partition alvo, com alvo `rankPercent` presente?" — evitando gastar 17 pontos numa extração que
seria descartada. É o filtro que torna o orçamento viável.

Princípios inegociáveis (§5 do pedido):
- Reusar `WclClient` (piso, retry/backoff, cache de rate limit) — nenhum caminho HTTP novo.
- Reserva interativa preservada por um piso **próprio, mais alto**, para backfill (§8.3).
- Checkpoint após **cada página** e após **cada fight** — parar é sempre seguro.
- Nunca refetch: consultar `discovery_*` e `Store.has_log` antes de qualquer chamada.
- Preferir muitas execuções curtas a uma operação gigante.

**`fightRankings` não entra no corpo do dataset** (viés de sobrevivência, §4.2); fica disponível
como fonte de bootstrap marcada, para validar o pipeline antes de gastar orçamento real.

---

## 7. Target piloto recomendado

### 7.1 Critérios (o que faz um bom primeiro alvo)

| Critério | Como medir | Peso |
|---|---|---|
| Volume disponível | `kills/report` × `spec/kill` × reports/dia | 🔴 decisivo |
| Estabilidade da partition | a partition não pode virar no meio da coleta | 🔴 decisivo |
| Disponibilidade das features | spec sem mecânica exótica que quebre a extração | 🟠 |
| Qualidade do alvo | cobertura de `rankPercent` | 🟠 |
| Diversidade de performance | dispersão de `rankPercent` (evitar dataset só de topo) | 🟠 |
| Custo de aquisição | pts/observação (§8) | 🟠 |
| Fora de escopo | Augmentation excluído como sujeito (linha 209) | 🔴 |

### 7.2 O que a evidência já permite afirmar

- **Demonology / Fallen-King Salhadaar (3179) Mythic é um alvo ruim.** Densidade
  0,05 kills/report × 0,70 Demo/kill → **48 pts por observação** (§8) — o **dobro** do melhor
  candidato. Foi escolhido como fixture porque é o caso *difícil* (70% do dano vem de pets), não
  porque é abundante.
- Um bom alvo combina **encontro denso** (0,26/report em vez de 0,05) com **spec frequente**
  (1,40/kill em vez de 0,70): Unholy DK, Devourer DH ou Shadow Priest.
- `rankPercent` teve cobertura de 100% e dispersão de 18 a 94 → qualidade e diversidade do alvo
  não são um problema em nenhum candidato.

### 7.3 Recomendação

**Não fixar a spec agora — deixar o censo decidir.** A frequência de spec foi medida em **um só
encontro** (3179) com **10 kills**; a composição varia por boss. O Estágio A+B custa ~1 pt/report
+ 2 pts/fight e **produz exatamente o censo que falta**: contagem de observações candidatas por
`(spec, encounter, difficulty, partition)`.

Plano concreto: rodar Estágios A+B sobre **~7 dias de calendário** da zona 46
(~14.000 reports ≈ **14.000 pts de descoberta** + ~2.200 fights × 2 = **4.400 pts de triagem**,
≈ **18.400 pts ≈ 7 horas** de orçamento) e escolher o alvo com números reais em mãos.

Aposta provisória, a ser confirmada pelo censo: **DeathKnight/Unholy, Mythic, partition 3, no
encontro mais denso da zona 46 (candidato `3182`, 0,26 kills/report).**

---

## 8. Estimativa de custo de API

### 8.1 Fórmula

```
pts_por_observação = [ 1/(kills_por_report) + 2 ] / (specs_alvo_por_kill) + 17
                       ^descoberta            ^triagem                      ^extração
```

### 8.2 Cenários (todos os insumos [medidos])

| Alvo | kills/report | spec/kill | pts/obs | **5.000 obs** | horas @2.700 pts/h |
|---|---|---|---|---|---|
| **Unholy DK @ enc denso (0,26) Mythic** | 0,26 | 1,40 | **21** | **~106.000** | **~39 h** |
| Devourer DH @ 0,26 | 0,26 | 1,30 | 22 | ~110.000 | ~41 h |
| Shadow Priest @ 0,26 | 0,26 | 1,20 | 22 | ~112.000 | ~41 h |
| Unholy DK @ 3179 Mythic (0,05) | 0,05 | 1,40 | 33 | ~163.000 | ~60 h |
| **Demonology @ 3179 Mythic** (fixture) | 0,05 | 0,70 | **48** | **~242.000** | **~90 h** |

Onde 2.700 pts/h = 3.600 − 25% de reserva interativa.

**Ordem de grandeza da recomendação: ~106.000 pontos ≈ 39 horas de coleta contínua** — ou, no
regime educado recomendado (~900 pts/h, 25% de ciclo de trabalho), **~118 horas ≈ 5 dias
corridos** de coleta em background. Latência HTTP não é o gargalo (~5 s/log sequencial,
~7 h totais com `max_workers=4`).

Custo do censo prévio (§7.3): **~18.400 pts ≈ 7 h**, e ele é **reaproveitado** — os reports
descobertos não precisam ser redescobertos na coleta.

### 8.3 Alternativa: extrair **todos** os DPS de cada fight

Como as páginas de evento são do fight inteiro (§1.4-e), o custo marginal de um jogador extra do
mesmo fight cai para ~2 pts (Buffs+Debuffs) se o compartilhamento for implementado:

| Modo | pts/fight | obs/fight | pts por obs **de qualquer spec** | pts por obs **da spec alvo** |
|---|---|---|---|---|
| Só a spec alvo | ~30 | 1,4 | 21 | **21** |
| Todos os 14 DPS | ~49 | 14 | **3,5** | 35 |

Ou seja: extrair tudo é **6× mais barato por linha** e banca simultaneamente o dataset de ~20
specs (5.000 alvo + ~45.000 de outras specs, úteis para futuros alvos da Fase 4), mas é **~65%
mais caro para fechar *este* gate** (~175.000 pts ≈ 65 h). É uma decisão de custo × opção futura
que cabe ao usuário (§ Perguntas).

---

## 9. Design de deduplicação / checkpoint / resume

### 9.1 Chaves de dedup

| Nível | Chave | Onde |
|---|---|---|
| Report | `report_code` | `discovery_reports` PK |
| Fight | `(report_code, fight_id)` | `discovery_fights` PK |
| Observação candidata | `(report_code, fight_id, player_name)` | `discovery_targets` PK |
| Log ingerido | `(report_code, fight_id, player_name)` + `ingested_at` mais recente | view sobre `logs` |

`logs` **continua insert-only** (D-12c preservado); a deduplicação é uma *view*, não uma
constraint:

```sql
CREATE OR REPLACE VIEW logs_latest AS
SELECT * FROM logs
QUALIFY row_number() OVER (
  PARTITION BY report_code, fight_id, player_name ORDER BY ingested_at DESC) = 1;
```

### 9.2 Checkpoint / resume

Tabela nova `backfill_checkpoints`:

```
job_key        VARCHAR   -- ex.: 'discover:zone=46'
zone_id        INTEGER
window_start   BIGINT    -- epoch ms, fechado
window_end     BIGINT
last_page      INTEGER   -- última página CONCLUÍDA (0 = nenhuma)
state          VARCHAR   -- 'pending' | 'in_progress' | 'done' | 'exhausted_cap'
points_spent   DOUBLE
updated_at     TIMESTAMP
```

Regras:
- Janelas são **fechadas, disjuntas e no passado** (12 h) → imutáveis na prática, então
  `(janela, página)` é um cursor estável apesar de `reports` não ordenar por `startTime`.
- Commit do checkpoint **após cada página** e após cada fight triado. Matar o processo a qualquer
  momento perde no máximo uma página.
- Se uma janela chegar à página 25 com `has_more_pages=true`, marcar `exhausted_cap` e
  **subdividir automaticamente** em duas janelas de 6 h (evita perda silenciosa de dados — o teto
  da §4.1 é justamente a armadilha).
- Retomar = pegar a primeira janela `pending`/`in_progress` em ordem temporal.

### 9.3 Evitar refetch

Antes de qualquer chamada de rede: (1) `report_code` já em `discovery_reports` → pular Estágio A;
(2) `(report, fight)` já em `discovery_fights` → pular triagem; (3) `Store.has_log(...)` → pular
extração (já é o comportamento de `LogFetcher.fetch`, custo zero).

### 9.4 Política de orçamento

- Reusar `WclClient` inteiro — nenhum caminho HTTP novo, nenhuma alteração no piso existente.
- Novo `JobType = "backfill"` em `bot/job_models.py`, com o **limiar mais estrito** em
  `BudgetStatus.allows`: backfill só roda com `points_remaining ≥ 50% de limit_per_hour`
  (1.800 de 3.600), acima da reserva interativa de 25% e do piso de 1.000. Ordem de prioridade
  final: `analyze` > `build_cohort` > `backfill`.
- `RateLimitBudgetExceeded` durante o backfill = **parada limpa** com checkpoint salvo e exit code
  `EX_TEMPFAIL` (75), o mesmo contrato que `build-cohort` já usa (`cli.py:109`).
- Flag `--max-points N` por execução, para o usuário limitar cada rodada independentemente do piso.

---

## 10. Definição objetiva do `FASE 4 DATA GATE`

### 10.1 Comando

```
botgitgud dataset-status --spec <Class/Spec> --encounter <id> [--difficulty N] [--partition N]
```

Saída (todos os campos pedidos):

```
FASE 4 DATA GATE — DeathKnight/Unholy @ encounter 3182, difficulty 5, partition 3

  Descoberta
    reports descobertos ................ 14.213
    fights triados ..................... 2.204
    janelas concluídas ................. 28 de 40   (2026-06-01 .. 2026-08-20)

  Ingestão
    observações candidatas (triagem) ... 3.086
    logs ingeridos ..................... 2.940
    duplicatas (ignoradas na contagem) . 51
    rejeitadas ......................... 95
      - percentile ausente ............. 38
      - partition divergente ........... 27
      - feature incompleta ............. 18
      - fight não-kill ................. 9
      - duração fora da banda .......... 3

  Observações VÁLIDAS ................ 2.845 / 5.000   (56,9%)

  Dispersão temporal
    intervalo .......................... 2026-06-02 .. 2026-08-19  (78 dias)
    antes do corte candidato (2026-07-21) .. 1.902
    depois ................................. 943

  Restante
    observações faltantes .............. 2.155
    pontos de API estimados ............ ~45.300   (~17 h @2.700 pts/h)

  FASE 4 DATA GATE: FAIL  (faltam 2.155 observações válidas)
```

### 10.2 Definição de "observação válida"

Uma linha de `logs_latest` conta **se e somente se** todas valerem:

1. `class_name`/`spec_name` == alvo;
2. `encounter_id` == alvo **e** `difficulty` == alvo;
3. `partition` == alvo **e** `partition IS NOT NULL`;
4. `percentile IS NOT NULL`;
5. `kill = true`;
6. `duration_s` dentro da banda de sanidade de ±35% da mediana do alvo (`SANITY_BAND_PCT`,
   `analysis/cohort.py`) — exclui pulls estruturalmente diferentes;
7. **completude de features**: `active_time_pct IS NOT NULL` **e** `cast_timeline_json` não vazio
   **e** `damage_by_ability_json` não vazio **e** `uptimes_json` não vazio;
8. é a linha mais recente da sua chave (garantido pela view).

Toda linha reprovada é contada com **um** motivo, na ordem acima (primeiro motivo que falhar).

### 10.3 Critério de PASS

```
FASE 4 DATA GATE: PASS
```
é declarado **se e somente se**:

- **(P1) Volume — critério principal:** `observações válidas ≥ 5.000` para o alvo declarado.
- **(P2) Dispersão temporal:** existe uma data de corte que deixa **≥ 1.000 observações de cada
  lado**. Sem isso o split temporal obrigatório da T4.1 é impossível, e P1 sozinho seria um número
  vazio.
- **(P3) Integridade:** 0 linhas com `partition IS NULL` na contagem, e 0 duplicatas não resolvidas.

P1 é o critério principal e **não é negociável nem ajustável**. P2 e P3 não relaxam P1 — são
condições adicionais que impedem um PASS tecnicamente verdadeiro e cientificamente inútil.

---

## 11. Mudanças de código necessárias — tarefas pequenas e ordenadas

Nenhuma inicia sem aprovação. Cada uma segue o §0.5 (ruff + pyright limpos, suite verde,
`docs/progresso.md` atualizado, 1 commit por tarefa) e o limite de 300 linhas por arquivo.

| # | Tarefa | Arquivos | Custo API |
|---|---|---|---|
| **T-DG.0** | **Popular `partition`** no `FightRef` | `ingest/log_fetcher.py` | 0 |
| **T-DG.1** | Query + parser de `report.rankings` | `wcl/queries.py`, novo `ingest/fight_rankings.py` | ~4 |
| **T-DG.2** | Tabelas de descoberta + checkpoint | `ingest/discovery_store.py` (novo) | 0 |
| **T-DG.3** | Estágio A: descoberta janelada e resumível | `ingest/discovery.py` (novo), `cli.py` | ~20 (teste) |
| **T-DG.4** | Estágio B: triagem via `report.rankings` | `ingest/discovery.py`, `ingest/fight_rankings.py` | ~20 (teste) |
| **T-DG.5** | `dataset-status` + view `logs_latest` | `analysis/dataset_status.py` (novo), `cli.py` | 0 |
| **T-DG.6** | Compartilhar páginas de evento por fight | `ingest/log_fetcher.py`, `ingest/performance_fetch.py` | ~35 (teste) |
| **T-DG.7** | Estágio C + `backfill` com guarda de orçamento | `cli.py`, `bot/job_models.py`, `ingest/backfill.py` (novo) | ~100 (teste) |
| **T-DG.8** | Avaliação do gate (`PASS`/`FAIL`) | `analysis/dataset_status.py`, `cli.py` | 0 |
| **T-DG.9** | *(opcional)* percentil forte no caminho interativo | `ingest/log_fetcher.py` | ~4 |
| **T-DG.10** | *(opcional)* compactação de Parquet | `ingest/store.py` | 0 |

Custo total de **desenvolvimento e teste**: **< 200 pontos**. A coleta em si (§8) é uma decisão
separada, sob controle explícito do usuário.

### Critérios de aceite verificáveis

**T-DG.0 — popular `partition`**
- `FightRef.partition` deixa de ser `None` em ingestões novas; teste unitário com resposta
  sintética afirma o valor.
- Teste de integração: um log ingerido grava em `.../partition=<N>/` e `logs.partition = N`
  (hoje: `partition=unknown` / `NULL`).
- Decidido e documentado de onde vem a partition (via T-DG.1, `report.rankings.partition`);
  se indisponível, permanece `NULL` — **nunca** um valor inventado.

**T-DG.1 — `report.rankings`**
- Parser devolve, por fight: `partition, difficulty, size, kill, duration`, e por jogador:
  `name, server, region, class, spec, amount, rank_percent, bracket_data`.
- Testes com resposta sintética cobrindo: `roles` ausente, `characters` vazio, `rankPercent`
  nulo, `dps` ausente — nenhum estoura, todos degradam para "sem observação".
- Um teste de contrato contra a cassete gravada do fight real confirma 14 DPS e 14 `rankPercent`.

**T-DG.2 — tabelas de descoberta**
- `discovery_reports`, `discovery_fights`, `discovery_targets`, `backfill_checkpoints` criadas
  idempotentemente (`CREATE TABLE IF NOT EXISTS`), pelo mesmo `Store` e sob o mesmo lock (D-19).
- Teste: inserir a mesma chave duas vezes não duplica (upsert por PK).
- Teste: `logs` continua sem PRIMARY KEY (D-12c preservado — regressão explícita).

**T-DG.3 — descoberta janelada**
- Dada uma janela, pagina de 1 a 25 e persiste checkpoint **após cada página**; matar e reexecutar
  retoma sem repetir página concluída (teste com fake client contando chamadas).
- Ao atingir página 25 com `has_more_pages=true`, marca `exhausted_cap` e **enfileira duas
  sub-janelas**; teste explícito para isso.
- Reports já conhecidos não geram chamada nova (teste conta 0 chamadas na segunda execução).
- `RateLimitBudgetExceeded` → parada limpa, checkpoint íntegro, exit 75.

**T-DG.4 — triagem**
- Só chama `report.rankings` para `(report, fight)` ainda não triados.
- Popula `discovery_targets` com uma linha por DPS, incluindo `rank_percent` e `spec`.
- Teste: um fight já triado custa 0 chamadas.

**T-DG.5 — `dataset-status`**
- Emite **todos** os campos da §10.1; sem alvo com dados, imprime zeros em vez de estourar.
- Motivos de rejeição somam exatamente `candidatas − válidas − duplicatas` (teste de identidade
  aritmética, no espírito do teste de identidade da T3.2).
- `logs_latest` colapsa reingestões: teste com 3 inserções da mesma chave devolve 1 linha,
  a de `ingested_at` maior.
- **0 pontos de API** — teste garante que o comando não instancia `WclClient`.

**T-DG.6 — compartilhamento por fight**
- Extrair 2 jogadores do mesmo fight custa **< 60%** do custo de extraí-los separadamente
  (teste com fake client contando queries: hoje 2×17; meta ≈ 15 + 2×2).
- `PlayerLog` produzido é **byte-idêntico** ao do caminho atual para o mesmo jogador
  (teste de equivalência contra a cassete existente) — otimização não pode alterar dados.

**T-DG.7 — `backfill`**
- `cli.py backfill` deixa de ser o stub do D-13; D-13 é atualizado em `docs/desvios.md`.
- Respeita `--max-points`; para no limiar de 50% e sai com 75, checkpoint salvo.
- Teste: com `points_remaining` abaixo do limiar, **nenhuma** chamada de rede é feita.
- Teste: `BudgetStatus.allows("backfill")` é `False` enquanto `allows("analyze")` é `True` na
  faixa entre 25% e 50% — a reserva interativa é comprovadamente preservada.
- Execução real limitada (`--max-points 500`) ingere ≥ 10 logs válidos e é registrada em
  `docs/progresso.md` com pontos gastos medidos.

**T-DG.8 — avaliação do gate**
- Emite literalmente `FASE 4 DATA GATE: PASS` **somente** com P1 ∧ P2 ∧ P3 (§10.3);
  caso contrário `FAIL` com o que falta.
- Teste com dataset sintético de 5.000 linhas válidas mas todas na mesma semana → **FAIL** por P2.
- Teste com 4.999 válidas → **FAIL**; com 5.000 → **PASS**.
- Teste com 1 linha de `partition NULL` → **FAIL** por P3.

---

## 12. Riscos

| Risco | Severidade | Mitigação |
|---|---|---|
| **Termos de uso da WCL para coleta em massa** | 🔴 | **Decisão do usuário** — ver Perguntas. Nenhuma coleta antes da aprovação. Coleta lenta e educada, muito abaixo do rate limit, é o perfil pretendido. |
| Partition vira no meio da coleta (patch novo) | 🟠 | `partition` gravada por observação (T-DG.0/1); o gate filtra por partition, então uma virada não corrompe o dataset — só congela o crescimento do alvo antigo. Detectável em `dataset-status`. |
| Teto de página 25 descarta dados silenciosamente | 🟠 | `exhausted_cap` + subdivisão automática de janela, com teste dedicado (T-DG.3). |
| Ordenação de `reports` não é temporal | 🟡 | Janelas fechadas no passado + dedup por `report_code`; ordenação estável verificada [medido]. |
| Viés: só reports públicos / leaderboard | 🟠 | `fightRankings` fora do corpo do dataset; a limitação "amostra de raides logadas publicamente" deve ser declarada na T4.3. |
| `rankPercent` inteiro (perda de precisão) | 🟡 | Documentar; suficiente para regressão em percentil. |
| 5.000 arquivos Parquet pequenos (~255 MB) | 🟡 | T-DG.10 (compactação), opcional e não bloqueante. |
| Coleta longa (dias/semanas) interrompida | 🟡 | Checkpoint por página; retomada é o modo normal de operação, não a exceção. |
| Backfill degradar o bot interativo | 🟠 | Limiar de 50% para `backfill`, abaixo de `analyze`; teste explícito (T-DG.7). |
| Frequência de spec medida em 1 encontro / 10 kills | 🟡 | Por isso o alvo **não** é fixado agora — o censo (§7.3) decide com números reais. |

---

## Resumo executivo

- **Chegar a 5.000 é tecnicamente viável**, por um caminho que o projeto nunca havia sondado:
  `reportData.reports` (descoberta) + `report.rankings` (triagem barata com o alvo e a partition)
  + o `LogFetcher` existente (extração).
- **`characterRankings` e `fightRankings` não resolvem** — ambos são leaderboards com teto
  (26 e ~1.150 respectivamente) e viés de sobrevivência.
- **Custo estimado: ~106.000 pontos (~39 h de coleta contínua, ~5 dias em ritmo educado)** para o
  melhor alvo; ~242.000 (~90 h) para Demonology/3179, motivo pelo qual esse alvo **não** é
  recomendado.
- **Um bloqueio real e barato de corrigir foi encontrado:** `partition` nunca é populado, o que
  hoje desqualifica 100% das linhas para um gate definido por partition.
- **Nenhuma mudança de schema é necessária** para a T4.1: DuckDB já consulta o Parquet e os campos
  JSON in place.
