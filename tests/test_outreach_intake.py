"""Unit tests for the Gate 1 intake decision core (Track O).

Pure rules tested directly; the DB path with `db.connection` mocked. The
guarantees that must hold:

  * **Capacity is enforced at intake** — `35-` is explicit that capacity is the
    constraint every other element exists to enforce, and cold vs re-engagement
    are metered separately so a departure trigger is never blocked by cold
    targets mid-arc (E1).
  * **Sequencing refuses without the two-tab diagnostic**, naming what is
    missing rather than letting a CHECK constraint fire.
  * **Idempotency is the database's** — a double-click transitions once.
  * **Drop preserves evidence history**, which cannot be rebuilt.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents._lib import outreach_intake as intake

TODAY = date(2026, 8, 14)

CAPACITY = {
    "cold_live": 3, "cold_ceiling": 15,
    "reengagement_live": 0, "reengagement_ceiling": 3,
}

TARGET = {
    "id": 7, "company_name": "Cadence Health", "status": "candidate",
    "stage": "series_a", "function_state": "vacant_seat",
    "trigger_kind": "request_open_past_45_days", "days_since_trigger": 56,
    "is_reengagement": False, "score": 23,
}


# --- the pure state machine ---------------------------------------------------


def test_candidate_transitions():
    assert intake.next_status("candidate", "work") == "in_sequence"
    assert intake.next_status("candidate", "watchlist") == "watchlist"
    assert intake.next_status("candidate", "drop") == "dropped"


def test_an_already_decided_target_is_a_noop():
    for decided in ("in_sequence", "watchlist", "dropped", "engaged"):
        assert intake.next_status(decided, "work") is None


def test_unknown_action_raises():
    with pytest.raises(ValueError):
        intake.next_status("candidate", "maybe")


def test_only_work_materialises_the_arc():
    assert "work" in intake.SEQUENCING_ACTIONS
    assert "watchlist" not in intake.SEQUENCING_ACTIONS
    assert "drop" not in intake.SEQUENCING_ACTIONS


# --- capacity (§8) ------------------------------------------------------------


def test_room_available_does_not_block():
    assert intake.capacity_blocks(CAPACITY, is_reengagement=False) is None


def test_cold_ceiling_blocks_at_the_cap_not_past_it():
    full = {**CAPACITY, "cold_live": 15}
    assert intake.capacity_blocks(full, is_reengagement=False) is not None
    almost = {**CAPACITY, "cold_live": 14}
    assert intake.capacity_blocks(almost, is_reengagement=False) is None


def test_reengagement_is_metered_separately_from_cold():
    # E1: the allowance of 3 runs ABOVE the cold cap, so a detected departure —
    # the highest-converting trigger in the method — is never blocked by cold
    # targets mid-arc.
    cold_full = {**CAPACITY, "cold_live": 15, "reengagement_live": 0}
    assert intake.capacity_blocks(cold_full, is_reengagement=True) is None
    assert intake.capacity_blocks(cold_full, is_reengagement=False) is not None


def test_reengagement_ceiling_blocks_reengagements_only():
    re_full = {**CAPACITY, "reengagement_live": 3}
    assert intake.capacity_blocks(re_full, is_reengagement=True) is not None
    assert intake.capacity_blocks(re_full, is_reengagement=False) is None


def test_capacity_message_says_what_to_do():
    msg = intake.capacity_blocks({**CAPACITY, "cold_live": 15}, is_reengagement=False)
    assert "15/15" in msg and "drain" in msg


# --- readiness to sequence ----------------------------------------------------


def test_a_complete_target_is_ready_to_work():
    assert intake.work_blocks(TARGET) is None


def test_missing_function_state_refuses_and_names_the_diagnostic():
    # Mirrors outreach_targets_seq_ck so the operator is told which judgement is
    # missing rather than reading a constraint name off a stack trace.
    blocked = intake.work_blocks({**TARGET, "function_state": None})
    assert blocked and "two-tab diagnostic" in blocked
    assert "vacant_seat" in blocked          # names the options


def test_missing_stage_refuses():
    blocked = intake.work_blocks({**TARGET, "stage": None})
    assert blocked and "stage" in blocked


# --- decide() -----------------------------------------------------------------


def _patch(mocker, target=None, capacity=None, updated=True):
    """Mock the DB so `decide` runs its logic without a database."""
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    mocker.patch.object(intake.db, "connection", return_value=cm)

    cur = mocker.MagicMock()
    cur.fetchone.side_effect = None
    cur.fetchone.return_value = target if target is not None else TARGET
    conn.cursor.return_value.__enter__.return_value = cur
    conn.transaction.return_value.__enter__.return_value = None
    conn.transaction.return_value.__exit__.return_value = False

    mocker.patch.object(intake, "read_capacity", return_value=capacity or CAPACITY)
    mocker.patch.object(intake, "_open_role_age", return_value=56)
    materialize = mocker.patch.object(
        intake.packet, "materialize_sequence",
        return_value=[{"id": i, "slot": i, "skip_reason": None} for i in range(1, 6)],
    )
    return cur, materialize


def test_work_admits_and_materialises_five_touches(mocker):
    cur, materialize = _patch(mocker)
    result = intake.decide(7, "work", today=TODAY)
    assert result["status"] == "in_sequence"
    assert len(result["touches"]) == 5
    materialize.assert_called_once()


def test_work_refuses_when_capacity_is_full_and_changes_nothing(mocker):
    cur, materialize = _patch(mocker, capacity={**CAPACITY, "cold_live": 15})
    with pytest.raises(intake.CapacityFullError):
        intake.decide(7, "work", today=TODAY)
    materialize.assert_not_called()
    # No UPDATE was issued — the refusal happens before the transition.
    assert not any("UPDATE" in str(c) for c in cur.execute.call_args_list)


def test_work_refuses_without_the_diagnostic_and_changes_nothing(mocker):
    cur, materialize = _patch(mocker, target={**TARGET, "function_state": None})
    with pytest.raises(intake.NotReadyToWorkError):
        intake.decide(7, "work", today=TODAY)
    materialize.assert_not_called()


def test_an_already_decided_target_returns_none(mocker):
    _patch(mocker, target={**TARGET, "status": "in_sequence"})
    assert intake.decide(7, "work", today=TODAY) is None


def test_watchlist_satisfies_both_schema_constraints(mocker):
    # outreach_targets_stalled_ck AND _watch_ck both require a value; a
    # watchlist write missing either would be rejected by the database.
    cur, _ = _patch(mocker)
    intake.decide(7, "watchlist", today=TODAY)
    sql, params = next(
        c.args for c in cur.execute.call_args_list if "watchlist" in str(c.args[0])
    )
    assert "stalled_reason" in sql and "watch_until" in sql
    assert params[0] == intake.INTAKE_WATCHLIST_REASON
    assert params[1] > TODAY                 # 18 months out


def test_drop_sets_status_and_never_deletes(mocker):
    # 37- D1 says "delete the row", but outreach_evidence is ON DELETE CASCADE —
    # deleting destroys accumulated first_seen_at history, the one datum that
    # cannot be rebuilt.
    cur, _ = _patch(mocker)
    intake.decide(7, "drop", today=TODAY)
    statements = " ".join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "status = 'dropped'" in statements
    assert "DELETE" not in statements.upper()


def test_every_transition_is_guarded_on_candidate(mocker):
    # Idempotency is the database's: a second click updates zero rows.
    for action in ("work", "watchlist", "drop"):
        cur, _ = _patch(mocker)
        intake.decide(7, action, today=TODAY)
        updates = [str(c.args[0]) for c in cur.execute.call_args_list if "UPDATE" in str(c.args[0])]
        assert updates and all("status = 'candidate'" in u for u in updates), action


def test_watchlist_and_drop_never_materialise_touches(mocker):
    for action in ("watchlist", "drop"):
        _, materialize = _patch(mocker)
        intake.decide(7, action, today=TODAY)
        materialize.assert_not_called()


def test_unknown_action_raises_before_touching_the_db(mocker):
    mocker.patch.object(intake.db, "connection", side_effect=AssertionError("must not connect"))
    with pytest.raises(ValueError):
        intake.decide(7, "frobnicate")
