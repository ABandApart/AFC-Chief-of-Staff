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

from psycopg.rows import dict_row

from agents._lib import db

# Surface only reasonably-confident candidates (discovery ones are ≥0.55). Below
# this a candidate stays pending but isn't posted to #task-tinder.
MIN_CONFIDENCE = 0.5

# Days a deferred candidate is pushed out (informational for now; re-surfacing
# deferred-past-date candidates is a future enhancement).
DEFER_DAYS = 7

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


# --- DB reads/writes (runtime; the cog calls these off the event loop) --------

_CANDIDATE_COLS = "id, proposed_action, source_type, source_ref, evidence_text, confidence"

# Owned by the bespoke outreach re-score cog (O2, `outreach_rescore.STALE_SOURCE_TYPE`),
# NOT the generic Task Tinder card — the stale-signal re-check needs an S4/S5 modal,
# not a Work/Snooze/Dismiss card. Excluded from both queries so the two cogs never
# both post the same candidate. (Literal, not an import, to keep this lib leaf-level.)
_OUTREACH_RESCORE = "outreach_stale_signal"


def list_undelivered(min_confidence: float = MIN_CONFIDENCE) -> list[dict[str, Any]]:
    """Pending candidates (≥ min_confidence) not yet posted to #task-tinder."""
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_CANDIDATE_COLS} FROM task_candidates "
            "WHERE status = 'pending' AND discord_message_id IS NULL "
            "AND source_type <> %s AND confidence >= %s "
            "ORDER BY confidence DESC, created_at",
            (_OUTREACH_RESCORE, min_confidence),
        )
        return cur.fetchall()


def list_pending_posted() -> list[dict[str, Any]]:
    """Pending candidates already posted (views to re-attach on bot restart)."""
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, discord_message_id FROM task_candidates "
            "WHERE status = 'pending' AND discord_message_id IS NOT NULL "
            "AND source_type <> %s ORDER BY created_at",
            (_OUTREACH_RESCORE,),
        )
        return cur.fetchall()


def mark_posted(candidate_id: int, discord_message_id: int) -> None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE task_candidates SET discord_message_id = %s WHERE id = %s",
            (str(discord_message_id), candidate_id),
        )


def count_pending(min_confidence: float = MIN_CONFIDENCE) -> int:
    """Count of surfaced pending candidates (for the briefing line)."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM task_candidates "
            "WHERE status = 'pending' AND confidence >= %s",
            (min_confidence,),
        )
        return cur.fetchone()[0]


def decide(candidate_id: int, action: str) -> dict[str, Any] | None:
    """Atomically transition a `pending` candidate; the idempotency gate.

    `WHERE status='pending'` is the authority — a second click updates zero rows
    and returns None (the no-op). On success returns `{status, candidate}` where
    `candidate` is the row (so an accept can be promoted). Defer stamps
    `deferred_until`.
    """
    new_status = _ACTION_STATUS.get(action)
    if new_status is None:
        raise ValueError(f"unknown task-tinder action: {action!r}")
    deferred = "now()::date + %s" if action == "defer" else "NULL"
    params: tuple = (new_status, DEFER_DAYS, candidate_id) if action == "defer" \
        else (new_status, candidate_id)
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"UPDATE task_candidates SET status = %s, decided_at = now(), "
            f"deferred_until = {deferred} "
            f"WHERE id = %s AND status = 'pending' RETURNING {_CANDIDATE_COLS}",
            params,
        )
        row = cur.fetchone()
    return None if row is None else {"status": new_status, "candidate": row}


def promote(candidate: dict[str, Any], *, owner: str) -> dict[str, int]:
    """Create the follow_up + linked task for an accepted candidate (one txn)."""
    fu = followup_from_candidate(candidate, owner=owner)
    with db.connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO follow_ups (owner, action, status, escalation_level) "
            "VALUES (%(owner)s, %(action)s, %(status)s, %(escalation_level)s) "
            "RETURNING id",
            fu,
        )
        follow_up_id = cur.fetchone()[0]
        task = task_from_candidate(candidate, owner=owner, follow_up_id=follow_up_id)
        cur.execute(
            "INSERT INTO tasks (title, description, owner, source_candidate_id, "
            "                   status, follow_up_id) "
            "VALUES (%(title)s, %(description)s, %(owner)s, %(source_candidate_id)s, "
            "        %(status)s, %(follow_up_id)s) RETURNING id",
            task,
        )
        task_id = cur.fetchone()[0]
    return {"task_id": task_id, "follow_up_id": follow_up_id}
