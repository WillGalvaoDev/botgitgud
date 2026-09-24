"""T-DG.3 (docs/phase4.md) — Estágio A
of the Data Acquisition Gate: windowed, resumable, idempotent discovery of
reports via reportData.reports.

Windowing is not an optimization here, it is required correctness: the WCL
server rejects page > MAX_DISCOVERY_PAGE (docs/schema_confirmado.md §13.2,
measured live — "The maximum allowed page is 25 until the performance of
paginated queries can be improved"), so any range wide enough to exceed
2,500 reports (MAX_DISCOVERY_PAGE * DISCOVERY_PAGE_LIMIT) silently loses
data past page 25 unless split into narrower time windows first.

`discover_reports_in_window` is the atomic, checkpointed unit: one call
either finishes a window, hits `exhausted_cap` (and queues two half-span
sub-windows — the same problem this module exists to solve, recursively),
or stops early on RateLimitBudgetExceeded with the checkpoint left exactly
where it was, page-for-page (§9.2: "matar o processo a qualquer momento
perde no máximo uma página"). `run_discovery` is the orchestration loop
over a whole [start_ms, end_ms) range, safe to call repeatedly — a second
call does zero network work for any window already `done`/`exhausted_cap`
(§9.3: never refetch).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from botgitgud.errors import ApiError, RateLimitBudgetExceeded
from botgitgud.ingest.discovery_store import CheckpointState, DiscoveryStore
from botgitgud.wcl.client import WclClient
from botgitgud.wcl.queries import QUERY_DISCOVER_REPORTS

log = structlog.get_logger(__name__)

# docs/schema_confirmado.md §13.2: measured live — page 25 succeeds, page 26
# is rejected by the server. limit=100 is the largest page size measured
# working (§13.2's density probes).
MAX_DISCOVERY_PAGE = 25
DISCOVERY_PAGE_LIMIT = 100
# docs/phase4.md: 12h primary window (1,988 reports/day
# measured for zone 46, comfortably under the 2,500 = 25*100 cap with margin),
# split to 6h on cap exhaustion.
DEFAULT_WINDOW_SPAN_MS = 12 * 3600 * 1000
# docs/schema_confirmado.md §13.1: net cost of one `reports(...)` page, measured.
_POINTS_PER_PAGE = 1.0


def default_job_key(zone_id: int) -> str:
    return f"discover:zone={zone_id}"


@dataclass(slots=True)
class DiscoveryResult:
    """Mutable on purpose: filled in place as discover_reports_in_window
    pages through, so that function stays linear to read.
    """

    job_key: str
    window_start: int
    window_end: int
    state: CheckpointState = "in_progress"
    last_page: int = 0
    n_reports_written: int = 0
    points_spent: float = 0.0


def discover_reports_in_window(
    client: WclClient,
    discovery_store: DiscoveryStore,
    *,
    job_key: str,
    zone_id: int,
    window_start: int,
    window_end: int,
) -> DiscoveryResult:
    """Resumes from the checkpoint's last completed page, or page 1 if none
    exists. A window already `done`/`exhausted_cap` returns immediately —
    zero network calls (§9.3).
    """
    result = DiscoveryResult(job_key=job_key, window_start=window_start, window_end=window_end)
    checkpoint = discovery_store.read_checkpoint(job_key, window_start, window_end)
    if checkpoint is not None and checkpoint.state in ("done", "exhausted_cap"):
        result.state = checkpoint.state
        result.last_page = checkpoint.last_page
        return result

    page = (checkpoint.last_page + 1) if checkpoint is not None else 1
    points_spent = checkpoint.points_spent if checkpoint is not None else 0.0

    while page <= MAX_DISCOVERY_PAGE:
        try:
            res_json = client.query(
                QUERY_DISCOVER_REPORTS,
                {
                    "zoneID": zone_id,
                    "limit": DISCOVERY_PAGE_LIMIT,
                    "page": page,
                    "startTime": float(window_start),
                    "endTime": float(window_end),
                },
                op_name="discover_reports",
            )
        except RateLimitBudgetExceeded:
            # Global condition, not a per-page defect (mirrors
            # ingest/rankings.py's own contract) — checkpoint left exactly
            # at the last COMPLETED page, never advanced past it.
            discovery_store.write_checkpoint(
                job_key=job_key,
                zone_id=zone_id,
                window_start=window_start,
                window_end=window_end,
                last_page=page - 1,
                state="in_progress",
                points_spent=points_spent,
            )
            raise
        except ApiError as e:
            log.warning("discovery.page_failed", page=page, error=str(e))
            break

        points_spent += _POINTS_PER_PAGE
        reports_data = res_json.get("data", {}).get("reportData", {}).get("reports", {})
        for row in reports_data.get("data") or []:
            code, start_ms, end_ms = row.get("code"), row.get("startTime"), row.get("endTime")
            if not code or start_ms is None or end_ms is None:
                continue
            if not discovery_store.has_report(code):
                discovery_store.write_report(
                    report_code=code,
                    zone_id=zone_id,
                    start_time_ms=int(start_ms),
                    end_time_ms=int(end_ms),
                )
                result.n_reports_written += 1

        has_more = bool(reports_data.get("has_more_pages"))
        result.last_page = page

        if not has_more:
            result.state = "done"
            break

        if page == MAX_DISCOVERY_PAGE:
            result.state = "exhausted_cap"
            _enqueue_subdivided_windows(
                discovery_store,
                job_key=job_key,
                zone_id=zone_id,
                window_start=window_start,
                window_end=window_end,
            )
            break

        page += 1

    result.points_spent = points_spent
    discovery_store.write_checkpoint(
        job_key=job_key,
        zone_id=zone_id,
        window_start=window_start,
        window_end=window_end,
        last_page=result.last_page,
        state=result.state,
        points_spent=points_spent,
    )
    return result


def _enqueue_subdivided_windows(
    discovery_store: DiscoveryStore,
    *,
    job_key: str,
    zone_id: int,
    window_start: int,
    window_end: int,
) -> None:
    mid = window_start + (window_end - window_start) // 2
    if mid <= window_start or mid >= window_end:
        # Degenerate (sub-millisecond) window — cannot subdivide further.
        # Leaves the parent as exhausted_cap with no children; the loss is
        # visible in dataset-status rather than silently retried forever.
        log.warning(
            "discovery.window_too_small_to_subdivide",
            window_start=window_start,
            window_end=window_end,
        )
        return
    for sub_start, sub_end in ((window_start, mid), (mid, window_end)):
        if discovery_store.read_checkpoint(job_key, sub_start, sub_end) is None:
            discovery_store.write_checkpoint(
                job_key=job_key,
                zone_id=zone_id,
                window_start=sub_start,
                window_end=sub_end,
                last_page=0,
                state="pending",
                points_spent=0.0,
            )


def _plan_windows(start_ms: int, end_ms: int, window_span_ms: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    cursor = start_ms
    while cursor < end_ms:
        nxt = min(cursor + window_span_ms, end_ms)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


@dataclass(frozen=True, slots=True)
class DiscoveryRunSummary:
    job_key: str
    windows_total: int
    windows_done: int
    reports_written: int
    points_spent: float
    stopped_reason: str  # "completed" | "budget_exceeded" | "max_points_reached"

    @property
    def windows_remaining(self) -> int:
        return self.windows_total - self.windows_done


def run_discovery(
    client: WclClient,
    discovery_store: DiscoveryStore,
    *,
    zone_id: int,
    start_ms: int,
    end_ms: int,
    window_span_ms: int = DEFAULT_WINDOW_SPAN_MS,
    job_key: str | None = None,
    max_points: float | None = None,
) -> DiscoveryRunSummary:
    """Safe to call repeatedly with the same [start_ms, end_ms) range —
    every already-`done`/`exhausted_cap` window costs zero network calls
    (§9.3). Also picks up any `pending` sub-window a previous run queued
    via `_enqueue_subdivided_windows`, even though those aren't on the
    regular window_span_ms grid.
    """
    key = job_key or default_job_key(zone_id)
    planned = _plan_windows(start_ms, end_ms, window_span_ms)
    pending_extra = {
        (c.window_start, c.window_end)
        for c in discovery_store.list_checkpoints(key)
        if c.state in ("pending", "in_progress")
    }
    worklist = sorted(set(planned) | pending_extra)

    windows_done = 0
    reports_written = 0
    points_spent = 0.0
    stopped_reason = "completed"

    for window_start, window_end in worklist:
        checkpoint = discovery_store.read_checkpoint(key, window_start, window_end)
        if checkpoint is not None and checkpoint.state in ("done", "exhausted_cap"):
            windows_done += 1
            continue

        if max_points is not None and points_spent >= max_points:
            stopped_reason = "max_points_reached"
            break

        try:
            result = discover_reports_in_window(
                client,
                discovery_store,
                job_key=key,
                zone_id=zone_id,
                window_start=window_start,
                window_end=window_end,
            )
        except RateLimitBudgetExceeded:
            stopped_reason = "budget_exceeded"
            break

        reports_written += result.n_reports_written
        points_spent += result.points_spent
        if result.state in ("done", "exhausted_cap"):
            windows_done += 1

    return DiscoveryRunSummary(
        job_key=key,
        windows_total=len(worklist),
        windows_done=windows_done,
        reports_written=reports_written,
        points_spent=points_spent,
        stopped_reason=stopped_reason,
    )
