"""Unit tests for the O2 stale-signal re-check core (`agents/_lib/outreach_rescore`).

The load-bearing guarantees, all pure/DB-mocked:
  * a re-score **resets the 30-day clock** (`signals_observed_at = CURRENT_DATE`) —
    without it the sweep re-raises the same target forever;
  * it **claims the candidate first**, so a double-click cannot double-write;
  * S4/S5 are validated to 1/3/5;
  * the poll and re-attach queries filter posted vs unposted correctly.
"""

from __future__ import annotations

import pytest

from agents._lib import outreach_rescore as rc


def _conn(mocker, fetch_seq):
    cur = mocker.MagicMock()
    cur.fetchone.side_effect = list(fetch_seq)
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.transaction.return_value.__enter__.return_value = None
    return conn, cur


_SCORED_ROW = {"id": 5, "company_name": "AIIR", "score": 21, "treatment": "work"}


def test_apply_rescore_resets_the_clock_and_writes_s4_s5(mocker):
    conn, cur = _conn(mocker, [(5,), {"id": 5}, _SCORED_ROW])
    out = rc.apply_rescore(conn, candidate_id=9, target_id=5, s4=5, s5=3)
    assert out == _SCORED_ROW
    # The score write is the 2nd execute (claim is 1st, SELECT is 3rd).
    sql, params = cur.execute.call_args_list[1].args
    assert "signals_observed_at = CURRENT_DATE" in sql   # the O2 clock reset
    assert "s4_leadership_gap" in sql and "s5_team_build_below" in sql
    assert params["s4"] == 5 and params["s5"] == 3 and params["id"] == 5


def test_apply_rescore_claims_the_candidate_before_writing(mocker):
    conn, cur = _conn(mocker, [(5,), {"id": 5}, _SCORED_ROW])
    rc.apply_rescore(conn, candidate_id=9, target_id=5, s4=1, s5=1)
    # First statement claims the candidate: pending -> done, guarded.
    claim_sql = cur.execute.call_args_list[0].args[0]
    assert "UPDATE task_candidates SET status = 'done'" in claim_sql
    assert "WHERE id = %s AND status = 'pending'" in claim_sql


def test_apply_rescore_is_a_noop_when_already_decided(mocker):
    conn, cur = _conn(mocker, [None])   # the claim finds no pending row
    out = rc.apply_rescore(conn, candidate_id=9, target_id=5, s4=5, s5=5)
    assert out is None
    # Only the claim ran — no score write, so a double-click cannot re-score.
    assert cur.execute.call_count == 1


@pytest.mark.parametrize("s4,s5", [(2, 5), (5, 4), (0, 0), (5, 6)])
def test_apply_rescore_rejects_non_135(mocker, s4, s5):
    conn, _ = _conn(mocker, [])
    with pytest.raises(ValueError, match="1, 3, 5"):
        rc.apply_rescore(conn, candidate_id=9, target_id=5, s4=s4, s5=s5)


def test_undelivered_filters_to_pending_unposted(mocker):
    conn, cur = _conn(mocker, [])
    cur.fetchall.return_value = []
    rc.list_undelivered(conn)
    sql = cur.execute.call_args.args[0]
    assert "tc.status = 'pending'" in sql
    assert "tc.discord_message_id IS NULL" in sql      # not yet posted


def test_posted_undecided_filters_to_posted_pending(mocker):
    conn, cur = _conn(mocker, [])
    cur.fetchall.return_value = []
    rc.list_posted_undecided(conn)
    sql = cur.execute.call_args.args[0]
    assert "tc.status = 'pending'" in sql
    assert "tc.discord_message_id IS NOT NULL" in sql   # posted, for re-attach


def test_mark_posted_stores_the_message_id_as_text(mocker):
    conn, cur = _conn(mocker, [])
    rc.mark_posted(conn, candidate_id=9, message_id=12345)
    sql, params = cur.execute.call_args.args
    assert "discord_message_id" in sql
    assert params == ("12345", 9)
