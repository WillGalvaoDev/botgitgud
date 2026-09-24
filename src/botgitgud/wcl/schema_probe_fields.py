"""T1.6 split of schema_probe.py's static FIELD_TABLE data out of the probe
logic itself, to keep schema_probe.py under the 300-line file limit
(T1.6). Pure data, no behavior change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldCheck:
    """One row of the T0.1 confirmation table."""

    path: str
    purpose: str
    depends_on: str
    # For introspectable object types: (type_name, [expected_field_or_arg_names]).
    # None when the field returns the JSON scalar and must be verified live instead.
    introspect: tuple[str, list[str]] | None = None
    # For JSON-scalar fields already verified by hand (docs/schema_confirmado.md).
    live_verified_note: str | None = None


FIELD_TABLE: list[FieldCheck] = [
    FieldCheck(
        path="rateLimitData { limitPerHour, pointsSpentThisHour, pointsResetIn }",
        purpose="orçamento de API",
        depends_on="T0.3, T1.8",
        introspect=("RateLimitData", ["limitPerHour", "pointsSpentThisHour", "pointsResetIn"]),
    ),
    FieldCheck(
        path=(
            "reportData.report.fights { id, encounterID, name, startTime, endTime, "
            "kill, difficulty, size, phaseTransitions }"
        ),
        purpose="metadados da luta",
        depends_on="T0.6, T2.4",
        introspect=(
            "ReportFight",
            [
                "id",
                "encounterID",
                "name",
                "startTime",
                "endTime",
                "kill",
                "difficulty",
                "size",
                "phaseTransitions",
            ],
        ),
    ),
    FieldCheck(
        path="reportData.report.table(dataType: Summary) -> playerDetails, combatantInfo",
        purpose="spec, ilvl, talentos",
        depends_on="T2.1",
        live_verified_note=(
            "table() retorna o escalar JSON (não introspectável). Confirmado ao vivo em "
            "schema_confirmado.md §4: playerDetails.{dps,healers,tanks}[].combatantInfo existe; "
            "combatantInfo.talents vem VAZIO, usar combatantInfo.talentTree."
        ),
    ),
    FieldCheck(
        path="reportData.report.table(dataType: DamageDone)",
        purpose="dano por habilidade",
        depends_on="T3.2",
        live_verified_note=(
            "Confirmado ao vivo em schema_confirmado.md §5: entry.total já inclui pets; "
            "entry.abilities vem TRUNCADO (5 de 29 no log de referência) — não usar para "
            "decomposição por habilidade, agregar eventos brutos com masterData.actors.petOwner."
        ),
    ),
    FieldCheck(
        path="reportData.report.table(dataType: Buffs / Debuffs)",
        purpose="uptimes",
        depends_on="T3.1",
        live_verified_note=(
            "Confirmado ao vivo em schema_confirmado.md §6/§11: mesmo formato para Buffs e "
            "Debuffs (auras[]: guid, name, type, abilityIcon, totalUptime, totalUses, bands)."
        ),
    ),
    FieldCheck(
        path="reportData.report.events(dataType: Casts / Resources) { data, nextPageTimestamp }",
        purpose="timeline e recursos",
        depends_on="T0.6, T3.1",
        introspect=("ReportEventPaginator", ["data", "nextPageTimestamp"]),
    ),
    FieldCheck(
        path=(
            "worldData.encounter.characterRankings(className, specName, metric, page, "
            "difficulty, partition, bracket)"
        ),
        purpose="coorte",
        depends_on="T2.1",
        introspect=(
            "__arg__:Encounter.characterRankings",
            ["className", "specName", "metric", "page", "difficulty", "partition", "bracket"],
        ),
    ),
    FieldCheck(
        path=(
            "ranking fields: name, duration, percentile, amount, report{code,fightID,startTime}, "
            "bracketData, talents, gear, server, guild, faction"
        ),
        purpose="matching de coorte",
        depends_on="T2.1",
        live_verified_note=(
            "characterRankings retorna JSON escalar (não introspectável). Confirmado ao vivo em "
            "schema_confirmado.md §8: presentes {amount, bracketData, class, duration, faction, "
            "guild, hardModeLevel, name, report, server, spec, startTime}. AUSENTES: "
            "'percentile' e 'talents'/'gear' NÃO existem; "
            "o parse real vem de characterData.character.encounterRankings.ranks[].rankPercent "
            "(schema_confirmado.md §9)."
        ),
    ),
]
