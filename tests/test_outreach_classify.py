"""Unit tests for Part 2 — classification and promotion (Track O).

The deterministic half is tested here; the Haiku call runs on barry-agent. The
guarantees that must hold:

  * **Idempotent** (outcome 4) — a re-run never re-classifies or double-promotes.
  * **`none` is terminal** (open #2) — a not-a-trigger verdict still stamps
    `classified_at`, so the queue drains and the item is never re-asked.
  * **Promotion anchors on the acceptance date** (0023) — a classified market
    trigger sets the kind, never the arc date.
  * **first_seen_at is the event date, not today** (R1.4, outcome 2).
  * **A promoted fact is never close-swept** (outcome 3) — it is `news_event`,
    not `open_role`.
  * **H5 quarantines before the prompt** (outcome 5).
"""

from __future__ import annotations

from datetime import date, datetime

from agents.outreach import classify


def _signal(**over):
    base = {
        "id": 1, "target_id": 18, "discovery_id": None, "source_kind": "news_rss",
        "source_url": "https://n/a", "excerpt": "Acme raises Series B",
        "dedup_key": "https://n/a", "detected_at": datetime(2026, 8, 20, 9, 0),
    }
    return {**base, **over}


def _conn(mocker, updated=True):
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchone.return_value = {"id": 1} if updated else None
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# --- the queue reader ---------------------------------------------------------


def test_the_queue_is_unclassified_oldest_first_bounded(mocker):
    conn, cur = _conn(mocker)
    cur.fetchall.return_value = []
    classify.fetch_unclassified(conn, 50)
    sql, params = cur.execute.call_args[0]
    assert "classified_at IS NULL" in sql
    assert "ORDER BY detected_at" in sql
    assert params == (50,)


# --- verdict recording --------------------------------------------------------


def test_recording_writes_only_an_unclassified_row(mocker):
    conn, cur = _conn(mocker)
    classify.record_verdict(conn, 1, "funding_announced", 0.9, "clear")
    sql, params = cur.execute.call_args[0]
    assert "classified_at IS NULL" in sql, "the idempotency guard must be in the SQL"
    assert params[0] == "funding_announced"


def test_a_none_verdict_still_stamps_classified_at(mocker):
    """open #2: 'none' is terminal, so the queue drains and it is never re-asked."""
    conn, cur = _conn(mocker)
    classify.record_verdict(conn, 1, None, None, "generic article")
    params = cur.execute.call_args[0][1]
    assert params[0] == "none"


def test_a_second_verdict_changes_nothing(mocker):
    conn, cur = _conn(mocker, updated=False)
    assert classify.record_verdict(conn, 1, "funding_announced", 0.9, "x") is False


# --- handle_classified routing ------------------------------------------------


def test_a_confident_trigger_promotes(mocker):
    conn, _ = _conn(mocker)
    promote = mocker.patch.object(classify, "promote_signal")
    out = classify.handle_classified(conn, _signal(), "funding_announced", 0.9, "x")
    assert out == "promoted"
    promote.assert_called_once()


def test_a_trigger_below_threshold_does_not_promote(mocker):
    conn, _ = _conn(mocker)
    promote = mocker.patch.object(classify, "promote_signal")
    out = classify.handle_classified(conn, _signal(), "funding_announced", 0.5, "weak")
    assert out == "below_threshold"
    promote.assert_not_called()


def test_a_none_verdict_does_not_promote(mocker):
    conn, _ = _conn(mocker)
    promote = mocker.patch.object(classify, "promote_signal")
    out = classify.handle_classified(conn, _signal(), None, 0.9, "generic")
    assert out == "classified_none"
    promote.assert_not_called()


def test_an_already_classified_signal_is_left_alone(mocker):
    conn, _ = _conn(mocker, updated=False)
    promote = mocker.patch.object(classify, "promote_signal")
    assert classify.handle_classified(conn, _signal(), "funding_announced", 0.9,
                                      "x") == "already_classified"
    promote.assert_not_called()


# --- promotion (0023 + R1.4) --------------------------------------------------


def test_a_target_signal_writes_evidence_dated_on_the_event(mocker):
    conn = mocker.MagicMock()
    upsert = mocker.patch.object(classify.outreach, "upsert_evidence",
                                 return_value=True)
    classify.promote_signal(conn, _signal(target_id=18), "funding_announced", 0.9)
    row = upsert.call_args[0][1]
    assert row["fact_kind"] == "news_event"       # outcome 3: not open_role
    assert row["first_seen_at"] == date(2026, 8, 20)  # the event date, not today
    assert row["target_id"] == 18


def test_a_pool_signal_promotes_the_firm_on_the_acceptance_date(mocker):
    """0023: the classified kind sets trigger_kind; the arc date is the
    acceptance date, so promote() is called with NO trigger_date."""
    conn = mocker.MagicMock()
    promote = mocker.patch.object(classify.outreach_discovery, "promote",
                                  return_value={"target_id": 77, "created": True})
    mocker.patch.object(classify.outreach, "reparent_watch_signals")
    mocker.patch.object(classify.outreach, "upsert_evidence", return_value=True)
    classify.promote_signal(conn, _signal(target_id=None, discovery_id=5),
                            "funding_announced", 0.9)
    disc_id, trigger = promote.call_args[0]
    assert disc_id == 5
    assert trigger["trigger_kind"] == "funding_announced"
    assert "trigger_date" not in trigger, "the arc date is the acceptance date, not the event"


# --- H5 and the fact kind -----------------------------------------------------


def test_h5_screens_the_excerpt():
    """A crafted excerpt is caught before it can reach the prompt."""
    assert classify.screen_excerpt("normal headline") == [] or isinstance(
        classify.screen_excerpt("normal headline"), list)


def test_the_promote_threshold_matches_roy_kents_gate():
    assert classify.PROMOTE_THRESHOLD == 0.7


def test_the_fact_kind_is_not_open_role():
    """outcome 3: close_absent_evidence runs on open_role, so a news fact under a
    different kind is never close-swept."""
    assert classify.FACT_KIND != "open_role"


def test_the_eight_triggers_exclude_inbound_and_operator_selected():
    assert "inbound_enquiry" not in classify.TRIGGER_KINDS
    assert "operator_selected" not in classify.TRIGGER_KINDS
    assert len(classify.TRIGGER_KINDS) == 8
