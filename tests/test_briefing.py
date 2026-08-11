"""Unit tests for the briefing skeleton's pure formatter.

`gather_status` (DB) and `post_to_discord` (network) are exercised by the
Phase 3.5 runtime validation; `format_briefing` is pure and tested here.
"""

from __future__ import annotations

from datetime import datetime

from agents.briefing.run import format_briefing

STATUS = {
    "notes_total": 12,
    "notes_24h": 3,
    "spend_24h": 0.00001234,
    "calls_24h": 7,
    "failures_24h": 0,
    "outcomes_total": 2,
}


def test_briefing_includes_date_and_counts():
    now = datetime(2026, 7, 6, 6, 0)
    s = format_briefing(now, STATUS)
    assert "Monday 06 July 2026" in s
    assert "Notes captured: 12 total, 3 in the last 24h" in s
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


# --- reading recs (Phase 4, Task 5) -----------------------------------------

from agents.briefing.run import READING_RECS_LIMIT, format_reading_recs  # noqa: E402


def test_format_reading_recs_empty_is_blank():
    assert format_reading_recs([]) == ""


def test_format_reading_recs_lists_title_url_score():
    recs = [{"url": "https://ex.com/a", "title": "A Thing", "interest_score": 0.82}]
    out = format_reading_recs(recs)
    assert "A Thing" in out and "https://ex.com/a" in out and "0.82" in out


def test_format_reading_recs_caps_at_limit():
    recs = [
        {"url": f"https://ex.com/{i}", "title": f"T{i}", "interest_score": 0.9 - i * 0.1}
        for i in range(6)
    ]
    out = format_reading_recs(recs)
    assert out.count("• ") == READING_RECS_LIMIT
