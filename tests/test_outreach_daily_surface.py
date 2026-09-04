"""Unit tests for the #outreach daily-surface core (Track O).

Pure rules tested directly; the DB writes with a mocked cursor. The guarantees
that must hold:

  * **Defer respects the window** — a snooze never crosses `window_closes`
    (`outreach_touches_snooze_ck`), so a touch whose window closes today cannot
    be deferred (R2: no Skip either — it drains).
  * **Defer requires a note** (R3) — a blank note is refused before any write.
  * **Contact is advisory and idempotent** — it stamps `marked_working_at` and
    is a no-op on an already-resolved touch.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from agents._lib import outreach_daily_surface as ds

TODAY = date(2026, 9, 3)


# --- snooze_date (pure) -------------------------------------------------------


def test_snooze_is_tomorrow_when_the_window_allows():
    assert ds.snooze_date(TODAY, date(2026, 9, 10)) == date(2026, 9, 4)


def test_snooze_is_allowed_up_to_the_window_close():
    # window closes tomorrow — today+1 lands exactly on window_closes, which the
    # CHECK permits (<=).
    assert ds.snooze_date(TODAY, date(2026, 9, 4)) == date(2026, 9, 4)


def test_snooze_refused_when_the_window_closes_today_or_earlier():
    assert ds.snooze_date(TODAY, TODAY) is None
    assert ds.snooze_date(TODAY, date(2026, 9, 2)) is None


# --- format_driving_facts (pure) ----------------------------------------------


def test_no_live_evidence_reads_plainly():
    assert ds.format_driving_facts([]) == "_no live evidence_"


def test_closed_facts_are_not_shown_as_live():
    closed = [{"payload": {"title": "VP req"}, "closed_at": date(2026, 8, 1),
               "freshness": "closed", "age_days": 40}]
    assert ds.format_driving_facts(closed) == "_no live evidence_"


def test_fresh_fact_line_carries_title_and_age():
    ev = [{"payload": {"title": "VP Revenue req"}, "closed_at": None,
           "freshness": "fresh", "age_days": 56}]
    assert ds.format_driving_facts(ev) == "• VP Revenue req — open 56d"


def test_ageing_and_stale_are_marked():
    ev = [
        {"payload": {"title": "A"}, "closed_at": None, "freshness": "ageing", "age_days": 10},
        {"payload": {"title": "B"}, "closed_at": None, "freshness": "stale", "age_days": 20},
    ]
    out = ds.format_driving_facts(ev)
    assert "⚠️ ageing" in out and "⛔ stale" in out


def test_facts_are_capped_and_the_remainder_counted():
    ev = [
        {"payload": {"title": f"F{i}"}, "closed_at": None, "freshness": "fresh", "age_days": i}
        for i in range(4)
    ]
    out = ds.format_driving_facts(ev, limit=3)
    assert out.count("•") == 4                # 3 facts + the "…1 more" line
    assert "…1 more" in out


# --- gmail_link / bcc_address (pure) ------------------------------------------


def test_gmail_link_targets_the_thread_when_present():
    link = ds.gmail_link({"gmail_thread_id": "abc123"})
    assert link and link.endswith("#all/abc123")


def test_gmail_link_is_none_without_a_thread():
    assert ds.gmail_link({"gmail_thread_id": None}) is None
    assert ds.gmail_link({}) is None


def test_bcc_address_is_plus_addressed_on_the_dedicated_mailbox():
    assert ds.bcc_address({"bcc_token": "tok9"}) == "bcc+tok9@aiadaptive.co"


# --- DB writes (mocked cursor) ------------------------------------------------


def _conn(mocker, cur):
    """A mock connection whose `.cursor(...)` yields `cur` for any args."""
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = cur
    cm.__exit__.return_value = False
    conn.cursor.return_value = cm
    return conn


def test_defer_refuses_a_blank_note_before_touching_the_db(mocker):
    cur = mocker.MagicMock()
    conn = _conn(mocker, cur)
    with pytest.raises(ValueError):
        ds.defer(conn, 5, "   ", today=TODAY)
    cur.execute.assert_not_called()


def test_defer_refuses_when_the_window_closes_today(mocker):
    cur = mocker.MagicMock()
    cur.fetchone.return_value = {"window_closes": TODAY, "sent_at": None, "skipped_at": None}
    conn = _conn(mocker, cur)
    with pytest.raises(ds.DeferWindowClosedError):
        ds.defer(conn, 5, "waiting on their board meeting", today=TODAY)


def test_defer_sets_snooze_and_returns_the_new_date(mocker):
    cur = mocker.MagicMock()
    cur.fetchone.side_effect = [
        {"window_closes": date(2026, 9, 30), "sent_at": None, "skipped_at": None},
        (date(2026, 9, 4),),
    ]
    conn = _conn(mocker, cur)
    assert ds.defer(conn, 5, "circle back next week", today=TODAY) == date(2026, 9, 4)


def test_defer_is_a_noop_on_an_already_sent_touch(mocker):
    cur = mocker.MagicMock()
    cur.fetchone.return_value = {
        "window_closes": date(2026, 9, 30),
        "sent_at": datetime(2026, 9, 2, 9, 0), "skipped_at": None,
    }
    conn = _conn(mocker, cur)
    assert ds.defer(conn, 5, "note", today=TODAY) is None


def test_mark_working_returns_the_stamp(mocker):
    stamp = datetime(2026, 9, 3, 6, 30)
    cur = mocker.MagicMock()
    cur.fetchone.return_value = (stamp,)
    conn = _conn(mocker, cur)
    assert ds.mark_working(conn, 5) == stamp


def test_mark_working_is_a_noop_when_the_touch_is_resolved(mocker):
    cur = mocker.MagicMock()
    cur.fetchone.return_value = None
    conn = _conn(mocker, cur)
    assert ds.mark_working(conn, 5) is None


# --- card_inputs (mocked packet read) -----------------------------------------


def test_card_inputs_bundles_touch_target_evidence_and_packet(mocker):
    touch = {"id": 5, "slot": 1, "bcc_token": "t"}
    target = {"id": 2, "company_name": "Acme"}
    evidence = [{"payload": {"title": "req"}}]
    mocker.patch.object(
        ds.packet, "fetch_packet_inputs", return_value=(touch, target, evidence)
    )
    mocker.patch.object(ds, "latest_packet", return_value={"subject_line": "Hi"})
    out = ds.card_inputs(mocker.MagicMock(), 5)
    assert out["touch"] is touch and out["target"] is target
    assert out["evidence"] is evidence and out["packet"]["subject_line"] == "Hi"


def test_card_inputs_is_none_for_a_missing_touch(mocker):
    mocker.patch.object(ds.packet, "fetch_packet_inputs", side_effect=KeyError("no touch"))
    assert ds.card_inputs(mocker.MagicMock(), 999) is None
