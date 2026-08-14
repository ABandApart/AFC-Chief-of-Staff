"""Gate 1 — the intake decision core (Track O, `35-` §5).

The human gate that admits a company into the five-touch arc. Discord-free by
design, exactly like `_lib/task_tinder` and `_lib/approvals`: the pure decision
rules and the guarded DB writes live here so they are unit-testable without a
bot, and the cog owns only the Discord surface.

**Three outcomes**, from the card's three buttons (`35-` §5):

  * **Work this** — `status='in_sequence'`, stamp `sequence_started_at`, and
    materialise five touches from the Selector. Capacity-gated (§8).
  * **Watchlist** — park it; Trent Crimm watches for a trigger (§10).
  * **Drop** — not worth pursuing.

**Idempotency is the database's**, not this module's: every transition is
`UPDATE ... WHERE status = 'candidate'`, so a double-click updates zero rows and
returns None. Same guarantee the Task Tinder cog relies on.

**Two deviations from the spec's own wording, both deliberate:**

*Drop sets `status='dropped'`; it does not delete the row.* `37-` D1 says "Drop —
delete the row", but `outreach_evidence` is `ON DELETE CASCADE`, so deleting a
target destroys its accumulated `first_seen_at` history — the one datum the whole
subsystem exists to accrue and the one that cannot be rebuilt. Re-importing the
company later would restart its posting-age clock at zero. `pollable_targets`
already excludes dropped targets, so the row costs nothing but keeps the history.

*Watchlisting at intake records a canned `stalled_reason`.* The schema requires
one (`outreach_targets_stalled_ck`), and rightly — but that constraint was
written for the drain rule, where "what stalled it" is a real question. At intake
nothing has stalled yet, so a fixed marker distinguishes "parked before it was
ever worked" from "went cold after five touches", which matters when Trent Crimm
later decides what to say.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db, packet

logger = logging.getLogger(__name__)

# action -> the status it produces. A target must be `candidate` to transition.
_ACTION_STATUS = {
    "work": "in_sequence",
    "watchlist": "watchlist",
    "drop": "dropped",
}

# Only `work` materialises the arc.
SEQUENCING_ACTIONS = frozenset({"work"})

# §10: `watch_until` defaults to 18 months out. At intake there is no
# `sequence_completed_at` to measure from, so it runs from today.
WATCH_MONTHS = 18
WATCH_DAYS = WATCH_MONTHS * 30

# Distinguishes a target parked before it was ever worked from one that went
# cold after five touches — the drain rule's reasons are free text from the
# operator, this one is a marker.
INTAKE_WATCHLIST_REASON = "watchlisted_at_intake"


class CapacityFullError(Exception):
    """The cold ceiling (or the E1 re-engagement allowance) is reached.

    Not an error condition — it is the capacity discipline working. `35-` is
    explicit that capacity is the constraint every other element exists to
    enforce, so this refuses the admission rather than quietly exceeding it.
    """


class NotReadyToWorkError(Exception):
    """The target is missing something the arc structurally requires.

    Raised instead of letting the database's `outreach_targets_seq_ck` fire, so
    the operator gets told *which* judgement is missing rather than a constraint
    name.
    """


def next_status(current: str, action: str) -> str | None:
    """The status `action` produces if the target is still `candidate` (pure).

    None means already-decided — the idempotent double-click no-op, which the DB
    write enforces independently via `WHERE status = 'candidate'`.
    """
    if current != "candidate":
        return None
    if action not in _ACTION_STATUS:
        raise ValueError(f"unknown intake action: {action!r}")
    return _ACTION_STATUS[action]


def capacity_blocks(capacity: dict[str, Any], *, is_reengagement: bool) -> str | None:
    """Why this admission is refused, or None if there is room (pure).

    Cold and re-engagement are metered separately: E1 runs the allowance of 3
    *above* the cold cap so a detected departure trigger — the
    highest-converting message in the method — is never blocked by cold targets
    mid-arc.
    """
    if is_reengagement:
        live, ceiling, label = (
            capacity["reengagement_live"], capacity["reengagement_ceiling"], "re-engagement",
        )
    else:
        live, ceiling, label = (
            capacity["cold_live"], capacity["cold_ceiling"], "cold",
        )
    if live >= ceiling:
        return (
            f"{label} capacity is full ({live}/{ceiling}) — drain a stalled "
            "target before admitting another"
        )
    return None


def work_blocks(target: dict[str, Any]) -> str | None:
    """What structurally prevents sequencing this target, or None (pure).

    Mirrors `outreach_targets_seq_ck` so the refusal names the missing judgement.
    `function_state` is the two-tab diagnostic — five minutes of human work that
    the method is explicit cannot be bought or inferred (`35-` §6).
    """
    if not target.get("stage"):
        return "no stage set — the Selector has no stage-specific template for slot 1"
    if not target.get("function_state"):
        return (
            "function_state not set — do the two-tab diagnostic first "
            "(self_covered / under_led / vacant_seat); it is what S4 and the "
            "arc both rest on"
        )
    return None


# --- reads --------------------------------------------------------------------


def read_capacity(conn: object) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT * FROM v_outreach_capacity")
        return cur.fetchone()


def list_undelivered(conn: object) -> list[dict[str, Any]]:
    """Targets scored to `work` that have never been surfaced (§5 intake gate)."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT * FROM v_outreach_scored
            WHERE status = 'candidate'
              AND treatment = 'work'
              AND intake_message_id IS NULL
            ORDER BY score DESC NULLS LAST, days_since_trigger
            """
        )
        return cur.fetchall()


def list_posted_undecided(conn: object) -> list[dict[str, Any]]:
    """Cards already posted and still awaiting a decision — the Views to re-bind
    after a bot restart, so a pre-restart card's buttons keep working."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id, intake_message_id FROM outreach_targets "
            "WHERE status = 'candidate' AND intake_message_id IS NOT NULL "
            "ORDER BY id"
        )
        return cur.fetchall()


def target_evidence(conn: object, target_id: int) -> list[dict[str, Any]]:
    """Live evidence for the card, newest-observed first. Closed facts are
    excluded — §3 says they are history, never a live signal."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT * FROM v_outreach_evidence_display "
            "WHERE target_id = %s AND closed_at IS NULL "
            "ORDER BY first_seen_at",
            (target_id,),
        )
        return cur.fetchall()


def mark_posted(conn: object, target_id: int, message_id: int) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_targets SET intake_message_id = %s WHERE id = %s",
            (str(message_id), target_id),
        )


# --- the decision --------------------------------------------------------------


def decide(target_id: int, action: str, *, today: date | None = None) -> dict[str, Any] | None:
    """Apply an intake decision. Returns a summary, or None if already decided.

    `work` runs capacity and readiness checks *before* the transition, and
    materialises the five touches in the **same transaction** as the status
    change — a target left `in_sequence` with no touches would sit in the
    pipeline consuming a capacity slot with nothing due, forever.

    Raises `CapacityFullError` or `NotReadyToWorkError` without changing anything.
    """
    if action not in _ACTION_STATUS:
        raise ValueError(f"unknown intake action: {action!r}")
    today = today or date.today()

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM v_outreach_scored WHERE id = %s", (target_id,))
            target = cur.fetchone()
        if target is None:
            raise KeyError(f"no outreach target {target_id}")
        if target["status"] != "candidate":
            return None                      # already decided — the no-op

        if action == "work":
            if blocked := work_blocks(target):
                raise NotReadyToWorkError(blocked)
            if full := capacity_blocks(
                read_capacity(conn), is_reengagement=target["is_reengagement"]
            ):
                raise CapacityFullError(full)

        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                if action == "work":
                    cur.execute(
                        "UPDATE outreach_targets SET status = 'in_sequence', "
                        "sequence_started_at = %s "
                        "WHERE id = %s AND status = 'candidate' RETURNING id",
                        (today, target_id),
                    )
                elif action == "watchlist":
                    cur.execute(
                        "UPDATE outreach_targets SET status = 'watchlist', "
                        "stalled_reason = %s, watch_until = %s "
                        "WHERE id = %s AND status = 'candidate' RETURNING id",
                        (INTAKE_WATCHLIST_REASON,
                         today + timedelta(days=WATCH_DAYS), target_id),
                    )
                else:
                    cur.execute(
                        "UPDATE outreach_targets SET status = 'dropped' "
                        "WHERE id = %s AND status = 'candidate' RETURNING id",
                        (target_id,),
                    )
                if cur.fetchone() is None:
                    return None              # lost the race to another click

            touches: list[dict[str, Any]] = []
            if action in SEQUENCING_ACTIONS:
                facts = {
                    "trigger_kind": target["trigger_kind"],
                    "days_since_trigger": target["days_since_trigger"],
                    "open_role_age_days": _open_role_age(conn, target_id),
                }
                touches = packet.materialize_sequence(conn, target, facts, today=today)

    logger.info(
        "outreach intake: target %s -> %s (%d touch(es))",
        target_id, _ACTION_STATUS[action], len(touches),
    )
    return {
        "status": _ACTION_STATUS[action],
        "target": target,
        "touches": touches,
    }


def _open_role_age(conn: object, target_id: int) -> int | None:
    """Age of the oldest still-open, non-stale req — the Selector's condition."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT max(age_days) FROM v_outreach_evidence_display "
            "WHERE target_id = %s AND fact_kind = 'open_role' "
            "AND closed_at IS NULL AND freshness <> 'stale'",
            (target_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None
