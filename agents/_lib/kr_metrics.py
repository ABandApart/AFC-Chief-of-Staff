"""KR metric registry and scoring (`PRD-notion-integration.md` N4; N-S2, N-S15).

Every Key Result in the Notion master OKR document names a Metric id. This module
declares each id's *kind* and *window*, computes its Current value from Postgres,
and scores it. The dashboard push and the Discord summary both read it, so the
two never disagree.

- A **cumulative** metric counts events inside its window and grows over time
  (quarter to date, or month to date for `llm_spend_month`).
- A **point** metric is a level or rate measured at `as_of`.

Rates are returned as percentages (0–100), matching how targets are written in
Notion (95, not 0.95). `compute` returns None for no_data: a median with no
rows, or a rate with an empty denominator. A count of 0 is data.

The kind lives here, not in Notion, so scoring never depends on a property the
operator could set wrong. `okr_schema.METRICS` must list exactly these ids
(`tests/test_kr_metrics.py` pins it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

CUMULATIVE = "cumulative"
POINT = "point"

HIGHER = "Higher is better"
LOWER = "Lower is better"

# Status keys (stored in okr_snapshots.status) and their dashboard labels.
NO_DATA, SET_TARGET, TOO_EARLY = "no_data", "set_target", "too_early"
ON_TRACK, AT_RISK, OFF_TRACK = "on_track", "at_risk", "off_track"
LABELS = {
    NO_DATA: "⚪ No data",
    SET_TARGET: "🔧 Set target",
    TOO_EARLY: "⏳ Too early",
    ON_TRACK: "🟢 On track",
    AT_RISK: "🟡 At risk",
    OFF_TRACK: "🔴 Off track",
}
# Worst first: the order the Discord summary uses to pick "the worst KR".
SEVERITY = (OFF_TRACK, AT_RISK, NO_DATA, SET_TARGET, TOO_EARLY, ON_TRACK)

AT_RISK_FLOOR = {POINT: 0.9, CUMULATIVE: 0.7}  # N-S15
TOO_EARLY_DAYS = 15  # a cumulative KR is Too early before day 15 of its window
RUN_SUCCESS_DAYS = 7
CONVERSION_DAYS = 90


@dataclass(frozen=True)
class Metric:
    id: str
    kind: str
    window: str  # quarter | month | rolling | now | manual — see `window_start`
    sql: str | None  # returns one row: (value, rows measured)


# Each query takes %(start)s, %(as_of)s (timestamptz), and %(as_of_date)s, and
# returns (value, rows measured). Counts and sums report 1 measured row, so 0 is
# a value; a median or rate reports its denominator, so an empty one is no_data.
_SQL = {
    "engagements_signed": """
        SELECT count(*), 1 FROM outcomes
        WHERE outcome_type = 'engagement_signed'
          AND recorded_at >= %(start)s AND recorded_at < %(as_of)s""",
    "median_engagement_value": """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY outcome_value), count(*)
        FROM outcomes
        WHERE outcome_type = 'engagement_signed' AND outcome_value IS NOT NULL
          AND recorded_at >= %(start)s AND recorded_at < %(as_of)s""",
    # Of the engagements signed in the last 90 days, the share whose client has
    # since converted to maintenance (client key from migration 0030, gap M5).
    "project_to_maintenance_rate": """
        SELECT 100.0 * count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM outcomes m
                   WHERE m.outcome_type = 'maintenance_converted'
                     AND m.client_id = e.client_id AND m.recorded_at < %(as_of)s))
               / nullif(count(*), 0),
               count(*)
        FROM outcomes e
        WHERE e.outcome_type = 'engagement_signed'
          AND e.recorded_at >= %(start)s AND e.recorded_at < %(as_of)s""",
    # Current status, so only meaningful at push time (not backfilled).
    "cold_sequences_live": """
        SELECT count(*), 1 FROM outreach_targets
        WHERE status IN ('in_sequence', 'conversation', 'call_booked')
          AND NOT is_reengagement""",
    # Touches whose window closed this quarter. A skip made before the window
    # closed is a decision, not a miss, so it leaves the denominator.
    "touches_on_schedule_rate": """
        SELECT 100.0 * count(*) FILTER (WHERE sent_at::date <= window_closes)
               / nullif(count(*), 0), count(*)
        FROM outreach_touches
        WHERE window_closes >= %(start)s::date AND window_closes < %(as_of_date)s
          AND NOT (skipped_at IS NOT NULL AND skipped_at::date <= window_closes)""",
    "sequences_completed": """
        SELECT count(*), 1 FROM outreach_targets
        WHERE sequence_completed_at >= %(start)s::date
          AND sequence_completed_at < %(as_of_date)s""",
    # Status transitions, not current status (gap M3): a target that moves on to
    # call_booked still counts as a conversation opened.
    "conversations_opened": """
        SELECT count(DISTINCT entity_id), 1 FROM outreach_events
        WHERE entity_table = 'outreach_targets' AND changed->'status'->>'to' = 'conversation'
          AND occurred_at >= %(start)s AND occurred_at < %(as_of)s""",
    # Outreach calls from status transitions; /outcome adds only calls with no
    # outreach target, so nothing is counted twice (gap M2).
    "calls_booked": """
        SELECT (SELECT count(DISTINCT entity_id) FROM outreach_events
                WHERE entity_table = 'outreach_targets'
                  AND changed->'status'->>'to' = 'call_booked'
                  AND occurred_at >= %(start)s AND occurred_at < %(as_of)s)
             + (SELECT count(*) FROM outcomes
                WHERE outcome_type = 'discovery_call_booked' AND attributed_target_id IS NULL
                  AND recorded_at >= %(start)s AND recorded_at < %(as_of)s),
               1""",
    "proposals_sent": """
        SELECT count(*), 1 FROM outcomes
        WHERE outcome_type = 'proposal_sent'
          AND recorded_at >= %(start)s AND recorded_at < %(as_of)s""",
    "followups_on_time_rate": """
        SELECT 100.0 * count(*) FILTER (WHERE completed_at::date <= deadline)
               / nullif(count(*), 0), count(*)
        FROM follow_ups
        WHERE deadline >= %(start)s::date AND deadline < %(as_of_date)s""",
    "agent_run_success_rate": """
        SELECT 100.0 * count(*) FILTER (WHERE status = 'success')
               / nullif(count(*), 0), count(*)
        FROM agent_runs
        WHERE status <> 'running'
          AND started_at >= %(start)s AND started_at < %(as_of)s""",
    "llm_spend_month": """
        SELECT coalesce(sum(usd_cost), 0), 1 FROM agent_runs
        WHERE started_at >= %(start)s AND started_at < %(as_of)s""",
}

_KINDS = {
    "engagements_signed": (CUMULATIVE, "quarter"),
    "median_engagement_value": (POINT, "quarter"),
    "project_to_maintenance_rate": (POINT, "rolling"),
    "cold_sequences_live": (POINT, "now"),
    "touches_on_schedule_rate": (POINT, "quarter"),
    "sequences_completed": (CUMULATIVE, "quarter"),
    "conversations_opened": (CUMULATIVE, "quarter"),
    "calls_booked": (CUMULATIVE, "quarter"),
    "proposals_sent": (CUMULATIVE, "quarter"),
    "followups_on_time_rate": (POINT, "quarter"),
    "agent_run_success_rate": (POINT, "rolling"),
    "llm_spend_month": (CUMULATIVE, "month"),
    "manual": (POINT, "manual"),
}

REGISTRY: dict[str, Metric] = {
    mid: Metric(mid, kind, window, _SQL.get(mid)) for mid, (kind, window) in _KINDS.items()
}

_ROLLING_DAYS = {"project_to_maintenance_rate": CONVERSION_DAYS,
                 "agent_run_success_rate": RUN_SUCCESS_DAYS}


# --- windows (pure) ---------------------------------------------------------------


def quarter_bounds(quarter: str) -> tuple[date, date]:
    """'2026-Q4' → (2026-10-01, 2027-01-01): first day, and first day after."""
    year, q = quarter.split("-Q")
    start = date(int(year), 3 * (int(q) - 1) + 1, 1)
    end = date(start.year + (start.month + 2) // 12, (start.month + 2) % 12 + 1, 1)
    return start, end


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


def window(metric_id: str, quarter: str, as_of: datetime) -> tuple[datetime, date, date]:
    """(query start, window first day, window end day) for `metric_id` at `as_of`.

    The window days drive elapsed share and the Too early rule; only cumulative
    metrics use them. A point metric's window first day is its query start.
    """
    m = REGISTRY[metric_id]
    tz = as_of.tzinfo
    if m.window == "month":
        first, end = month_bounds((as_of - timedelta(microseconds=1)).date())
    elif m.window == "rolling":
        first = (as_of - timedelta(days=_ROLLING_DAYS[metric_id])).date()
        end = as_of.date()
    else:
        first, end = quarter_bounds(quarter)
    start = (as_of - timedelta(days=_ROLLING_DAYS[metric_id])) if m.window == "rolling" \
        else datetime(first.year, first.month, first.day, tzinfo=tz)
    return start, first, end


def elapsed_share(first: date, end: date, as_of: datetime) -> float:
    """Share of [first, end) elapsed at `as_of`, by whole days, in (0, 1]."""
    total = (end - first).days
    done = (as_of.date() - first).days
    if as_of.time() != datetime.min.time():
        done += 1  # a partial day counts as elapsed
    return min(max(done, 1), total) / total


def day_of_window(first: date, as_of: datetime) -> int:
    """1-based day of the window that `as_of` falls in (the day just ended at midnight)."""
    return max((as_of - timedelta(microseconds=1)).date().toordinal() - first.toordinal() + 1, 1)


def clamp(metric_id: str, quarter: str, as_of: datetime) -> datetime:
    """A quarter-window metric stops at its quarter's end: the last push of a
    quarter, run after the quarter closed, must not count the next quarter's days."""
    if REGISTRY[metric_id].window != "quarter":
        return as_of
    _, end = quarter_bounds(quarter)
    end_at = datetime(end.year, end.month, end.day, tzinfo=as_of.tzinfo)
    return min(as_of, end_at)


# --- compute ------------------------------------------------------------------------


def compute(conn: Any, metric_id: str, *, quarter: str, as_of: datetime,
            manual_value: Decimal | None = None) -> Decimal | None:
    """Current value of `metric_id` at `as_of`, or None for no_data."""
    m = REGISTRY[metric_id]
    if m.sql is None:
        return manual_value
    as_of = clamp(metric_id, quarter, as_of)
    start, _, _ = window(metric_id, quarter, as_of)
    with conn.cursor() as cur:
        cur.execute(m.sql, {"start": start, "as_of": as_of, "as_of_date": as_of.date()})
        value, measured = cur.fetchone()
    if not measured or value is None:
        return None
    return Decimal(value).quantize(Decimal("0.01")) if not isinstance(value, int) \
        else Decimal(value)


# --- scoring (pure) -------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    status: str
    score: float | None
    progress: float | None  # 0..1 for the Notion bar; None leaves it blank

    @property
    def label(self) -> str:
        return LABELS[self.status]


def progress(direction: str, current: Decimal | None, target: Decimal | None) -> float | None:
    """Current ÷ Target, capped at 1, for Higher-is-better KRs only."""
    if direction != HIGHER or current is None or not target or target <= 0:
        return None
    return min(float(current) / float(target), 1.0)


def raw_score(kind: str, direction: str, current: Decimal, target: Decimal,
              share: float) -> float:
    cur, tgt = float(current), float(target)
    due = tgt * share if kind == CUMULATIVE else tgt
    if direction == LOWER:
        return float("inf") if cur <= 0 else due / cur
    return float("inf") if due <= 0 else cur / due


def score(metric_id: str, direction: str, current: Decimal | None,
          target: Decimal | None, *, share: float = 1.0, day: int = 99) -> Score:
    """Status for one KR (N-S15). `share` and `day` matter only for cumulative KRs."""
    kind = REGISTRY[metric_id].kind
    bar = progress(direction, current, target)
    if current is None:
        return Score(NO_DATA, None, None)
    if target is None:
        return Score(SET_TARGET, None, bar)
    s = raw_score(kind, direction, current, target, share)
    if s >= 1.0:
        status = ON_TRACK
    elif s >= AT_RISK_FLOOR[kind]:
        status = AT_RISK
    else:
        status = OFF_TRACK
    if status != ON_TRACK and kind == CUMULATIVE and direction == HIGHER \
            and day < TOO_EARLY_DAYS:
        status = TOO_EARLY
    return Score(status, s, bar)


@dataclass(frozen=True)
class Result:
    key_result_id: int
    name: str
    metric: str
    current: Decimal | None
    target: Decimal | None
    score: Score


def evaluate(conn: Any, kr: dict[str, Any], *, quarter: str, as_of: datetime) -> Result:
    """Compute and score one KR row (okr_key_results columns: id, name, metric,
    target, direction, manual_value)."""
    metric = kr["metric"]
    current = compute(conn, metric, quarter=quarter, as_of=as_of,
                      manual_value=kr.get("manual_value"))
    as_of = clamp(metric, quarter, as_of)
    _, first, end = window(metric, quarter, as_of)
    sc = score(metric, kr.get("direction") or HIGHER, current, kr.get("target"),
               share=elapsed_share(first, end, as_of), day=day_of_window(first, as_of))
    return Result(kr["id"], kr["name"], metric, current, kr.get("target"), sc)


def worst(results: list[Result]) -> Result | None:
    """The Off track KR with the lowest score, else the lowest At risk KR, else None."""
    for status in (OFF_TRACK, AT_RISK):
        hits = [r for r in results if r.score.status == status]
        if hits:
            return min(hits, key=lambda r: r.score.score if r.score.score is not None else 0)
    return None
