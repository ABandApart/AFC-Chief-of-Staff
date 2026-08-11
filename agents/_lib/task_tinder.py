"""Task Tinder decision core (Phase 5) — the pending-candidate state machine.

Discovery (Tartt) and, later, capture/meeting agents write **pending**
`task_candidates`. The Task Tinder cog surfaces them in `#task-tinder` with
accept / decline / defer buttons; an **accept promotes** the candidate into a
`tasks` row. This is the round-trip Phase 4 exists to trial: does the
`task_candidates` shape hold up with a real producer, and does accept→task work.

Discord-free by design (like `_lib/approvals`): the pure status transitions and
the candidate→task field mapping live here and are unit-tested without a bot or
DB; the cog owns the Discord surface + the guarded writes.

**CPX-4 note.** The `tasks`/`follow_ups` split (eval CPX-4) is unresolved. A
discovery candidate ("share/write about X") promotes to a **task only** — a
`follow_up` is a *chase-able external commitment* (what meeting captures produce),
not a content suggestion. `task_from_candidate` encodes that; if the operator
collapses the queue tables, or meeting-derived candidates later need a follow_up,
this is the one place that changes.
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


def task_from_candidate(candidate: dict[str, Any], *, owner: str) -> dict[str, Any]:
    """Map an accepted `task_candidate` → the `tasks` row to insert (pure).

    Task-only (no follow_up) — see the CPX-4 note above. `source_candidate_id`
    keeps the provenance link back to the candidate.
    """
    return {
        "title": candidate["proposed_action"],
        "description": candidate.get("evidence_text"),
        "owner": owner,
        "source_candidate_id": candidate["id"],
        "status": "open",
    }
