"""KR metric SQL against fixture rows (PRD-notion-integration.md N4, verification 1).

Needs a Postgres connection: set KR_METRICS_TEST_DSN (for example
`dbname=postgres`). Each test builds the columns the metrics read as temporary
tables, so no real table is read or written. Skipped when unset.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agents._lib import kr_metrics as km

DSN = os.environ.get("KR_METRICS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="KR_METRICS_TEST_DSN not set")

TZ = timezone(timedelta(hours=-4))
AS_OF = datetime(2026, 11, 2, tzinfo=TZ)  # a Monday: the week Oct 26–Nov 1 just ended
Q = "2026-Q4"

_TABLES = """
CREATE TEMP TABLE outcomes (outcome_type text, outcome_value numeric, recorded_at timestamptz,
                            client_id bigint, attributed_target_id bigint);
CREATE TEMP TABLE outreach_targets (status text, is_reengagement boolean,
                                    sequence_completed_at date);
CREATE TEMP TABLE outreach_events (entity_table text, entity_id bigint, changed jsonb,
                                   occurred_at timestamptz);
CREATE TEMP TABLE outreach_touches (window_closes date, sent_at timestamptz,
                                    skipped_at timestamptz);
CREATE TEMP TABLE follow_ups (deadline date, completed_at timestamptz);
CREATE TEMP TABLE agent_runs (status text, started_at timestamptz, usd_cost numeric);
"""


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET search_path = pg_temp")
        c.execute(_TABLES)
        yield c


def _rows(conn, table, cols, rows):
    marks = ", ".join(["%s"] * len(cols))
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", rows)


def _v(conn, metric):
    return km.compute(conn, metric, quarter=Q, as_of=AS_OF)


def d(y, m, day, h=12):
    return datetime(y, m, day, h, tzinfo=TZ)


def test_outcome_counts_respect_the_quarter(conn):
    _rows(conn, "outcomes", ["outcome_type", "outcome_value", "recorded_at", "client_id"], [
        ("engagement_signed", 10000, d(2026, 10, 5), 1),
        ("engagement_signed", 30000, d(2026, 10, 20), 2),
        ("engagement_signed", 20000, d(2026, 9, 30), 3),   # last quarter
        ("engagement_signed", 50000, d(2026, 11, 3), 4),   # after as_of
        ("proposal_sent", None, d(2026, 10, 12), None),
    ])
    assert _v(conn, "engagements_signed") == 2
    assert _v(conn, "median_engagement_value") == Decimal("20000.00")
    assert _v(conn, "proposals_sent") == 1


def test_median_and_conversion_are_no_data_without_engagements(conn):
    assert _v(conn, "median_engagement_value") is None
    assert _v(conn, "project_to_maintenance_rate") is None
    assert _v(conn, "engagements_signed") == 0


def test_project_to_maintenance_rate_links_by_client(conn):
    _rows(conn, "outcomes", ["outcome_type", "recorded_at", "client_id"], [
        ("engagement_signed", d(2026, 9, 1), 1),
        ("engagement_signed", d(2026, 9, 15), 2),
        ("maintenance_converted", d(2026, 10, 20), 1),
        ("maintenance_converted", d(2026, 11, 5), 2),       # after as_of
        ("engagement_signed", d(2026, 6, 1), 3),            # outside 90 days
    ])
    assert _v(conn, "project_to_maintenance_rate") == Decimal("50.00")


def test_pipeline_metrics(conn):
    _rows(conn, "outreach_targets", ["status", "is_reengagement", "sequence_completed_at"], [
        ("in_sequence", False, None), ("conversation", False, None),
        ("call_booked", True, None),            # re-engagement: not cold
        ("completed", False, "2026-10-10"), ("completed", False, "2026-09-10"),
    ])
    trans = [("outreach_targets", 1, '{"status": {"to": "conversation"}}', d(2026, 10, 3)),
             ("outreach_targets", 1, '{"status": {"to": "call_booked"}}', d(2026, 10, 9)),
             ("outreach_targets", 2, '{"status": {"to": "conversation"}}', d(2026, 10, 4)),
             ("outreach_targets", 2, '{"status": {"to": "conversation"}}', d(2026, 10, 6)),
             ("outreach_touches", 3, '{"status": {"to": "conversation"}}', d(2026, 10, 6))]
    _rows(conn, "outreach_events", ["entity_table", "entity_id", "changed", "occurred_at"], trans)
    _rows(conn, "outcomes", ["outcome_type", "recorded_at", "attributed_target_id"], [
        ("discovery_call_booked", d(2026, 10, 9), 1),   # same call as the transition
        ("discovery_call_booked", d(2026, 10, 11), None),  # a non-outreach call
    ])
    assert _v(conn, "cold_sequences_live") == 2
    assert _v(conn, "sequences_completed") == 1
    # Target 1 moved on to call_booked and still counts (gap M3); target 2 once.
    assert _v(conn, "conversations_opened") == 2
    assert _v(conn, "calls_booked") == 2  # no double count (gap M2)


def test_touches_on_schedule_rate(conn):
    _rows(conn, "outreach_touches", ["window_closes", "sent_at", "skipped_at"], [
        ("2026-10-10", d(2026, 10, 9), None),     # on time
        ("2026-10-12", d(2026, 10, 14), None),    # late
        ("2026-10-15", None, d(2026, 10, 14)),   # skipped in time: excluded
        ("2026-10-20", None, None),               # missed
        ("2026-11-10", None, None),               # window still open
    ])
    assert _v(conn, "touches_on_schedule_rate") == Decimal("33.33")


def test_followups_on_time_rate(conn):
    _rows(conn, "follow_ups", ["deadline", "completed_at"], [
        ("2026-10-10", d(2026, 10, 10, 23)), ("2026-10-20", None), ("2026-11-20", None),
    ])
    assert _v(conn, "followups_on_time_rate") == Decimal("50.00")


def test_agent_runs_rate_and_spend(conn):
    _rows(conn, "agent_runs", ["status", "started_at", "usd_cost"], [
        ("success", d(2026, 10, 27), Decimal("1.50")),
        ("success", d(2026, 10, 28), Decimal("2.00")),
        ("failed", d(2026, 10, 29), Decimal("0.50")),
        ("running", d(2026, 11, 1), None),                 # unfinished: excluded
        ("failed", d(2026, 10, 20), Decimal("10")),        # before the 7 days
        ("success", d(2026, 11, 2, 1), Decimal("99")),     # after as_of
    ])
    assert _v(conn, "agent_run_success_rate") == Decimal("66.67")
    # as_of is 00:00 on Nov 2, so the month is November: Nov 1 only, no spend.
    assert _v(conn, "llm_spend_month") == 0
    oct_end = km.compute(conn, "llm_spend_month", quarter=Q, as_of=datetime(2026, 11, 1, tzinfo=TZ))
    assert oct_end == Decimal("14.00")
