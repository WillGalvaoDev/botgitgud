# Saída mecânica de `schema_probe.py`

> Gerado automaticamente. Reexecute com `python -m botgitgud.wcl.schema_probe` para atualizar.
> Este arquivo é sempre seguro de sobrescrever — a fonte de verdade curada é `docs/schema_confirmado.md` (ver `docs/desvios.md` D-2).

| Campo | Uso | Depende de | Veredito | Detalhe |
|---|---|---|---|---|
| `rateLimitData { limitPerHour, pointsSpentThisHour, pointsResetIn }` | orçamento de API | T0.3, T1.8 | ✅ | todos presentes (campos de RateLimitData) |
| `reportData.report.fights { id, encounterID, name, startTime, endTime, kill, difficulty, size, phaseTransitions }` | metadados da luta | T0.6, T2.4 | ✅ | todos presentes (campos de ReportFight) |
| `reportData.report.table(dataType: Summary) -> playerDetails, combatantInfo` | spec, ilvl, talentos | T2.1 | ✅ (verificado ao vivo) | table() retorna o escalar JSON (não introspectável). Confirmado ao vivo em schema_confirmado.md §4: playerDetails.{dps,healers,tanks}[].combatantInfo existe; combatantInfo.talents vem VAZIO, usar combatantInfo.talentTree. |
| `reportData.report.table(dataType: DamageDone)` | dano por habilidade | T3.2 | ✅ (verificado ao vivo) | Confirmado ao vivo em schema_confirmado.md §5: entry.total já inclui pets; entry.abilities vem TRUNCADO (5 de 29 no log de referência) — não usar para decomposição por habilidade, agregar eventos brutos com masterData.actors.petOwner. |
| `reportData.report.table(dataType: Buffs / Debuffs)` | uptimes | T3.1 | ✅ (verificado ao vivo) | Confirmado ao vivo em schema_confirmado.md §6/§11: mesmo formato para Buffs e Debuffs (auras[]: guid, name, type, abilityIcon, totalUptime, totalUses, bands). |
| `reportData.report.events(dataType: Casts / Resources) { data, nextPageTimestamp }` | timeline e recursos | T0.6, T3.1 | ✅ | todos presentes (campos de ReportEventPaginator) |
| `worldData.encounter.characterRankings(className, specName, metric, page, difficulty, partition, bracket)` | coorte | T2.1 | ✅ | todos presentes (argumentos de Encounter.characterRankings) |
| `ranking fields: name, duration, percentile, amount, report{code,fightID,startTime}, bracketData, talents, gear, server, guild, faction` | matching de coorte | T2.1 | ✅ (verificado ao vivo) | characterRankings retorna JSON escalar (não introspectável). Confirmado ao vivo em schema_confirmado.md §8: presentes {amount, bracketData, class, duration, faction, guild, hardModeLevel, name, report, server, spec, startTime}. AUSENTES: 'percentile' e 'talents'/'gear' NÃO existem — legacy/bot.py:361 fabrica 99.0 sempre; o parse real vem de characterData.character.encounterRankings.ranks[].rankPercent (schema_confirmado.md §9). |
