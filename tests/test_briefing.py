"""Unit tests for the briefing skeleton's pure formatter.

`gather_status` (DB) and `post_to_discord` (network) are exercised by the
Phase 3.5 runtime validation; `format_briefing` is pure and tested here.
"""

from __future__ import annotations

from datetime import datetime

from agents.briefing.run import format_briefing

STATUS = {
    "facts_total": 12,
    "facts_24h": 3,
    "spend_24h": 0.00001234,
    "calls_24h": 7,
    "failures_24h": 0,
    "outcomes_total": 2,
}


def test_briefing_includes_date_and_counts():
    now = datetime(2026, 7, 6, 6, 0)
    s = format_briefing(now, STATUS)
    assert "Monday 06 July 2026" in s
    assert "12 total, 3 captured in the last 24h" in s
    assert "7 for $0.000012" in s
    assert "Outcomes recorded: 2" in s


def test_briefing_no_failures_reads_clean():
    s = format_briefing(datetime(2026, 7, 6), STATUS)
    assert "no failures" in s
    assert "⚠️" not in s


def test_briefing_flags_failures():
    status = STATUS | {"failures_24h": 2}
    s = format_briefing(datetime(2026, 7, 6), status)
    assert "⚠️ 2 failure(s)" in s


def test_briefing_marks_itself_as_skeleton():
    s = format_briefing(datetime(2026, 7, 6), STATUS)
    assert "Phase 4" in s
