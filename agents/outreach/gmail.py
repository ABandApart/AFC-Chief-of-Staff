"""Gmail drafting — compose outreach drafts in the operator's mailbox (Track O).

`PRD-outreach-gmail-channel.md` §8 step 4. For each due touch this creates a Gmail
**draft** (never a send — G3, enforced by `tests/test_no_outbound_send.py`) from
the assembled packet, addressed to the target's contact and **BCC'd to the
dedicated `bcc+<token>@aiadaptive.co` mailbox** so the send-capture poller can
later match the sent message to this touch by threadId + that token (the V1-probe
fallback: the custom header does not survive draft→sent).

**Idempotent, create-once (G-R4).** `outreach-daily` regenerates packets every
morning, so a blind create would pile up duplicate drafts. A touch that already
carries a `gmail_draft_id` is left alone — the draft is the operator's to edit and
send. (Update-in-place-when-unedited is the §6 open sub-question — a later slice;
this stores `draft_body_hash` ready for it but does not overwrite an existing
draft.)

**§6 readiness:** the draft is created regardless of `ready` — an unresolved
`[operator]` slot is shown, not a reason to withhold — because R1 (a placeholder
reaching a prospect) is a property of *sending*, and a draft sits unsent in the
operator's own mailbox. The `outreach_touch_ready_guard()` (amended in 0025) is
what keeps a not-ready assertion off the wire.

Runs where the OAuth token lives (barry-agent): the Google SDK is the barry-agent
-only `gmail` group and is imported LAZILY here, so the build box (which never
syncs it) imports this module fine and its tests mock the service.

V1-V3 probe (2026-08-31, recorded in the PRD §7): custom header does NOT survive
send; `gmail.metadata` allows `history.list`; draft message.id is reassigned on
send — hence correlation is threadId + BCC token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from email.message import EmailMessage
from typing import Any

from psycopg.rows import dict_row

from agents._lib import creds, db

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.metadata",
]
BCC_DOMAIN = "aiadaptive.co"


# --- pure helpers (no Google import) -----------------------------------------

def bcc_address(token: str, *, domain: str = BCC_DOMAIN) -> str:
    """The dedicated-mailbox plus-address that carries the correlation token
    (35- §8): `bcc+<token>@aiadaptive.co`. All land in the one `bcc@` inbox."""
    return f"bcc+{token}@{domain}"


def body_hash(body: str) -> str:
    """Stable hash of the composed body — stored so a later slice can tell whether
    the operator has edited the draft before updating it in place."""
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def build_raw(*, to: str, subject: str, body: str, bcc: str) -> str:
    """Compose one draft as a base64url RFC-822 message (what drafts.create wants)."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# --- the Gmail service (lazy — barry-agent only) -----------------------------

def service() -> Any:
    """Build the Gmail API client from the keychain creds. Imports the Google SDK
    lazily so the build box (no `gmail` group) never trips on it."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client = json.loads(creds.keychain_get("gmail-oauth-client"))
    cred = Credentials(
        token=None,
        refresh_token=creds.keychain_get("gmail-refresh-token"),
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    cred.refresh(Request())
    return build("gmail", "v1", credentials=cred)


def create_draft(svc: Any, *, to: str, subject: str, body: str, bcc: str) -> dict[str, str]:
    """Create one Gmail draft. Returns the ids we persist for correlation.
    `svc` is injected so this is unit-testable without a live client."""
    raw = build_raw(to=to, subject=subject, body=body, bcc=bcc)
    draft = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    message = draft.get("message", {})
    return {
        "draft_id": draft["id"],
        "thread_id": message.get("threadId"),
        "message_id": message.get("id"),
    }


# --- DB (draftable touches + persisting draft state) -------------------------

_DRAFTABLE_SQL = """
    SELECT t.id AS touch_id, t.bcc_token, tg.contact_email, tg.company_name,
           p.subject_line, p.body_filled
      FROM outreach_touches t
      JOIN outreach_targets tg ON tg.id = t.target_id
      JOIN LATERAL (
             SELECT subject_line, body_filled
               FROM outreach_packets
              WHERE touch_id = t.id
              ORDER BY assembled_at DESC, id DESC
              LIMIT 1
           ) p ON true
     WHERE tg.status = 'in_sequence'
       AND t.sent_at IS NULL AND t.skipped_at IS NULL
       AND tg.contact_email IS NOT NULL
       AND t.gmail_draft_id IS NULL          -- create-once: not yet drafted
     ORDER BY t.due_date, t.id
"""


def list_draftable(conn: object) -> list[dict[str, Any]]:
    """Touches in an active sequence that have a packet + a contact and no draft
    yet. A touch that already has a `gmail_draft_id` is intentionally excluded
    (create-once)."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_DRAFTABLE_SQL)
        return cur.fetchall()


def save_draft_state(conn: object, touch_id: int, ids: dict[str, str],
                     hash_: str) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_touches SET gmail_draft_id = %s, gmail_thread_id = %s, "
            "draft_created_at = now(), draft_body_hash = %s WHERE id = %s",
            (ids["draft_id"], ids["thread_id"], hash_, touch_id),
        )


# --- orchestration -----------------------------------------------------------

def run(*, dry_run: bool = False) -> dict[str, int]:
    """One drafting pass: create a draft for each draftable touch. Returns counts.
    In `dry_run` nothing is composed or written — it only reports what it would do
    (and makes no Gmail call), so it is safe on the build box without the token."""
    with db.connection() as conn:
        touches = list_draftable(conn)
    counts = {"draftable": len(touches), "created": 0}
    if dry_run:
        for t in touches:
            logger.info("gmail: would draft touch %s → %s (%s)",
                        t["touch_id"], t["contact_email"], t["company_name"])
        return counts

    if not touches:
        return counts
    svc = service()
    for t in touches:
        try:
            ids = create_draft(
                svc, to=t["contact_email"], subject=t["subject_line"],
                body=t["body_filled"], bcc=bcc_address(t["bcc_token"]))
            with db.connection() as conn:
                save_draft_state(conn, t["touch_id"], ids, body_hash(t["body_filled"]))
            counts["created"] += 1
            logger.info("gmail: drafted touch %s (draft %s, thread %s)",
                        t["touch_id"], ids["draft_id"], ids["thread_id"])
        except Exception:
            logger.exception("gmail: failed to draft touch %s", t["touch_id"])
    return counts


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List draftable touches; compose and send nothing "
                             "(makes no Gmail call).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    counts = run(dry_run=args.dry_run)
    verb = "would draft" if args.dry_run else "drafted"
    print(f"{counts['draftable']} draftable touch(es); {verb} {counts['created']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
