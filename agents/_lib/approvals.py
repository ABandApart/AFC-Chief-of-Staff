"""Approval gate (B2) — the human "yes" trust boundary for outbound actions.

Any agent that wants to take a **world-affecting action** (publish content,
create a Drive doc, send an email) does not act directly. It calls
`request_approval(...)`, which inserts a `pending` row into `approval_queue`.
The Discord `approvals` cog posts that row to `#approvals` with Approve /
Reject / Edit buttons; the registered handler for the item's `item_type` runs
**only** after a human clicks Approve (or Edit → approve-with-changes).

Trust-boundary rules baked in here:
  - The human Discord click is the ONLY authority. No `item_type` handler runs
    without a row transitioning out of `pending` via a Discord decision.
  - Ingested / untrusted content can *populate a payload* but can never
    *approve* it — approval is a separate, human-driven transition.
  - Handlers are the only place an outbound side-effect lives.

This module is deliberately Discord-free: it owns the registry, the pure state
machine, the JSONB envelope shape, and the guarded DB writes. The cog
(`agents/discord_bot/cogs/approvals.py`) owns the Discord surface and calls in
here. Keeping it split lets the state-machine / merge / registry logic be
unit-tested without a bot or a database.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agents._lib import db

# A handler takes the (possibly edited) caller payload and performs the
# outbound act, returning a short human-readable result for the #approvals
# thread. Handlers are synchronous — the cog runs a slow one in a thread.
Handler = Callable[[dict[str, Any]], str]

# item_type -> handler. An agent registers how its item_type executes on
# approve, at import time, via register_handler().
HANDLERS: dict[str, Handler] = {}

# The three terminal decisions and the status each produces. A row must be
# `pending` to transition; anything else is an already-decided no-op.
_ACTION_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "edit": "edited",
}

# Decisions whose handler runs (the action actually ships). Reject does not.
DISPATCH_STATUSES = frozenset({"approved", "edited"})

# item_types whose handler contacts a third party (email, publish, Drive). A
# bare button click is too cheap for these — especially a mobile mis-tap — so
# approving one requires a typed confirmation token (PRD-b2 Amendment 1). This
# narrows *how deliberately* a yes is given; it does not widen *what* may ship.
HIGH_CONSEQUENCE_ITEM_TYPES = frozenset(
    {"email_send", "content_publish", "drive_doc_create"}
)

# The literal an operator types to confirm a high-consequence dispatch.
CONFIRM_TOKEN = "SEND"


def requires_typed_confirm(item_type: str) -> bool:
    """True if approving this item_type must be gated by a typed confirmation."""
    return item_type in HIGH_CONSEQUENCE_ITEM_TYPES


def confirmation_ok(token: str | None) -> bool:
    """True iff the operator typed the confirmation token exactly (trimmed)."""
    return (token or "").strip() == CONFIRM_TOKEN


def is_authorized(user_id: int, operator_id: int) -> bool:
    """True iff the clicking user is the configured operator.

    Fail-closed on an unset operator id: `operator_id == 0` means the allowlist
    was never configured, so *nobody* is authorized (better a dead gate than one
    anyone can drive). PRD-b2 Amendment 1: identity is code, not guild hygiene.
    """
    return operator_id != 0 and user_id == operator_id


class HandlerNotRegisteredError(KeyError):
    """No handler is registered for an item_type at dispatch time."""


# --- handler registry -----------------------------------------------------


def register_handler(item_type: str, fn: Handler) -> None:
    """Register how `item_type` executes on approve. Last registration wins."""
    HANDLERS[item_type] = fn


def get_handler(item_type: str) -> Handler:
    """Look up the handler for `item_type`, or raise HandlerNotRegisteredError.

    Raising (rather than silently no-op'ing) is deliberate: a row that reached
    approval with no way to execute is an operator-visible bug, not something
    to swallow.
    """
    try:
        return HANDLERS[item_type]
    except KeyError as e:
        raise HandlerNotRegisteredError(
            f"no approval handler registered for item_type={item_type!r}; "
            f"known: {sorted(HANDLERS)}"
        ) from e


# --- pure envelope / state helpers (unit-tested, no DB) -------------------
#
# The `payload` JSONB column stores an envelope, not the bare caller payload,
# so the human-facing summary and the editable-field name persist alongside
# the data the handler needs:
#     {"summary": str, "payload": {...}, "edit_field": str}
# The handler only ever receives envelope["payload"].


def build_envelope(summary: str, payload: dict[str, Any], edit_field: str) -> dict[str, Any]:
    """Wrap a caller payload + its human summary into the stored JSONB shape."""
    return {"summary": summary, "payload": dict(payload), "edit_field": edit_field}


def envelope_summary(envelope: dict[str, Any]) -> str:
    return str(envelope.get("summary", ""))


def envelope_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    return dict(envelope.get("payload") or {})


def envelope_edit_field(envelope: dict[str, Any]) -> str:
    """The payload key the Edit modal amends (defaults to 'text')."""
    return str(envelope.get("edit_field") or "text")


def merge_edit(payload: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge an edit into a payload, returning a new dict.

    Pure so the edit path is testable without a modal. Shallow by design:
    v1 edits replace a single top-level draft field, not deep structures.
    """
    return {**payload, **updates}


def next_status(current: str, action: str) -> str | None:
    """The state machine, as a pure function.

    Returns the new status for `action` if `current` is `pending`; returns
    None if the row is already decided (the double-click no-op). The DB write
    enforces the same rule atomically via `WHERE status='pending'`; this
    mirror exists so the guard is unit-testable and self-documenting.
    """
    if current != "pending":
        return None
    if action not in _ACTION_STATUS:
        raise ValueError(f"unknown approval action: {action!r}")
    return _ACTION_STATUS[action]


# --- DB writes / reads ----------------------------------------------------


def request_approval(
    *,
    item_type: str,
    payload: dict[str, Any],
    summary: str,
    ref_id: int | None = None,
    edit_field: str = "text",
) -> int:
    """Enqueue a world-affecting action for human approval. Returns the row id.

    Inserts a `pending` row; the cog posts it to `#approvals`. This never
    executes anything — that only happens when a human decides in Discord.

    `edit_field` names the single payload key the Edit modal amends (the draft
    text, by convention). `ref_id` is an optional pointer back to the row that
    spawned this request (e.g. a content_pipeline id); it defaults to 0 because
    the column is NOT NULL and not every caller has a back-reference.
    """
    envelope = build_envelope(summary, payload, edit_field)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approval_queue (item_type, item_ref_id, payload, status)
            VALUES (%s, %s, %s, 'pending')
            RETURNING id
            """,
            (item_type, ref_id if ref_id is not None else 0, Jsonb(envelope)),
        )
        row = cur.fetchone()
        assert row is not None
        return row[0]


def list_undelivered() -> list[dict[str, Any]]:
    """Pending rows not yet posted to Discord (the cog's post queue)."""
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, item_type, item_ref_id, payload, status
            FROM approval_queue
            WHERE status = 'pending' AND discord_message_id IS NULL
            ORDER BY posted_at
            """
        )
        return cur.fetchall()


def list_pending_posted() -> list[dict[str, Any]]:
    """Pending rows already posted (views to re-attach on bot restart).

    item_type is included so the re-attached View knows whether the item is
    high-consequence (typed-confirm) without a per-click DB read.
    """
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, item_type, discord_message_id
            FROM approval_queue
            WHERE status = 'pending' AND discord_message_id IS NOT NULL
            ORDER BY posted_at
            """
        )
        return cur.fetchall()


def get_row(row_id: int) -> dict[str, Any] | None:
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, item_type, item_ref_id, payload, discord_message_id,
                   status, edit_notes
            FROM approval_queue
            WHERE id = %s
            """,
            (row_id,),
        )
        return cur.fetchone()


def mark_posted(row_id: int, discord_message_id: int) -> None:
    """Record the Discord message id so the row isn't re-posted, and so its
    view can be re-attached after a restart."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE approval_queue SET discord_message_id = %s WHERE id = %s",
            (str(discord_message_id), row_id),
        )


def decide(
    row_id: int,
    action: str,
    *,
    payload: dict[str, Any] | None = None,
    edit_notes: str | None = None,
) -> dict[str, Any] | None:
    """Atomically transition a `pending` row per `action`; the idempotency gate.

    The `WHERE status = 'pending'` clause is the authority: a second click on an
    already-decided row updates zero rows and returns None (the no-op), so one
    decision yields exactly one execution even under concurrent clicks.

    On success returns a dict with the new `status`, the `item_type`, and the
    `payload` the handler should receive (the edited envelope's inner payload
    for an edit, else the stored one). Returns None if the row was not pending.
    """
    new_status = _ACTION_STATUS.get(action)
    if new_status is None:
        raise ValueError(f"unknown approval action: {action!r}")

    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # Serialize the read-modify-write for edits (and guard the transition)
        # inside one transaction; the WHERE clause still does the real gating.
        with conn.transaction():
            if action == "edit":
                new_envelope = _edited_envelope(cur, row_id, payload)
                if new_envelope is None:
                    return None  # not pending (or gone)
                cur.execute(
                    """
                    UPDATE approval_queue
                    SET status = %s, decided_at = now(),
                        payload = %s, edit_notes = %s
                    WHERE id = %s AND status = 'pending'
                    RETURNING item_type
                    """,
                    (new_status, Jsonb(new_envelope), edit_notes, row_id),
                )
                envelope = new_envelope
            else:
                cur.execute(
                    """
                    UPDATE approval_queue
                    SET status = %s, decided_at = now()
                    WHERE id = %s AND status = 'pending'
                    RETURNING item_type, payload
                    """,
                    (new_status, row_id),
                )

            updated = cur.fetchone()
            if updated is None:
                return None  # already decided — the double-click no-op
            # Approve/reject return the stored envelope; edit already computed
            # the merged one above.
            if action != "edit":
                envelope = updated["payload"]

    return {
        "status": new_status,
        "item_type": updated["item_type"],
        "payload": envelope_payload(envelope),
    }


def _edited_envelope(
    cur: psycopg.Cursor[dict[str, Any]],
    row_id: int,
    updates: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Read the current envelope (if still pending) and merge `updates` into
    its inner payload. Returns the new envelope, or None if not pending."""
    cur.execute(
        "SELECT payload FROM approval_queue WHERE id = %s AND status = 'pending'",
        (row_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    envelope = row["payload"]
    merged = merge_edit(envelope_payload(envelope), updates or {})
    return {**envelope, "payload": merged}


# --- demo handler ---------------------------------------------------------
#
# Smokes the full loop before any real outbound exists (PRD verification):
# request_approval(item_type="noop_echo", ...) → Approve in #approvals → this
# runs. It has no side-effect beyond returning a string, so it is safe to ship
# and safe to leave registered.


def noop_echo(payload: dict[str, Any]) -> str:
    text = payload.get("text", "")
    return f"echoed: {text}"


register_handler("noop_echo", noop_echo)
