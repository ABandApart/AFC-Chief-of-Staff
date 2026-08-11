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


def test_task_from_candidate_maps_fields_and_keeps_provenance():
    candidate = {
        "id": 42,
        "proposed_action": "Share or write about: Git-knife",
        "evidence_text": "why it matters",
        "confidence": 0.69,
    }
    task = task_tinder.task_from_candidate(candidate, owner="barry")
    assert task["title"] == "Share or write about: Git-knife"
    assert task["description"] == "why it matters"
    assert task["owner"] == "barry"
    assert task["source_candidate_id"] == 42
    assert task["status"] == "open"
