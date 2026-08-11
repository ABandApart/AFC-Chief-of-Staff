"""Task Tinder decision core (Phase 5) — the pending-candidate state machine.

Discovery (Tartt) and, later, capture/meeting agents write **pending**
`task_candidates`. The Task Tinder cog surfaces them in `#task-tinder` with
accept / decline / defer buttons; an **accept promotes** the candidate into a
`tasks` row. This is the round-trip Phase 4 exists to trial: does the
`task_candidates` shape hold up with a real producer, and does accept→task work.

Discord-free by design (like `_lib/approvals`): the pure status transitions and
the candidate→task field mapping live here and are unit-tested without a bot or
DB; the cog owns the Discord surface + the guarded writes.

**CPX-4 decision (operator, 2026-08-11).** Keep the two tables; an accept creates
a **`follow_up` (the chase-able commitment) and a `tasks` row linked to it**
(`tasks.follow_up_id`) — the build-order "done when" for Phase 5. The eval's
CPX-4 collapse is deferred; `followup_from_candidate` + `task_from_candidate` are
the one place it would change.
"""

from __future__ import annotations

from typing import Any

# action → the status it produces. A candidate must be `pending` to transition.
_ACTION_STATUS = {
    "accept": "accepted",
    "decline": "declined",
    "defer": "deferred",
}

# Only an accept promotes the candidate into a task; decline/defer do not.
PROMOTE_STATUSES = frozenset({"accepted"})


def next_status(current: str, action: str) -> str | None:
    """The new status for `action` if the candidate is still `pending`.

    Returns None if the candidate is already decided (the idempotent double-click
    no-op — the DB write enforces the same via `WHERE status='pending'`). Raises
    on an unknown action.
    """
    if current != "pending":
        return None
    if action not in _ACTION_STATUS:
        raise ValueError(f"unknown task-tinder action: {action!r}")
    return _ACTION_STATUS[action]


def followup_from_candidate(candidate: dict[str, Any], *, owner: str) -> dict[str, Any]:
    """Map an accepted `task_candidate` → the `follow_up` row to insert (pure).

    The follow_up is the chase-able commitment (fresh escalation, no source
    meeting for a discovery candidate). The task links to it.
    """
    return {
        "owner": owner,
        "action": candidate["proposed_action"],
        "status": "open",
        "escalation_level": 0,
    }


def task_from_candidate(
    candidate: dict[str, Any], *, owner: str, follow_up_id: int
) -> dict[str, Any]:
    """Map an accepted `task_candidate` → the `tasks` row to insert (pure).

    Linked to its `follow_up` (`follow_up_id`); `source_candidate_id` keeps the
    provenance link back to the candidate.
    """
    return {
        "title": candidate["proposed_action"],
        "description": candidate.get("evidence_text"),
        "owner": owner,
        "source_candidate_id": candidate["id"],
        "status": "open",
        "follow_up_id": follow_up_id,
    }
