"""Send capture — detect a sent touch and mark it sent (Track O send half).

`PRD-outreach-gmail-channel.md` §8 step 5. Polls Gmail `history.list`
(`gmail.metadata` scope — G2) from a stored `history_id` watermark, finds newly
**SENT** messages, correlates each to its touch by **threadId** (the V1-fallback
key — the custom header does not survive send), and records `sent_at` /
`sent_via='gmail_api'` / `gmail_message_id`.

**A not-ready capture records UNCONDITIONALLY (§6)** — the mail has already gone,
so refusing the write would only lose the record and leave the arc thinking the
touch is still due (the 0025 ready-guard exempts capture paths). It is surfaced as
a **Ted alert** (a marked `WARNING` until Ted's log scanner lands, `35-` §14).

This path advances the **arc** — a touch marked sent lets the sequence progress and
the next touch schedule. The verbatim **`sent_body`** comes from the dedicated BCC
mailbox over IMAP (§3): `gmail.metadata` cannot read message bodies (that is the G2
guarantee), so the body is a separate capture path with its own credential — the
next slice. This one records THAT the send happened, by threadId + the send label.

Runs where the token lives (barry-agent); the Google SDK is imported lazily via
`gmail.service()`, so this module loads and its tests run green on the build box.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from agents._lib import db
from agents.outreach import gmail

logger = logging.getLogger(__name__)

SENT_VIA = "gmail_api"


# --- watermark ---------------------------------------------------------------

def read_watermark(conn: object) -> str | None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT history_id FROM gmail_channel_state WHERE only_row")
        row = cur.fetchone()
        return row[0] if row else None


def save_watermark(conn: object, history_id: str) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "INSERT INTO gmail_channel_state (only_row, history_id, updated_at) "
            "VALUES (true, %s, now()) "
            "ON CONFLICT (only_row) DO UPDATE "
            "  SET history_id = EXCLUDED.history_id, updated_at = now()",
            (history_id,),
        )


# --- Gmail history (service injected; no Google import here) ------------------

def sent_events(svc: Any, start_history_id: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Walk `history.list` from the watermark and return
    `(newest_history_id, [{message_id, thread_id}])` for every newly SENT message.

    Returns `(None, [])` when Gmail 404s the start id (history expired past the
    ~week Gmail keeps) — the caller then re-bootstraps the watermark. The 404 is
    duck-typed on `exc.resp.status` so this needs no `googleapiclient` import.
    """
    events: list[dict[str, Any]] = []
    newest = start_history_id
    page_token: str | None = None
    while True:
        try:
            resp = svc.users().history().list(
                userId="me", startHistoryId=start_history_id,
                historyTypes=["messageAdded"], pageToken=page_token).execute()
        except Exception as exc:  # noqa: BLE001 — duck-typed Gmail HttpError
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                return (None, [])
            raise
        newest = resp.get("historyId", newest)
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                message = added.get("message", {})
                if "SENT" in (message.get("labelIds") or []):
                    events.append({"message_id": message["id"],
                                   "thread_id": message.get("threadId")})
        page_token = resp.get("nextPageToken")
        if not page_token:
            return (newest, events)


# --- correlation + recording -------------------------------------------------

def correlate(conn: object, thread_id: str | None) -> int | None:
    """The touch whose draft opened this thread and is not yet marked sent."""
    if not thread_id:
        return None
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id FROM outreach_touches "
            "WHERE gmail_thread_id = %s AND sent_at IS NULL LIMIT 1",
            (thread_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def record_send(conn: object, touch_id: int, message_id: str) -> dict[str, bool]:
    """Mark the touch sent via gmail_api, idempotent on `sent_at` (a duplicate
    history event cannot re-record). Returns whether it recorded and whether the
    latest packet was ready — a not-ready record is the Ted-alert case."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_touches SET sent_at = now(), sent_via = %s, "
            "gmail_message_id = %s WHERE id = %s AND sent_at IS NULL RETURNING id",
            (SENT_VIA, message_id, touch_id),
        )
        recorded = cur.fetchone() is not None
        ready = True
        if recorded:
            cur.execute(
                "SELECT ready FROM outreach_packets WHERE touch_id = %s "
                "ORDER BY assembled_at DESC, id DESC LIMIT 1",
                (touch_id,),
            )
            row = cur.fetchone()
            ready = row[0] if row else True
    return {"recorded": recorded, "ready": ready}


# --- orchestration -----------------------------------------------------------

def run(*, today: date | None = None) -> dict[str, int]:
    """One capture pass. First run bootstraps the watermark and captures nothing
    (there is no history before 'now'); later runs walk history from it."""
    today = today or date.today()
    counts = {"events": 0, "recorded": 0, "unmatched": 0, "not_ready": 0}
    svc = gmail.service()
    with db.connection() as conn:
        watermark = read_watermark(conn)
        if watermark is None:
            history_id = svc.users().getProfile(userId="me").execute()["historyId"]
            save_watermark(conn, history_id)
            logger.info("gmail capture: watermark bootstrapped at %s", history_id)
            return counts

        newest, events = sent_events(svc, watermark)
        if newest is None:  # history expired — re-bootstrap, skip this cycle
            history_id = svc.users().getProfile(userId="me").execute()["historyId"]
            save_watermark(conn, history_id)
            logger.warning("gmail capture: history watermark expired; re-bootstrapped")
            return counts

        counts["events"] = len(events)
        for event in events:
            touch_id = correlate(conn, event["thread_id"])
            if touch_id is None:
                counts["unmatched"] += 1
                continue
            result = record_send(conn, touch_id, event["message_id"])
            if not result["recorded"]:
                continue
            counts["recorded"] += 1
            if not result["ready"]:
                counts["not_ready"] += 1
                logger.warning(
                    "gmail capture: touch %s recorded SENT but its packet was NOT "
                    "READY (unresolved slot or stale fact) — TED ALERT", touch_id)
        save_watermark(conn, newest)
    return counts


def main(argv: list[str] | None = None) -> int:
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    c = run()
    print(f"gmail capture: {c['events']} sent event(s), {c['recorded']} recorded "
          f"({c['unmatched']} unmatched, {c['not_ready']} not-ready)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
