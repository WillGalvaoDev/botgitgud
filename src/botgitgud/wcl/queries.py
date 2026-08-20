"""T1.6 — GraphQL query strings, centralized (docs/implementacao.md §1.1
lists this module explicitly). Previously scattered as module-level
constants inside ingest/log_fetcher.py and bot.py; consolidated here so
every caller (LogFetcher, ingest/rankings.py) shares one copy.
"""

from __future__ import annotations

QUERY_PLAYER_META = """
query GetPlayerMeta($code: String!, $fightIDs: [Int]!) {
  reportData {
    report(code: $code) {
      fights(fightIDs: $fightIDs) {
        id encounterID name startTime endTime kill difficulty
        phaseTransitions { id startTime }
      }
      masterData { actors { id name type subType petOwner } }
      table(fightIDs: $fightIDs, dataType: Summary, translate: true)
      castsTable: table(fightIDs: $fightIDs, dataType: Casts, translate: true)
      damageTable: table(fightIDs: $fightIDs, dataType: DamageDone, translate: true)
    }
  }
}
"""

QUERY_PLAYER_EVENTS = """
query GetPlayerEvents(
  $code: String!, $fightIDs: [Int]!, $startTime: Float!, $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: $fightIDs, dataType: Casts, startTime: $startTime,
        endTime: $endTime, limit: 5000, translate: true
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# T3.1 (docs/schema_confirmado.md §5): NOT filtered by sourceID — a
# player's real damage includes their pets (many distinct source IDs), so
# the whole fight's events are fetched once and filtered client-side by
# sourceID in {player_id} union {pet_ids} (ingest/damage_aggregation.py).
# Same 10000 page size the live measurement (7 pages for a 345s/20-pet
# fight) used.
QUERY_PLAYER_DAMAGE_EVENTS = """
query GetPlayerDamageEvents(
  $code: String!, $fightIDs: [Int]!, $startTime: Float!, $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: $fightIDs, dataType: DamageDone, startTime: $startTime,
        endTime: $endTime, limit: 10000, translate: true
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# T3.1: resourcechange events, filtered client-side by sourceID == player
# (mirrors QUERY_PLAYER_EVENTS' own pattern) — resource_waste is the
# player's own only, never pets'.
QUERY_PLAYER_RESOURCE_EVENTS = """
query GetPlayerResourceEvents(
  $code: String!, $fightIDs: [Int]!, $startTime: Float!, $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: $fightIDs, dataType: Resources, startTime: $startTime,
        endTime: $endTime, limit: 10000, translate: true
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

QUERY_PLAYER_PERCENTILE = """
query GetPercentile(
  $name: String!, $serverSlug: String!, $serverRegion: String!,
  $encounterID: Int!, $difficulty: Int!
) {
  characterData {
    character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
      encounterRankings(encounterID: $encounterID, metric: dps, difficulty: $difficulty)
    }
  }
}
"""

QUERY_RANKINGS_PAGE = """
query GetRankingsCDs(
  $encounterID: Int!, $className: String!, $specName: String!, $page: Int!, $partition: Int!
) {
  worldData {
    encounter(id: $encounterID) {
      characterRankings(
        className: $className, specName: $specName, metric: dps, page: $page, partition: $partition
      )
    }
  }
}
"""

# T1.7 (docs/schema_confirmado.md §11): the current partition must be passed
# explicitly to characterRankings (§1.5 — never mix partitions in a cohort),
# never hardcoded — it changes as new content patches ship.
QUERY_ZONE_PARTITIONS = """
query GetZonePartitions($encounterID: Int!) {
  worldData {
    encounter(id: $encounterID) {
      zone {
        id
        partitions { id default }
      }
    }
  }
}
"""

# T2.1 (docs/schema_confirmado.md §6): sourceID filters correctly on the
# Buffs table — this is the player's own aura list (self-buffs + anything
# applied to them by others), the source for has_augmentation and
# external_buffs detection.
QUERY_PLAYER_BUFFS = """
query GetPlayerBuffs($code: String!, $fightIDs: [Int]!, $sourceID: Int!) {
  reportData {
    report(code: $code) {
      table(fightIDs: $fightIDs, dataType: Buffs, sourceID: $sourceID, translate: true)
    }
  }
}
"""

# T3.1: same sourceID semantics as QUERY_PLAYER_BUFFS (docs/schema_confirmado.md
# §6) — the player's own debuff list (self-DoTs and anything applied to
# them), feeding uptimes[spell_id] alongside the Buffs table.
QUERY_PLAYER_DEBUFFS = """
query GetPlayerDebuffs($code: String!, $fightIDs: [Int]!, $sourceID: Int!) {
  reportData {
    report(code: $code) {
      table(fightIDs: $fightIDs, dataType: Debuffs, sourceID: $sourceID, translate: true)
    }
  }
}
"""

# T-DG.1 (docs/fase4-data-acquisition-plan.md §4.3, docs/schema_confirmado.md
# §13.3): bulk per-fight source of rankPercent + partition, discovered
# during the Fase 4 data-gate investigation. Costs 2.0 points regardless of
# player count and measured 100% rankPercent coverage (169/169 live), versus
# ~40% coverage and 1.0 point PER PLAYER for QUERY_PLAYER_PERCENTILE (the
# source LogFetcher used before T-DG.0/T-DG.1). `rankings` is a JSON scalar
# whose shape is documented in ingest/fight_rankings.py.
QUERY_REPORT_RANKINGS = """
query GetReportRankings($code: String!, $fightIDs: [Int]!) {
  reportData {
    report(code: $code) {
      rankings(fightIDs: $fightIDs)
    }
  }
}
"""

# T-DG.4 (docs/fase4-data-acquisition-plan.md §4.3, docs/schema_confirmado.md
# §13.3 update): `fightIDs` omitted entirely — measured live to return EVERY
# ranked fight of the report in one call (6/6 fights, same ~2.0 pts/fight as
# the single-fight form above), letting Estágio B triage a report without
# knowing any fight_id upfront and without filtering by spec/encounter —
# the plan's own requirement ("não fixe ainda uma spec/encontro").
QUERY_REPORT_RANKINGS_ALL_FIGHTS = """
query GetReportRankingsAllFights($code: String!) {
  reportData {
    report(code: $code) {
      rankings
    }
  }
}
"""

# T-DG.3 (docs/fase4-data-acquisition-plan.md §4.1, docs/schema_confirmado.md
# §13.2): Estágio A discovery — reportData.reports is the source that scales
# (no characterRankings/fightRankings leaderboard-size ceiling), but the WCL
# server itself rejects page > 25 ("The maximum allowed page is 25 until the
# performance of paginated queries can be improved"), so callers MUST window
# by startTime/endTime (ingest/discovery.py enforces this, never calls with
# page > MAX_DISCOVERY_PAGE). `has_more_pages` is snake_case in the response
# (verified live), unlike every other paginator in this file.
QUERY_DISCOVER_REPORTS = """
query DiscoverReports(
  $zoneID: Int!, $limit: Int!, $page: Int!, $startTime: Float!, $endTime: Float!
) {
  reportData {
    reports(zoneID: $zoneID, limit: $limit, page: $page, startTime: $startTime, endTime: $endTime) {
      has_more_pages
      data { code startTime endTime }
    }
  }
}
"""
