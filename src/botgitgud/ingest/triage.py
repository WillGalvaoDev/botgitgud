"""T-DG.4 (docs/phase4.md) — Estágio B:
triagem via reportData.report.rankings, spec/encontro-agnóstica by
construction (the user-approved plan explicitly forbids fixing a target
before the census: "não fixe ainda uma spec/encontro"). report.rankings
with `fightIDs` omitted returns EVERY ranked fight of a report in one call
(docs/schema_confirmado.md §13.3 update, measured live: 6/6 fights, same
~2.0 pts/fight as the single-fight form T-DG.0 uses) — so a fight_id never
needs to be known in advance, and Stage B can triage a whole report's worth
of specs/encounters/partitions at once.

Unlike Estágio A (paginated, checkpointed per page), `triage_report`
triages a whole report in one call — the dedup unit is the report itself
(discovery_reports.triaged_at, ingest/discovery_store.py), not a page.
RateLimitBudgetExceeded is never swallowed here (unlike
ingest/fight_rankings.py's fetch_fight_rankings, a best-effort wrapper
meant for LogFetcher's per-log extraction path where a missing partition
is an acceptable degradation) — Stage B's caller needs the signal to stop
cleanly, exactly like ingest/discovery.py's Estágio A.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import structlog

from botgitgud.errors import ApiError, RateLimitBudgetExceeded
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.fight_rankings import parse_report_rankings_all
from botgitgud.wcl.client import WclClient
from botgitgud.wcl.queries import QUERY_REPORT_RANKINGS_ALL_FIGHTS

log = structlog.get_logger(__name__)

# docs/schema_confirmado.md §13.3: measured net cost per fight returned
# (12.0 pts / 6 fights). An ESTIMATE for reporting/max_points purposes only
# — mirrors ingest/log_fetcher.py's own _POINTS_PER_QUERY idiom ("estimates
# api_points_spent from a query count" rather than a per-call delta, since
# WclClient.points_remaining is a periodically-cached snapshot, T0.3). A
# report with zero ranked fights costs an unmeasured minimum > 0 that this
# estimate reports as 0 — a known, documented undercount, never a budget
# enforcement mechanism (WclClient's own floor check is authoritative).
_POINTS_PER_FIGHT = 2.0

TriageStopReason = Literal["completed", "budget_exceeded", "max_points_reached"]


def triage_report(client: WclClient, discovery_store: DiscoveryStore, *, report_code: str) -> int:
    """Idempotent at report granularity: a report already triaged makes
    zero network calls (§9.3: never refetch). Returns the number of fights
    newly written to discovery_fights/discovery_targets.
    """
    if discovery_store.has_triaged_report(report_code):
        return 0

    try:
        res_json = client.query(
            QUERY_REPORT_RANKINGS_ALL_FIGHTS, {"code": report_code}, op_name="triage_report"
        )
    except RateLimitBudgetExceeded:
        # Global condition, not a per-report defect — never swallowed,
        # mirrors ingest/discovery.py's own contract.
        raise
    except ApiError as e:
        log.warning("triage.report_failed", report_code=report_code, error=str(e))
        return 0

    fight_rankings = parse_report_rankings_all(res_json)
    for fr in fight_rankings:
        discovery_store.write_fight_rankings(fr, report_code=report_code)
    discovery_store.mark_report_triaged(report_code)
    return len(fight_rankings)


@dataclass(frozen=True, slots=True)
class TriageRunSummary:
    reports_total: int
    reports_triaged: int
    fights_written: int
    points_spent: float
    stopped_reason: TriageStopReason

    @property
    def reports_remaining(self) -> int:
        return self.reports_total - self.reports_triaged


def triage_pending_reports(
    client: WclClient,
    discovery_store: DiscoveryStore,
    *,
    zone_id: int | None = None,
    max_points: float | None = None,
) -> TriageRunSummary:
    """Orchestrates triage_report over every discovered-but-untriaged
    report (§9.3). Safe to call repeatedly: any report already triaged by
    a previous call is excluded from `list_untriaged_reports` up front, so
    a second call with nothing new to do costs zero network calls. Stops
    cleanly on RateLimitBudgetExceeded or once max_points is reached —
    checked BEFORE each report, never mid-report (a report is always
    finished once triage_report starts it), same contract as
    ingest/discovery.py's run_discovery.
    """
    report_codes = discovery_store.list_untriaged_reports(zone_id=zone_id)

    reports_triaged = 0
    fights_written = 0
    points_spent = 0.0
    stopped_reason: TriageStopReason = "completed"

    for code in report_codes:
        if max_points is not None and points_spent >= max_points:
            stopped_reason = "max_points_reached"
            break

        try:
            n_fights = triage_report(client, discovery_store, report_code=code)
        except RateLimitBudgetExceeded:
            stopped_reason = "budget_exceeded"
            break

        reports_triaged += 1
        fights_written += n_fights
        points_spent += n_fights * _POINTS_PER_FIGHT

    return TriageRunSummary(
        reports_total=len(report_codes),
        reports_triaged=reports_triaged,
        fights_written=fights_written,
        points_spent=points_spent,
        stopped_reason=stopped_reason,
    )
