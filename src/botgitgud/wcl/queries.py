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
      table(fightIDs: $fightIDs, dataType: Summary, translate: true)
      castsTable: table(fightIDs: $fightIDs, dataType: Casts, translate: true)
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
