"""Unit tests for the Task Tinder decision core (Phase 5).

The pure state machine (accept/decline/defer, idempotent once decided) and the
candidate→task field mapping — the parts that must be correct regardless of the
Discord surface or DB.
"""

from __future__ import annotations

import pytest

from agents._lib import task_tinder


def test_pending_transitions():
    assert task_tinder.next_status("pending", "accept") == "accepted"
    assert task_tinder.next_status("pending", "decline") == "declined"
    assert task_tinder.next_status("pending", "defer") == "deferred"


def test_already_decided_is_noop():
    for decided in ("accepted", "declined", "deferred"):
        assert task_tinder.next_status(decided, "accept") is None


def test_unknown_action_raises_when_pending():
    with pytest.raises(ValueError):
        task_tinder.next_status("pending", "frobnicate")


def test_only_accept_promotes():
    assert "accepted" in task_tinder.PROMOTE_STATUSES
    assert "declined" not in task_tinder.PROMOTE_STATUSES
    assert "deferred" not in task_tinder.PROMOTE_STATUSES


CANDIDATE = {
    "id": 42,
    "proposed_action": "Share or write about: Git-knife",
    "evidence_text": "why it matters",
    "confidence": 0.69,
}


def test_followup_from_candidate_is_a_fresh_open_commitment():
    fu = task_tinder.followup_from_candidate(CANDIDATE, owner="barry")
    assert fu["owner"] == "barry"
    assert fu["action"] == "Share or write about: Git-knife"
    assert fu["status"] == "open"
    assert fu["escalation_level"] == 0


def test_task_from_candidate_maps_fields_links_followup_and_keeps_provenance():
    task = task_tinder.task_from_candidate(CANDIDATE, owner="barry", follow_up_id=7)
    assert task["title"] == "Share or write about: Git-knife"
    assert task["description"] == "why it matters"
    assert task["owner"] == "barry"
    assert task["source_candidate_id"] == 42
    assert task["status"] == "open"
    assert task["follow_up_id"] == 7
