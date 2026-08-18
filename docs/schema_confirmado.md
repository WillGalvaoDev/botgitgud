# Schema WCL v2 — fatos verificados contra a API real

**Verificado em:** 2026-08-17
**Log de referência:** `PtfBbQKRY9d6zAMC` fight 1 — Zarad (Warlock Demonology, Azralon-US)
**Método:** queries diretas à API v2 com as credenciais do projeto.

> Este arquivo é **entrada** para a T0.1, não saída. Os itens marcados ✅ já estão confirmados e
> **não precisam ser re-verificados**. Os marcados ❓ continuam sendo tarefa da T0.1.

---

## 1. Log de fixture (T0.2 / T3.1)

| Propriedade | Valor |
|---|---|
| `reportCode` | `PtfBbQKRY9d6zAMC` |
| `fightID` | `1` |
| Personagem | `Zarad` — Warlock / **Demonology** (grupo `dps`) |
| Servidor / região | `Azralon` / `US` |
| `encounterID` | `3179` — *Fallen-King Salhadaar* |
| `difficulty` | `5` (Mythic) · `size` 20 |
| `gameZone` | `2912` — The Voidspire · `zone` 46 |
| Duração | `1026037 → 1371183` = **345.146 s** |
| Dano total | `37.378.119` |
| Item level | `283` |
| `activeTime` | `344.303 ms` (**99,76%**) |
| Nº de pets | **20** (`masterData`) / 14 com dano > 0 |
| Augmentation no raid | **não** (nenhum Ebon Might / Prescience nos buffs) |

**Este único log satisfaz simultaneamente os itens 1 e 2 do Apêndice C** (log real + spec com pet).
Demonology deriva **70,4%** do dano de pets (26,3M de 37,4M) — é o caso de teste ideal para a
reconciliação de atribuição de pet exigida na T3.1.

---

## 2. Rate limit ✅

```graphql
{ rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }
```

✅ Existe. **`limitPerHour = 3600`** para esta conta. `pointsResetIn` em segundos.

⚠️ **O orçamento é da conta inteira, não por usuário.** Com o bot em servidor multiusuário
(decisão #9), todos os usuários compartilham os mesmos 3600 pontos/hora. Ver T1.8.

---

## 3. `reportData.report`

| Campo | Status | Observação |
|---|---|---|
| `revision`, `zone { id name }` | ✅ | |
| `gameVersion` **no Report** | ❌ **não existe** | está em `table(...).data.gameVersion` |
| `masterData { actors { id name type subType petOwner } }` | ✅ | **`petOwner` é o mapeamento pet→dono** |
| `fights { id encounterID name startTime endTime kill difficulty size gameZone }` | ✅ | |
| `fights { phaseTransitions { id startTime } }` | ✅ | ver §7 — **fases se repetem** |
| `phases { encounterID separatesWipes phases { id name isIntermission } }` | ✅ | retorna **todos** os encontros do report; filtrar por `encounterID` |
| `events(dataType: Casts \| DamageDone \| Resources, ...)` | ✅ | paginação por `nextPageTimestamp` |
| `table(dataType: Summary \| DamageDone \| Buffs \| Debuffs)` | ✅ | |
| `table(..., sourceID: Int)` | ✅ | funciona em `Buffs`; em `DamageDone` **muda o formato da resposta** — ver §5 |

---

## 4. `table(dataType: Summary)` ✅

Chaves de `data`: `composition, damageDone, damageTaken, deathEvents, exploitDetails,
gameVersion, healingDone, itemLevel, logFileDetails, logVersion, playerDetails, totalTime`

- `playerDetails` tem os grupos `dps`, `healers`, `tanks` ✅ (base do portão da T0.9).
- Chaves de cada jogador: `combatantInfo, guid, healthstoneUse, icon, id, maxItemLevel,
  minItemLevel, name, potionUse, region, server, specs, type`.
  - `type` = classe (`"Warlock"`), `specs` = lista (`["Demonology"]`).
  - **Item level do jogador vem de `maxItemLevel`/`minItemLevel`**, não de `combatantInfo.stats`.
- `combatantInfo`: `artifact, factionID, gear, heartOfAzeroth, specIDs, stats, talentTree, talents`
  - ⚠️ **`talents` vem VAZIO (`[]`)**. Os talentos reais estão em **`talentTree`**:
    `[{id, rank, nodeID}, ...]`. Use `talentTree` para o hash de build da T2.2.
  - ❌ **`talentTree[].id` NÃO resolve nome via `gameData.ability(id)`** (verificado ao vivo na
    T2.2: IDs reais de `talentTree`, ex. `91425`/`91430`, retornam `null`; um spell ID genuíno
    como `104316` resolve normalmente). `gameData` também não tem campo `talent` (introspecção
    `__type("GameData").fields` não lista nenhum). Do lado Blizzard, `/data/wow/talent/{id}` e
    `/data/wow/spell-tree-node/{id}` retornam 404 ao vivo. **Não há resolução de nome de talento
    disponível neste projeto** — D-26 em `docs/desvios.md`. `nodeID` é a posição na árvore, `id` é
    o identificador do talento nessa posição (não confundir os dois — `compute_talent_hash`/
    `extract_talent_pairs` usam `(nodeID, rank)`, não `id`).
  - `specIDs`: `[266]` (id numérico da spec — mais robusto que o nome).
  - `gear`: lista com `id, slot, quality, itemLevel, name, permanentEnchant, bonusIDs, setID` —
    ✅ **`setID` é o indicador confiável de peça de tier** (verificado ao vivo na T2.1, log de
    Zarad: as 4 peças "Abyssal Immolator's ..." — o tier set de Warlock — compartilham
    `setID: 1989`; toda peça não-tier tem `setID: null`). `tier_pieces` = contar itens com `setID`
    não nulo — não precisa de tabela curada de `bonusIDs` por patch.
- `damageDone` traz `{name, id, guid, type, icon, total}` por jogador — **o `total` é autoritativo**.

---

## 5. `table(dataType: DamageDone)` — ⚠️ ARMADILHA CONFIRMADA

Chaves de `data`: `entries, exploitDetails, gameVersion, logVersion, totalTime`.

Chaves de cada `entry`: `abilities, activeTime, activeTimeReduced, damageAbilities, gear, guid,
icon, id, itemLevel, name, pets, talents, targets, total, totalReduced, type`

Medições reais para Zarad:

| Item | Valor |
|---|---|
| `entry.total` | **37.378.119** ← autoritativo, **já inclui pets** |
| `sum(entry.abilities)` | 19.315.924 — **apenas 5 habilidades** |
| `sum(entry.pets)` | 26.297.092 — 14 pets |
| Agregação por **eventos** (jogador + 20 pets) | **37.378.119** — 29 habilidades, **erro 0,00%** |

**Conclusões normativas:**

1. ✅ `entry.total` **já inclui o dano de pets**. Não some pets ao total — isso causaria dupla contagem.
2. ❌ **`entry.abilities` é TRUNCADO** (5 de 29 habilidades reais). **Não use para a decomposição
   por habilidade da T3.2** — o resultado seria silenciosamente errado.
3. ✅ `entry.abilities` é uma visão **por habilidade**, atravessando jogador e pets (ex.: `Fel Firebolt`,
   guid 104318, é habilidade de Wild Imp e aparece ali). `entry.pets` é uma visão **por fonte**.
   As duas cobrem o mesmo total e **não devem ser somadas**.
4. ✅ **Método correto para o detalhamento por habilidade:** agregar eventos brutos
   `events(dataType: DamageDone)` por `abilityGameID`, somando `amount + absorbed`, onde
   `sourceID ∈ {player_id} ∪ {actors com petOwner == player_id}`. Verificado: bate exatamente.
5. ✅ `entry.activeTime` e `entry.itemLevel` disponíveis direto na tabela — não requerem query extra.
6. ⚠️ `table(dataType: DamageDone, sourceID: N)` retorna um formato **diferente** (mistura alvos e
   fontes). Não use esse atalho.

**Custo medido:** os eventos de dano deste log (345 s, 20 pets) consumiram **7 páginas**
com `limit: 10000`. Contabilize isso no orçamento da T1.8.

---

## 6. `table(dataType: Buffs)` ✅

Chaves de `data`: `auras, endTime, gameVersion, logVersion, startTime, totalTime, useTargets`.

- `auras[]`: `{guid, name, type, abilityIcon, totalUptime, totalUses, bands}`.
- ✅ `sourceID` funciona e filtra corretamente: 77 auras para Zarad.
- Uptime % = `totalUptime / data.totalTime`.
- ✅ Detecção de Augmentation (T2.1): buscar Ebon Might / Prescience na lista de auras do jogador.
  Neste log: **ausentes**, confirmando que a detecção por ausência funciona.
  ✅ `guid` confirmado em §11: `395152` (Ebon Might), `410089` (Prescience), `413984` (Shifting Sands).

---

## 7. Fases ⚠️ CORREÇÃO IMPORTANTE

`fights.phaseTransitions` para o fight 1:

```json
[{"id":1,"startTime":1026037},{"id":2,"startTime":1128366},{"id":1,"startTime":1148363},
 {"id":2,"startTime":1249977},{"id":1,"startTime":1269978}]
```

⚠️ **As fases se repetem em ciclo (1 → 2 → 1 → 2 → 1).** `phaseTransitions[].id` **não** é um
índice sequencial; é o identificador da fase, que reaparece.

**Consequência para a T2.4:** não é possível chavear o perfil por `phase_id` apenas. Use
**`(phase_id, ocorrência)`** — ex.: `(1,0), (2,0), (1,1), (2,1), (1,2)` — e alinhe dentro de cada
intervalo. Chavear só por `phase_id` misturaria a primeira e a terceira ocorrência da fase 1.

✅ **`phaseTransitions[0].startTime` == `fights[].startTime`** (verificado ao vivo: ambos
`1026037` para este fight) — a luta sempre começa exatamente no início da sua primeira fase, sem
gap. O último intervalo se estende até `fights[].endTime` (`1371183`, não incluído em
`phaseTransitions` — é implícito). `analysis/phases.py`'s `derive_phase_intervals` deriva os
limites de intervalo dessa forma: cada transição marca o início do seu próprio intervalo e o fim
do anterior; a última se estende até `endTime`.

---

## 8. `worldData.encounter.characterRankings` ⚠️ DESCOBERTAS CRÍTICAS

Chaves do objeto: `count, hasMorePages, page, rankings` ✅
Argumentos aceitos: `className, specName, metric, page, difficulty, partition, bracket` ✅

Chaves de cada ranking:
`amount, bracketData, class, duration, faction, guild, hardModeLevel, name, report{code,fightID,startTime}, server{id,name,region}, spec, startTime`

| Descoberta | Impacto |
|---|---|
| ❌ **NÃO existe campo `percentile`** | `legacy/bot.py:361` faz `r.get("percentile", 99.0)` → retorna **99.0 sempre**. O cabeçalho "Parse méd: 99 (min 99 - max 99)" é **ficção em 100% dos relatórios já gerados**. Ver §9 para a fonte real. |
| ❌ **NÃO existem `talents` nem `gear`** no ranking | O clustering de build (T2.2) exige buscar o `combatantInfo` de cada log de referência — não há atalho. |
| ✅ `amount` = DPS | Métrica direta, sem query extra. |
| ✅ `bracketData` = bracket de item level (ex. `292`) | Permite pareamento de ilvl **sem** fetch adicional. Zarad = ilvl 283, top ranker = bracket 292. |

### ⚠️⚠️ Tamanho real do pool — invalida os limiares atuais

Pool total de rankings para Warlock/Demonology no encounter 3179:

| Dificuldade | `count` | `hasMorePages` | Faixa de duração |
|---|---|---|---|
| (default) | 26 | **false** | 146–356 s |
| 3 (Normal) | 38 | false | 104–372 s |
| 4 (Heroic) | 20 | false | 144–281 s |
| 5 (Mythic) | 26 | false | 146–356 s |

Aplicando o filtro de duração da T0.8 ao kill de Zarad (345 s), sobre os 26 logs Mythic:

| Tolerância | Logs restantes |
|---|---|
| ±7% (24 s) | **2** |
| ±12% (41 s) | **3** |
| ±20% (69 s) | **3** |

**O `COHORT_MAX = 100` é inalcançável, e `COHORT_MIN_HARD = 10` recusaria esta análise.**
`characterRankings` não é uma amostra da população — é o **leaderboard**, e para conteúdo recente
ou specs menos populares ele tem dezenas de entradas, não centenas.

Note também que o kill de Zarad (345 s) está no **extremo lento** da faixa (146–356 s): parear por
duração empurra a referência para o fundo do leaderboard.

**Isto força a mudança de metodologia descrita na T2.1 revisada: duração vira covariável de
ajuste, não filtro.** Descartar 24 de 26 observações para ficar com 2 é estatisticamente pior do
que usar as 26 e normalizar.

---

## 9. `characterData.character.encounterRankings` ✅ — fonte real do percentil

```graphql
characterData { character(name:"Zarad", serverSlug:"azralon", serverRegion:"US") {
  encounterRankings(encounterID: 3179, metric: dps, difficulty: 5) } }
```

Chaves: `averagePerformance, bestAmount, difficulty, fastestKill, medianPerformance, metric,
partition, ranks, totalKills, zone` ✅

Chaves de cada `ranks[]`: `amount, bestSpec, bracketData, class, duration, faction, guild,
historicalPercent, historicalTotalParses, lockedIn, rankPercent, rankTotalParses, report, spec,
startTime, todayPercent, todayTotalParses`

- ✅ **`rankPercent` é o parse do jogador** (exemplo observado: `71.08`).
- Para achar o parse **daquele kill específico**, casar `ranks[].report.code` + `fightID` com a
  análise em curso.
- `medianPerformance` / `averagePerformance` do personagem servem de contexto histórico.

**Ação para a T0.7:** o parse do jogador vem daqui. O "parse médio da coorte" **não é obtenível**
do leaderboard (§8) — remova esse campo do cabeçalho em vez de inventá-lo.

---

## 10. `events(dataType: Resources)` ✅

Formato confirmado:

```json
{"timestamp":1026209,"type":"resourcechange","sourceID":13,"targetID":13,
 "abilityGameID":194192,"fight":1,"resourceChange":1,"resourceChangeType":7,
 "otherResourceChange":0,"maxResourceAmount":50,"waste":0}
```

✅ `waste` disponível diretamente. `resourceChangeType` identifica o tipo de recurso.

---

## 10b. Cooldown de habilidade — ❌ NENHUMA API EXPÕE (verificado na T2.5)

- `GET /data/wow/spell/{id}` (Blizzard) retorna só `id, name, description, media` — testado
  contra 3 IDs reais (104316 Call Dreadstalkers, 1122 Summon Infernal, 267171 Demonic Strength).
  Nenhum campo de cooldown em nenhum dos três.
- `gameData.ability(id)` (WCL) — tipo GraphQL `GameAbility` — expõe só `id, icon, name`. Mesma
  lacuna do lado da WCL.
- **Consequência:** `domain/cooldowns.py`'s tabela curada (fonte 2 da T2.5) começa vazia — não há
  fonte de API verificável para preenchê-la. Ver D-28 em `docs/desvios.md`.

---

## 11. Itens da sondagem original — resolvidos em T0.1

Todos os itens abaixo foram fechados durante a execução formal da T0.1
(`src/botgitgud/wcl/schema_probe.py`, saída completa em `docs/schema_probe_output.md`).

| Item | Resultado |
|---|---|
| ✅ `guid` de Ebon Might / Prescience | **`395152`** (Ebon Might) e **`410089`** (Prescience), confirmados via `gameData.ability(id)`. Buff auxiliar relacionado: **`413984`** (Shifting Sands). Use estes 3 IDs para detectar `has_augmentation` na T2.1. |
| ✅ `table(dataType: Debuffs)` tem o mesmo formato de `Buffs`? | **Sim, idêntico.** Mesma estrutura `data.auras[]` com `{guid, name, type, abilityIcon, totalUptime, totalUses, bands}`. |
| ⚠️ Existe `filterExpression` que evite paginar eventos de dano? | O argumento **existe** em `events(...)` (confirmado por introspecção), mas a sintaxe testada (`"source.id in (6, 16, 20, ...)"`) retornou 0 eventos sem erro — a sintaxe exata não foi determinada. **Não use `filterExpression` para a agregação de pet da T3.1** até a sintaxe ser confirmada num experimento dedicado; use o filtro client-side por `sourceID` já validado em §5, que tem exatidão comprovada (erro 0,00%). |
| ✅ Custo em **pontos** por tipo de query | **~2,0 pontos por requisição**, uniforme entre tipos (1 página de `events(Casts, limit:5000)`, 1 página de `events(DamageDone, limit:10000)`, 1 `table(Summary)` e 1 página de `characterRankings` custaram exatamente 2,00–2,01 pontos cada, medido via `rateLimitData.pointsSpentThisHour` antes/depois). Isso é **bem mais barato** do que o pior caso assumido na T1.8 (que estimava "centenas de requisições podem esgotar a cota rapidamente") — com `limitPerHour=3600`, o orçamento real é ~1800 requisições/hora. **Não relaxe os mecanismos de fila/orçamento da T1.8 por causa disso**: eles continuam sendo boa prática para justiça entre usuários e para not martelar a API à toa, mas a urgência é menor do que o documento original presumia. |
| ❌ Cooldown base na API da **WCL** | **Não existe.** `GameData.ability(id)` (tipo `GameAbility`) só tem `{id, icon, name}` — sem cooldown. Confirma que a T2.5 depende mesmo da API da Blizzard (fonte 1) ou de tabela manual curada (fonte 2), como já previsto no documento; não há atalho pela própria WCL. |
| ⬜ Valores literais de `class`/`spec` para as 25 specs | **Ainda não verificado** — nenhum log de fixture cobre as 25 specs. Warlock/Demonology confirmado (`type: "Warlock"`, `specs: ["Demonology"]`, `specIDs: [266]` em `combatantInfo`). A T0.9 deve confirmar as demais 24 ao encontrar logs reais, ou aceitar o risco e normalizar por `_normalize()` como já previsto. |
| ✅ Como obter `partition` atual programaticamente | `worldData.zones { id name partitions { id name compactName default } }` — o campo booleano **`default`** marca a partition vigente. Confirmado para a zone 46 (VS/DR/MQD): partition `4` ("12.1") é `default: true` entre as 4 partitions listadas. Use esta query na T1.7 em vez de hardcode. |

---

## 12. Fatos verificados na T3.1 (features além de casts)

| Item | Valor |
|---|---|
| `table(dataType: Summary)`'s `deathEvents[].deathTime` | **Relativo ao início da luta** (0-based), ao contrário de `phaseTransitions[].startTime` (absoluto). Verificado: para o fixture de Zarad, valores caem dentro de `[0, duration_ms]` (ex.: `178884` para uma luta de `345146` ms). |
| Timestamp de revive/ressurreição | **Não existe em nenhuma tabela/evento.** `table(dataType: Deaths)` só tem o instante da morte e o dano/cura que levou a ela. Uma varredura de `events(dataType: All)` na janela pós-morte do fixture de Zarad não achou `type: "resurrect"` (o pull é um wipe). Ver D-29. |
| `table(dataType: DamageDone)`'s `entries[].activeTime` | Disponível direto, sem query extra — já vem junto com `total`, `itemLevel`, `pets`, `abilities` (§5). `active_time_pct = activeTime / (endTime - startTime)`. Verificado: `344303 / 345146 = 99,76%`, batendo com o valor documentado em §1. |
| `masterData.actors[].petOwner` | Confirmado como o mapeamento pet → dono (já citado em §3) — usado para filtrar `events(dataType: DamageDone)` por `sourceID ∈ {player_id} ∪ {pet_ids}`. |
| `events(dataType: Resources)`'s `resourceChangeType` | Seguem o `Enum.PowerType` padrão da Blizzard (constante pública, estável entre expansões — não é o mesmo tipo de dado instável que os cooldowns de D-28). Verificado ao vivo: os eventos de Zarad (Warlock/Demonology) usam `resourceChangeType: 7`, e `maxResourceAmount: 50` bate com 5 Fragmentos de Alma × 10 (WCL reporta fragmentos fracionados ×10) — confirma `7 = SoulShards`. |
| `events(...)`'s argumento `sourceID` | Aceito (confirmado por introspecção de `Report.events`), mas não usado para os eventos de dano (múltiplas fontes — jogador + pets — não cabem num único `sourceID`); usado nos eventos de recurso (uma fonte só, o próprio jogador) só como filtro client-side, igual ao padrão já usado para `Casts`. |

---

## 0. Tabela de veredito — cobertura da T0.1

> Ver `docs/desvios.md` D-2: esta seção satisfaz "todo campo da tabela da T0.1 tem veredito
> registrado" sem duplicar o conteúdo narrativo abaixo. A saída mecânica completa e re-executável
> está em `docs/schema_probe_output.md` (gerada por `python -m botgitgud.wcl.schema_probe`).

| Campo da tabela T0.1 | Veredito | Seção com o detalhe |
|---|---|---|
| `rateLimitData { limitPerHour, pointsSpentThisHour, pointsResetIn }` | ✅ existe (introspecção) | §2 |
| `reportData.report.fights {...}` | ✅ existe (introspecção) | §3 |
| `table(dataType: Summary)` → `playerDetails`, `combatantInfo` | ✅ existe (JSON escalar, verificado ao vivo) | §4 |
| `table(dataType: DamageDone)` | ✅ existe, **com armadilha** (JSON escalar, verificado ao vivo) | §5 |
| `table(dataType: Buffs / Debuffs)` | ✅ existe, mesmo formato para ambos | §6, §11 |
| `events(dataType: Casts / Resources)` | ✅ existe (introspecção: `ReportEventPaginator{data, nextPageTimestamp}`) | §10 |
| `characterRankings(className, specName, metric, page, difficulty, partition, bracket)` | ✅ todos os 7 argumentos existem (introspecção) | §8 |
| Campos de cada ranking | ⚠️ **parcial** — `percentile`, `talents`, `gear` **NÃO existem**; os demais existem | §8, §9 |
