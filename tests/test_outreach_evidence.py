"""Unit tests for the evidence poller (Track O, `agents/outreach/evidence.py`).

The orchestration, with the board fetch and the DB mocked. The load-bearing
behaviour is the **failure posture**: a target whose board could not be read is
left entirely untouched. If a failed fetch ever fell through to close-detection
it would mark every open req closed, and the packet arithmetic would then
describe live reqs as closed in a founder's inbox — the mirror image of R19.
"""

from __future__ import annotations

from datetime import date

from agents.outreach import adapters, evidence

TODAY = date(2026, 8, 12)
TARGET = {"id": 7, "company_name": "Cadence Health",
          "company_domain": "cadence.health",
          "careers_url": "https://jobs.lever.co/cadence"}

ROLE = {"external_id": "1", "title": "VP Revenue", "url": "https://x/1",
        "location": "Remote", "team": "Sales"}


def _patch(mocker, *, result, new=True, closed=0):
    mocker.patch.object(adapters, "fetch_open_roles", return_value=result)
    up = mocker.patch.object(evidence.outreach, "upsert_evidence", return_value=new)
    cl = mocker.patch.object(evidence.outreach, "close_absent_evidence", return_value=closed)
    return up, cl


def test_poll_target_upserts_each_role_then_closes_the_absent(mocker):
    up, cl = _patch(
        mocker,
        result=adapters.BoardResult(ok=True, roles=[ROLE], provider="lever"),
        closed=2,
    )
    summary = evidence.poll_target(mocker.MagicMock(), TARGET, today=TODAY)

    assert summary == {"status": "polled", "roles": 1, "new": 1, "closed": 2}
    up.assert_called_once()
    # Close-detection is scoped to this target, this fact kind, and the keys
    # actually seen in this poll.
    kwargs = cl.call_args.kwargs
    assert kwargs["target_id"] == 7
    assert kwargs["fact_kind"] == "open_role"
    assert kwargs["seen_keys"] == ["lever:1"]
    assert kwargs["today"] == TODAY


def test_failed_fetch_writes_nothing_and_closes_nothing(mocker):
    # THE guard. "We could not look" must never be recorded as "there is nothing
    # there" — that would close every open req at once.
    up, cl = _patch(
        mocker,
        result=adapters.BoardResult(ok=False, reason="fetch failed: connection reset"),
    )
    summary = evidence.poll_target(mocker.MagicMock(), TARGET, today=TODAY)

    assert summary["status"] == "skipped"
    up.assert_not_called()
    cl.assert_not_called()


def test_unsupported_board_is_skipped_and_warned(mocker, caplog):
    # An unsupported careers URL accrues no posting age; that has to be loud now,
    # not discovered weeks later as an empty packet.
    _patch(mocker, result=adapters.BoardResult(ok=False, reason="unsupported careers URL"))
    with caplog.at_level("WARNING"):
        summary = evidence.poll_target(mocker.MagicMock(), TARGET, today=TODAY)
    assert summary["status"] == "skipped"
    assert "unsupported" in caplog.text


def test_genuinely_empty_board_still_closes_absent_evidence(mocker):
    # A board that parsed fine and has zero roles is a real zero — the company
    # closed its reqs — so close-detection must run.
    up, cl = _patch(
        mocker, result=adapters.BoardResult(ok=True, roles=[], provider="lever"), closed=3
    )
    summary = evidence.poll_target(mocker.MagicMock(), TARGET, today=TODAY)
    assert summary == {"status": "polled", "roles": 0, "new": 0, "closed": 3}
    up.assert_not_called()
    cl.assert_called_once()
    assert cl.call_args.kwargs["seen_keys"] == []


def test_confirming_poll_counts_no_new_facts(mocker):
    _patch(mocker, result=adapters.BoardResult(ok=True, roles=[ROLE], provider="lever"), new=False)
    summary = evidence.poll_target(mocker.MagicMock(), TARGET, today=TODAY)
    assert summary["new"] == 0 and summary["roles"] == 1


# --- poll() sweep -------------------------------------------------------------


def _patch_db(mocker, targets):
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    mocker.patch.object(evidence.db, "connection", return_value=cm)
    mocker.patch.object(evidence.outreach, "pollable_targets", return_value=targets)
    return conn


def test_poll_totals_across_targets(mocker):
    _patch_db(mocker, [TARGET, {**TARGET, "id": 8}])
    mocker.patch.object(
        evidence, "poll_target",
        return_value={"status": "polled", "roles": 2, "new": 1, "closed": 1},
    )
    totals = evidence.poll(today=TODAY)
    assert totals == {"targets": 2, "polled": 2, "skipped": 0, "new": 2, "closed": 2}


def test_one_failing_target_does_not_stop_the_sweep(mocker):
    # A per-target exception costs that target its polling window, not the cycle's.
    _patch_db(mocker, [TARGET, {**TARGET, "id": 8}])
    mocker.patch.object(
        evidence, "poll_target",
        side_effect=[RuntimeError("boom"), {"status": "polled", "roles": 1, "new": 1, "closed": 0}],
    )
    totals = evidence.poll(today=TODAY)
    assert totals["polled"] == 1 and totals["skipped"] == 1 and totals["new"] == 1


def test_poll_with_no_targets_is_a_clean_noop(mocker):
    _patch_db(mocker, [])
    assert evidence.poll(today=TODAY)["targets"] == 0
