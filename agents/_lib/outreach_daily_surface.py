"""#outreach daily surface — the contact-card core (Track O, PRD-outreach-daily-surface.md).

Discord-free by design, exactly like `_lib/outreach_intake`: the pure rules and
the guarded DB writes live here so they are unit-testable without a bot, and the
`cogs/outreach_today.py` surface owns only Discord.

**The surface shows one thing: today's due touches** (R1 — no worklist backfill,
no decision cards). Each carries two actions:

  * **Contact** — `marked_working_at = now()`. Advisory intent only; it changes
    no touch state and gates nothing. The draft already exists in Gmail (the
    06:00 loop); Contact does not create or send it.
  * **Defer** — `snoozed_until = today + 1` (clamped to `window_closes`) plus a
    **required** `snooze_note` (R3). A snooze never crosses the window
    (`outreach_touches_snooze_ck`), so when the window closes today a defer is
    refused — the touch is left to the drain, not skipped (R2: no Skip button).

**Idempotency is the database's**: every write is guarded on the touch still
being live (`sent_at IS NULL AND skipped_at IS NULL`), so a click on an
already-resolved touch updates zero rows and returns None — the same guarantee
the intake cog relies on.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row

from agents._lib import packet

logger = logging.getLogger(__name__)

# Driving facts shown on a card. The full evidence set is in the packet/NocoDB;
# the card needs only the two or three facts that drive the touch (`35-` §7).
MAX_CARD_FACTS = 3


class DeferWindowClosedError(Exception):
    """A defer would push `snoozed_until` past `window_closes`.

    Not a failure — the snooze constraint working. A touch whose window closes
    today cannot be snoozed to a later day; it is left to the drain rule (§8),
    which is why this surface has no Skip (R2).
    """


# --- pure rules ---------------------------------------------------------------


def snooze_date(today: date, window_closes: date) -> date | None:
    """The `snoozed_until` a defer sets, or None if the window closes too soon (pure).

    "Not today" is the smallest useful snooze — `today + 1` — because the daily
    query treats `snoozed_until <= today` as still-due. The snooze may not cross
    `window_closes` (`outreach_touches_snooze_ck`), so a touch whose window ends
    today (or earlier) cannot be deferred: None.
    """
    nxt = today + timedelta(days=1)
    return nxt if nxt <= window_closes else None


def format_driving_facts(evidence: list[dict[str, Any]], *, limit: int = MAX_CARD_FACTS) -> str:
    """The top facts as short dated lines, freshness marked (`35-` §3 display rules).

    Closed and stale facts are shown struck/marked rather than hidden — the
    operator needs to see that the driving fact went stale, which is exactly the
    R19 case the freshness tiers exist for.
    """
    live = [f for f in evidence if f.get("closed_at") is None]
    if not live:
        return "_no live evidence_"
    marks = {"fresh": "", "ageing": " ⚠️ ageing", "stale": " ⛔ stale"}
    lines = []
    for fact in live[:limit]:
        payload = fact.get("payload") or {}
        title = payload.get("title") or fact.get("fact_kind", "fact")
        mark = marks.get(fact.get("freshness", ""), "")
        age = fact.get("age_days")
        age_str = f" — open {age}d" if age is not None else ""
        lines.append(f"• {title}{age_str}{mark}")
    if len(live) > limit:
        lines.append(f"• _…{len(live) - limit} more_")
    return "\n".join(lines)


def gmail_link(touch: dict[str, Any]) -> str | None:
    """A best-effort link to the touch's Gmail draft thread, or None.

    Links to the *thread* (`gmail_thread_id`, the load-bearing correlation key
    from migration 0025), not the draft id — the draft id is reassigned on send
    (V3) and a draft deep-link is unreliable. Deep-linking straight to the draft
    is a follow-up once probed against the live account, matching the Gmail PRD's
    §7 verify-before-code discipline.
    """
    thread = touch.get("gmail_thread_id")
    if thread:
        return f"https://mail.google.com/mail/u/0/#all/{thread}"
    return None


def bcc_address(touch: dict[str, Any]) -> str:
    """The dedicated-mailbox BCC address for this touch (`35-` §8)."""
    return f"bcc+{touch['bcc_token']}@aiadaptive.co"


# --- reads --------------------------------------------------------------------


_DUE_UNCARDED_SQL = """
    SELECT tc.id, tc.slot, tc.due_date, tc.window_closes, t.company_name
    FROM outreach_touches tc
    JOIN outreach_targets t ON t.id = tc.target_id
    WHERE t.status = 'in_sequence'
      AND tc.sent_at IS NULL
      AND tc.skipped_at IS NULL
      AND tc.window_opens <= %(today)s
      AND tc.window_closes >= %(today)s
      AND (tc.snoozed_until IS NULL OR tc.snoozed_until <= %(today)s)
      AND tc.daily_message_id IS NULL
    ORDER BY tc.due_date, tc.id
"""


def list_due_uncarded(conn: object, *, today: date) -> list[dict[str, Any]]:
    """Due touches with no card yet — the ones to post (R1).

    Mirrors `daily.py`'s due-touch query (window-open, not sent/skipped, snooze
    respected) and adds `daily_message_id IS NULL` so each touch is carded once.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_DUE_UNCARDED_SQL, {"today": today})
        return cur.fetchall()


def list_carded_live(conn: object) -> list[dict[str, Any]]:
    """Carded touches still awaiting action — the Views to re-bind after a
    restart, so a pre-restart card's buttons keep working."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id, daily_message_id FROM outreach_touches "
            "WHERE daily_message_id IS NOT NULL "
            "AND sent_at IS NULL AND skipped_at IS NULL ORDER BY id"
        )
        return cur.fetchall()


def latest_packet(conn: object, touch_id: int) -> dict[str, Any] | None:
    """The most recent assembled packet for a touch (regenerate-never-edit: the
    newest row wins). None when the daily loop has not assembled one yet."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT * FROM outreach_packets WHERE touch_id = %s "
            "ORDER BY assembled_at DESC, id DESC LIMIT 1",
            (touch_id,),
        )
        return cur.fetchone()


def card_inputs(conn: object, touch_id: int) -> dict[str, Any] | None:
    """Everything one contact card renders: touch, target, evidence, packet.

    Reuses `packet.fetch_packet_inputs` for the touch/target/evidence (three
    separate queries — a `t.* , g.*` join would collide on shared column names)
    and adds the saved packet row.
    """
    try:
        touch, target, evidence = packet.fetch_packet_inputs(conn, touch_id)
    except KeyError:
        return None
    return {
        "touch": touch,
        "target": target,
        "evidence": evidence,
        "packet": latest_packet(conn, touch_id),
    }


# --- writes -------------------------------------------------------------------


def mark_carded(conn: object, touch_id: int, message_id: int) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_touches SET daily_message_id = %s WHERE id = %s",
            (str(message_id), touch_id),
        )


def mark_working(conn: object, touch_id: int) -> datetime | None:
    """Set the Contact intent flag. Returns the stamp, or None if the touch is
    no longer live (already sent/skipped) — the idempotent no-op."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_touches SET marked_working_at = now() "
            "WHERE id = %s AND sent_at IS NULL AND skipped_at IS NULL "
            "RETURNING marked_working_at",
            (touch_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def defer(conn: object, touch_id: int, note: str, *, today: date | None = None) -> date | None:
    """Snooze a due touch past today with a required note (R3).

    Returns the new `snoozed_until`, or None if the touch is no longer live.
    Raises `ValueError` on a blank note and `DeferWindowClosedError` when the
    window closes too soon for any snooze.
    """
    if not note or not note.strip():
        raise ValueError("a defer note is required")
    today = today or date.today()

    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT window_closes, sent_at, skipped_at FROM outreach_touches WHERE id = %s",
            (touch_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise KeyError(f"no touch {touch_id}")
    if row["sent_at"] is not None or row["skipped_at"] is not None:
        return None                                   # already resolved — no-op

    until = snooze_date(today, row["window_closes"])
    if until is None:
        raise DeferWindowClosedError(
            "the window closes today — this touch cannot be deferred; "
            "it drains if left unsent"
        )

    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_touches SET snoozed_until = %s, snooze_note = %s "
            "WHERE id = %s AND sent_at IS NULL AND skipped_at IS NULL "
            "RETURNING snoozed_until",
            (until, note.strip(), touch_id),
        )
        written = cur.fetchone()
    if written is None:
        return None                                   # lost the race to a send
    logger.info("outreach daily: touch %s deferred to %s", touch_id, until)
    return written[0]
