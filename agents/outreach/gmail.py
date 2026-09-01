"""Gmail drafting — compose outreach drafts in the operator's mailbox (Track O).

`PRD-outreach-gmail-channel.md` §8 step 4. For each due touch this creates a Gmail
**draft** (never a send — G3, enforced by `tests/test_no_outbound_send.py`) from
the assembled packet, addressed to the target's contact and **BCC'd to the
dedicated `bcc+<token>@aiadaptive.co` mailbox** so the send-capture poller can
later match the sent message to this touch by threadId + that token (the V1-probe
fallback: the custom header does not survive draft→sent).

**Idempotent (G-R4), and keeps drafts current (§6).** `outreach-daily` regenerates
packets every morning. A touch with no draft gets one (create-once — never a
duplicate). A touch that already has a draft is **refreshed to the latest packet
only while the operator has not edited it**: `draft_body_hash` stores the draft as
Gmail held it after our last write, so a re-read that still matches is ours to
update, and one that differs is the operator's edit — left untouched, never
overwritten. A draft the operator deleted is not resurrected.

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


def update_draft(svc: Any, draft_id: str, *, to: str, subject: str,
                 body: str, bcc: str) -> None:
    """Rewrite an existing draft in place (keeps the same draft id + thread)."""
    raw = build_raw(to=to, subject=subject, body=body, bcc=bcc)
    svc.users().drafts().update(
        userId="me", id=draft_id, body={"message": {"raw": raw}}).execute()


def extract_body(payload: dict) -> str:
    """The plain-text body from a Gmail message payload (`drafts.get?format=full`).
    Walks parts for the first text/plain leaf; empty if none."""
    def walk(part: dict) -> str | None:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        for child in part.get("parts") or []:
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(payload) or ""


def roundtrip_hash(svc: Any, draft_id: str) -> str:
    """Hash the draft's body AS GMAIL STORED IT — read back after we write. This is
    what makes edit-detection reliable: comparing `hash(body_filled)` to a fetched
    draft would mismatch on Gmail's own re-encoding even when untouched, so we
    always compare 'what Gmail holds now' to 'what Gmail held after our last write'."""
    draft = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    return body_hash(extract_body(draft.get("message", {}).get("payload", {})))


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


def update_draft_hash(conn: object, touch_id: int, hash_: str) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("UPDATE outreach_touches SET draft_body_hash = %s WHERE id = %s",
                    (hash_, touch_id))


# Existing drafts on active, unsent touches — candidates for a keep-current refresh.
_EXISTING_SQL = _DRAFTABLE_SQL.replace(
    "t.gmail_draft_id IS NULL          -- create-once: not yet drafted",
    "t.gmail_draft_id IS NOT NULL      -- already drafted: refresh-if-unedited",
).replace(
    "SELECT t.id AS touch_id, t.bcc_token, tg.contact_email, tg.company_name,",
    "SELECT t.id AS touch_id, t.bcc_token, t.gmail_draft_id, t.draft_body_hash, "
    "tg.contact_email, tg.company_name,",
)


def list_existing_drafts(conn: object) -> list[dict[str, Any]]:
    """Touches that already have a draft and are still active — checked each run to
    keep the draft current with the latest packet UNLESS the operator has edited it."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_EXISTING_SQL)
        return cur.fetchall()


# --- orchestration -----------------------------------------------------------

def refresh_draft(svc: Any, touch: dict) -> tuple[str, str | None]:
    """Keep one existing draft current (§6 update-in-place). Compares the draft as
    Gmail holds it now to `draft_body_hash` (what it held after our last write):
    a MISMATCH means the operator has edited it — leave their work untouched; a
    MATCH means it is still ours to refresh to the latest packet body. Returns
    `(action, new_hash)` — action ∈ {edited, refreshed, gone}."""
    draft_id = touch["gmail_draft_id"]
    try:
        current = roundtrip_hash(svc, draft_id)
    except Exception as exc:  # noqa: BLE001 — duck-typed Gmail HttpError
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            return ("gone", None)   # operator deleted it — do not resurrect
        raise
    if current != touch["draft_body_hash"]:
        return ("edited", None)
    update_draft(svc, draft_id, to=touch["contact_email"], subject=touch["subject_line"],
                 body=touch["body_filled"], bcc=bcc_address(touch["bcc_token"]))
    return ("refreshed", roundtrip_hash(svc, draft_id))

def run(*, dry_run: bool = False) -> dict[str, int]:
    """One drafting pass: create a draft for each draftable touch. Returns counts.
    In `dry_run` nothing is composed or written — it only reports what it would do
    (and makes no Gmail call), so it is safe on the build box without the token."""
    with db.connection() as conn:
        to_create = list_draftable(conn)
        to_refresh = list_existing_drafts(conn)
    counts = {"draftable": len(to_create), "created": 0,
              "existing": len(to_refresh), "refreshed": 0, "operator_edited": 0}
    if dry_run:
        for t in to_create:
            logger.info("gmail: would draft touch %s → %s (%s)",
                        t["touch_id"], t["contact_email"], t["company_name"])
        for t in to_refresh:
            logger.info("gmail: would check draft for touch %s (refresh if unedited)",
                        t["touch_id"])
        return counts

    if not (to_create or to_refresh):
        return counts
    svc = service()

    for t in to_create:
        try:
            ids = create_draft(
                svc, to=t["contact_email"], subject=t["subject_line"],
                body=t["body_filled"], bcc=bcc_address(t["bcc_token"]))
            # Stamp the round-trip hash (as Gmail stored it) so a later refresh can
            # tell our content from the operator's edits.
            with db.connection() as conn:
                save_draft_state(conn, t["touch_id"], ids,
                                 roundtrip_hash(svc, ids["draft_id"]))
            counts["created"] += 1
            logger.info("gmail: drafted touch %s (draft %s, thread %s)",
                        t["touch_id"], ids["draft_id"], ids["thread_id"])
        except Exception:
            logger.exception("gmail: failed to draft touch %s", t["touch_id"])

    for t in to_refresh:
        try:
            action, new_hash = refresh_draft(svc, t)
            if action == "refreshed":
                with db.connection() as conn:
                    update_draft_hash(conn, t["touch_id"], new_hash)
                counts["refreshed"] += 1
                logger.info("gmail: refreshed draft for touch %s (was unedited)",
                            t["touch_id"])
            elif action == "edited":
                counts["operator_edited"] += 1
                logger.info("gmail: left draft for touch %s (operator has edited it)",
                            t["touch_id"])
            else:  # gone
                logger.info("gmail: draft for touch %s is gone (deleted) — left as is",
                            t["touch_id"])
        except Exception:
            logger.exception("gmail: failed to refresh draft for touch %s", t["touch_id"])
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
    print(f"drafting: {counts['created']} created, {counts['refreshed']} refreshed, "
          f"{counts['operator_edited']} left as-edited "
          f"({counts['draftable']} draftable, {counts['existing']} existing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
