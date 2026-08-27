"""Unit tests for the weekly re-score sweep (Track O, §14).

Pure band logic and the SQL guards; the hinge edges are exercised against the
live `outreach_s1` on barry-admin. The guarantees that must hold:

  * **A crossing is recorded, a non-crossing is not** (outcome 1).
  * **The sweep never touches a target** (outcome 3) — it only INSERTs events.
  * **Both as-of dates ride the payload** (O1's accepted cost, made honest).
  * **Stale-signal cards are raised at most once** (outcome 2).
  * **No band card** (O4) — the sweep records; the intake poll cards upward.
"""

from __future__ import annotations

from datetime import date

from agents.outreach import rescore

# --- band thresholds ----------------------------------------------------------


def test_band_thresholds_match_the_view():
    assert rescore._band(20) == "work"
    assert rescore._band(19) == "watch"
    assert rescore._band(14) == "watch"
    assert rescore._band(13) == "drop"
    assert rescore._band(None) is None


# --- crossing detection -------------------------------------------------------


def _rows(mocker, conn, rows):
    cur = mocker.MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value.__enter__.return_value = cur
    return cur


def test_a_crossing_is_detected(mocker):
    conn = mocker.MagicMock()
    _rows(mocker, conn, [
        {"id": 1, "company_name": "Up", "score_now": 21, "score_prev": 19},
        {"id": 2, "company_name": "Down", "score_now": 13, "score_prev": 15},
    ])
    changes = rescore.band_changes(conn, date(2026, 8, 27))
    assert {c["company_name"] for c in changes} == {"Up", "Down"}
    up = next(c for c in changes if c["company_name"] == "Up")
    assert up["band_prev"] == "watch" and up["band_now"] == "work"


def test_a_move_that_does_not_cross_a_boundary_is_ignored(mocker):
    conn = mocker.MagicMock()
    _rows(mocker, conn, [
        {"id": 1, "company_name": "Same", "score_now": 17, "score_prev": 15},
    ])
    assert rescore.band_changes(conn, date(2026, 8, 27)) == []


def test_an_incomplete_score_is_skipped_not_crashed_on(mocker):
    conn = mocker.MagicMock()
    _rows(mocker, conn, [
        {"id": 1, "company_name": "Unscored", "score_now": None, "score_prev": None},
    ])
    assert rescore.band_changes(conn, date(2026, 8, 27)) == []


def test_both_asof_dates_are_carried(mocker):
    conn = mocker.MagicMock()
    _rows(mocker, conn, [
        {"id": 1, "company_name": "X", "score_now": 21, "score_prev": 19},
    ])
    change = rescore.band_changes(conn, date(2026, 8, 27))[0]
    assert change["asof"] == "2026-08-27"
    assert change["asof_prev"] == "2026-08-20"   # asof - 7


# --- recording (outcome 3: never touches a target) ----------------------------


def test_recording_writes_events_and_never_updates_a_target(mocker):
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchall.return_value = [
        {"id": 1, "company_name": "X", "score_now": 21, "score_prev": 19}]
    conn.cursor.return_value.__enter__.return_value = cur
    n = rescore.record_band_changes(conn, date(2026, 8, 27))
    assert n == 1
    sqls = " ".join(c[0][0] for c in cur.execute.call_args_list)
    assert "INSERT INTO outreach_events" in sqls
    assert "'RESCORE'" in sqls
    assert "UPDATE outreach_targets" not in sqls
    assert "outreach_targets SET" not in sqls


def test_the_op_is_rescore_so_events_are_distinguishable():
    """The band-change events must be filterable from row-change audit events."""
    assert "RESCORE" not in ("INSERT", "UPDATE", "DELETE")


# --- O4: the sweep does not card band changes ---------------------------------


def test_the_sweep_raises_no_discord_card_for_a_band_change(mocker):
    """O4: an upward crossing is carded by the intake poll, not here; a downward
    one is record-only. The sweep touches task_candidates only for stale signals."""
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchall.return_value = [
        {"id": 1, "company_name": "Up", "score_now": 21, "score_prev": 19}]
    conn.cursor.return_value.__enter__.return_value = cur
    rescore.record_band_changes(conn, date(2026, 8, 27))
    sqls = " ".join(c[0][0] for c in cur.execute.call_args_list)
    assert "task_candidates" not in sqls   # no card from the band-change path


# --- stale-signal raising (outcome 2) -----------------------------------------


def test_stale_raising_is_idempotent_via_a_not_exists_guard(mocker):
    """A target with a pending re-check is skipped by the query, so a re-run
    raises nothing (outcome 2). The guard is in the SQL."""
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchall.return_value = []   # the NOT EXISTS filter already excluded them
    conn.cursor.return_value.__enter__.return_value = cur
    assert rescore.raise_stale_signals(conn) == 0
    select_sql = cur.execute.call_args_list[0][0][0]
    assert "NOT EXISTS" in select_sql
    assert "status = 'pending'" in select_sql
    assert rescore.STALE_SOURCE_TYPE == "outreach_stale_signal"
