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
    assert "LLM calls (24h): 7 — no failures" in s
    assert "Outcomes recorded: 2" in s


def test_briefing_no_failures_reads_clean():
    s = format_briefing(datetime(2026, 7, 6), STATUS)
    assert "no failures" in s
    assert "⚠️" not in s


def test_briefing_flags_failures():
    status = STATUS | {"failures_24h": 2}
    s = format_briefing(datetime(2026, 7, 6), status)
    assert "⚠️ 2 failure(s)" in s


def test_briefing_no_longer_carries_the_skeleton_disclaimer():
    # Phase 4 is live, so the "synthesis arrives with Tartt" placeholder is gone;
    # the status block still renders and the message ends cleanly (sections are
    # appended by main() with \n\n).
    s = format_briefing(datetime(2026, 7, 6), STATUS)
    assert "skeleton" not in s.lower()
    assert "real synthesis arrives" not in s
    assert "**System status**" in s and s.rstrip().endswith("✓")


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


# --- new prospects (Phase 6, Roy Kent) --------------------------------------

from agents.briefing.run import NEW_PROSPECTS_LIMIT, format_new_prospects  # noqa: E402


def test_format_new_prospects_empty_is_blank():
    assert format_new_prospects([]) == ""


def test_format_new_prospects_shows_fit_score_and_company():
    prospects = [{"name": "Jane", "company": "Acme", "icp_fit_score": 0.82, "status": "qualified"}]
    out = format_new_prospects(prospects)
    assert "Jane (Acme)" in out and "fit 0.82" in out


def test_format_new_prospects_unscored_lead_says_not_yet_qualified():
    prospects = [{"name": "Jane", "company": None, "icp_fit_score": None, "status": "new"}]
    out = format_new_prospects(prospects)
    assert "not yet qualified" in out


def test_format_new_prospects_caps_at_limit():
    prospects = [
        {"name": f"P{i}", "company": None, "icp_fit_score": 0.5, "status": "qualified"}
        for i in range(8)
    ]
    out = format_new_prospects(prospects)
    assert out.count("• ") == NEW_PROSPECTS_LIMIT


# --- daily spend section (PRD-outreach-autonomous-sourcing §6.8) -------------

from datetime import date  # noqa: E402

from agents.briefing.run import format_spend  # noqa: E402


def _spend(by_agent, sourcing=None):
    return {"by_agent": by_agent, "avg7": 11.2, "mtd": 112.4, "sourcing": sourcing}


def test_spend_splits_outreach_from_everything_else():
    text = format_spend(date(2026, 10, 5), _spend([
        ("outreach-sourcing", 13.40), ("granola", 0.62), ("trent-crimm", 0.31),
        ("outreach-discover", 0.24), ("recall", 0.15)]))
    assert "Spend, Mon 2026-10-05: $14.72 of $25.00" in text
    assert "Outreach $13.95 of $20.00: sourcing $13.40 · trent-crimm $0.31 · discover $0.24" in text
    assert "Everything else $0.77: granola $0.62 · recall $0.15" in text
    assert "7-day average $11.20 · month to date $112.40" in text
    assert "not Anthropic's invoice" in text
    assert "⚠️" not in text


def test_spend_reports_the_sourcing_run_and_its_unit_cost():
    run = {"target_n": 15, "passed": 15, "usd_cost": 13.35, "stop_reason": "target_met",
           "finished_at": datetime(2026, 10, 5, 6, 12)}
    text = format_spend(date(2026, 10, 5), _spend([("outreach-sourcing", 13.35)], run))
    assert "Outreach run: 15 candidates surfaced, $0.89 each" in text


def test_spend_flags_a_budget_stop():
    run = {"target_n": 15, "passed": 8, "usd_cost": 19.9, "stop_reason": "budget",
           "finished_at": datetime(2026, 10, 5, 6, 41)}
    text = format_spend(date(2026, 10, 5), _spend([("outreach-sourcing", 20.01)], run))
    assert "Outreach ceiling reached at 06:41; the run stopped with 8 of 15 surfaced." in text


def test_spend_flags_the_system_ceiling():
    text = format_spend(date(2026, 10, 5), _spend([("granola", 25.5)]))
    assert "System ceiling reached" in text


def test_spend_with_no_activity_still_renders():
    text = format_spend(date(2026, 10, 5), _spend([]))
    assert "$0.00 of $25.00" in text and "Outreach $0.00 of $20.00" in text
