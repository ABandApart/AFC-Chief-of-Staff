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
import re
from datetime import date
from email import message_from_bytes, policy
from typing import Any

from agents._lib import creds, db, heartbeat
from agents.outreach import gmail

logger = logging.getLogger(__name__)

SENT_VIA = "gmail_api"

# Dead-man's switch for the BCC body pass (80-telemetry-layer § PERF-4, 15m/10m).
# The tight grace is deliberate: the BCC mailbox is the ONLY source of the
# verbatim sent body, and IMAP going quiet (auth expired, mailbox wedged) is
# invisible on-box. Pinged ONLY when the pass actually runs against a live
# credential — never on the intentional skip when bcc@ isn't set up, so the
# check stays honest: green means bodies are flowing, silent means they aren't.
HEARTBEAT_SLUG_BCC = "cos-outreach-bcc"

# The plus-address token in a BCC copy's routing headers: bcc+<token>@aiadaptive.co.
_BCC_TOKEN_RE = re.compile(r"bcc\+([^@\s]+)@" + re.escape(gmail.BCC_DOMAIN), re.IGNORECASE)
_BCC_IMAP_ITEM = "gmail-bcc-imap"       # the bcc@ IMAP app password (keychain)
_BCC_ADDRESS = f"bcc@{gmail.BCC_DOMAIN}"
_BCC_ROUTING_HEADERS = ("Delivered-To", "X-Original-To", "To", "Cc")


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


# --- BCC body path (the verbatim sent_body, §3) ------------------------------

def bcc_token_from_headers(msg: Any) -> str | None:
    """Pull the correlation token out of a BCC copy's routing headers. The token
    rides in the plus-address (`bcc+<token>@aiadaptive.co`) the draft BCC'd to."""
    for header in _BCC_ROUTING_HEADERS:
        for value in msg.get_all(header, []):
            match = _BCC_TOKEN_RE.search(str(value))
            if match:
                return match.group(1)
    return None


def plain_body(msg: Any) -> str:
    """The message's plain-text body (the verbatim send). Prefer text/plain; fall
    back to the payload as-is. Read in full — this is the SEPARATE bcc@ mailbox, so
    there is no client-mail blast radius (the whole point of G2's dedicated design)."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_content()
        return ""
    return msg.get_content()


def record_body(conn: object, token: str, body: str) -> dict[str, Any]:
    """Attach the verbatim body to the touch with this BCC token, and claim the
    send for the `bcc` path only if the history path has not already. Returns
    whether it matched, whether THIS write newly marked it sent, and the packet's
    readiness (a newly-sent not-ready capture is the Ted-alert case)."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id, sent_at IS NULL AS was_unsent FROM outreach_touches "
            "WHERE bcc_token = %s", (token,))
        row = cur.fetchone()
        if row is None:
            return {"matched": False, "newly_sent": False, "ready": True}
        touch_id, was_unsent = row
        cur.execute(
            "UPDATE outreach_touches SET sent_body = %s, "
            "sent_at = COALESCE(sent_at, now()), sent_via = COALESCE(sent_via, 'bcc') "
            "WHERE id = %s", (body, touch_id))
        ready = True
        if was_unsent:
            cur.execute("SELECT ready FROM outreach_packets WHERE touch_id = %s "
                        "ORDER BY assembled_at DESC, id DESC LIMIT 1", (touch_id,))
            r = cur.fetchone()
            ready = r[0] if r else True
    return {"matched": True, "newly_sent": was_unsent, "ready": ready}


def bcc_imap() -> Any:
    """Open an IMAP session to the dedicated bcc@ inbox. `imaplib` is stdlib; only
    the app-password credential is external."""
    import imaplib
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(_BCC_ADDRESS, creds.keychain_get(_BCC_IMAP_ITEM))
    imap.select("INBOX")
    return imap


def capture_bcc(imap: Any, conn: object) -> dict[str, int]:
    """Read UNSEEN bcc@ messages, correlate each by its token, write sent_body, and
    mark it seen. `imap` is injected so this is unit-testable without a live inbox."""
    counts = {"seen": 0, "bodies": 0, "unmatched": 0, "not_ready": 0}
    _typ, data = imap.search(None, "UNSEEN")
    for num in (data[0] or b"").split():
        _typ, fetched = imap.fetch(num, "(RFC822)")
        raw = fetched[0][1]
        msg = message_from_bytes(raw, policy=policy.default)
        counts["seen"] += 1
        token = bcc_token_from_headers(msg)
        if token:
            result = record_body(conn, token, plain_body(msg))
            if result["matched"]:
                counts["bodies"] += 1
                if result["newly_sent"] and not result["ready"]:
                    counts["not_ready"] += 1
                    logger.warning("gmail capture(bcc): touch for token %s recorded "
                                   "SENT but its packet was NOT READY — TED ALERT", token)
            else:
                counts["unmatched"] += 1
        imap.store(num, "+FLAGS", "\\Seen")
    return counts


def run_bcc() -> dict[str, int]:
    """The BCC body pass. No-ops (logs) when the `gmail-bcc-imap` credential is
    absent, so it is safe before the bcc@ app password is set up."""
    zero = {"seen": 0, "bodies": 0, "unmatched": 0, "not_ready": 0}
    try:
        creds.keychain_get(_BCC_IMAP_ITEM)
    except RuntimeError:
        logger.info("gmail capture(bcc): no '%s' credential — body capture skipped",
                    _BCC_IMAP_ITEM)
        return zero
    imap = bcc_imap()
    try:
        with db.connection() as conn:
            counts = capture_bcc(imap, conn)
        # Success path only: a live credential and the pass completed. The skip
        # above never reaches here, so the switch never falsely reads green.
        heartbeat.ping(HEARTBEAT_SLUG_BCC)
        return counts
    except Exception:
        heartbeat.ping_fail(HEARTBEAT_SLUG_BCC)
        raise
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    c = run()
    b = run_bcc()
    print(f"gmail capture: {c['events']} sent event(s), {c['recorded']} recorded "
          f"({c['unmatched']} unmatched, {c['not_ready']} not-ready); "
          f"bcc: {b['bodies']} body(ies) from {b['seen']} message(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
