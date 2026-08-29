"""Gmail send-half V1–V3 probes — throwaway, run ONCE against the live Workspace.

`PRD-outreach-gmail-channel.md` §7 makes this the mandated step 1: three ~15-minute
checks against the real account, each of which changes the design if it fails, run
BEFORE the correlation/capture code is written. This lives in `agents/test/`
(diagnostic-probe home, not collected by pytest, excluded by the send-guard) and is
meant to be deleted after its output is recorded in `agents/outreach/gmail.py`'s
docstring — the same discipline as the Track I stdio probe.

It creates a draft and READS headers; it never sends. **You (the operator) send the
one probe draft by hand** from Gmail — so this stays within the "system never
sends" property (G3) even while probing.

PREREQUISITES (barry-agent, the OAuth setup step):
  * `uv add google-api-python-client google-auth` (deps not in pyproject yet).
  * An **Internal** OAuth app in the Workspace Google Cloud project, scopes
    `https://www.googleapis.com/auth/gmail.compose` and
    `https://www.googleapis.com/auth/gmail.metadata`.
  * Run the installed-app flow once; store in the login keychain:
      security add-generic-password -a "$USER" -s gmail-oauth-client  -w
        # value = JSON {"client_id": "...", "client_secret": "..."}
      security add-generic-password -a "$USER" -s gmail-refresh-token -w
        # value = the refresh token
  * Sending identity `barry@aiadaptive.co`; the BCC mailbox is the SEPARATE user
    `bcc@aiadaptive.co` (35- §8; provisioned 2026-08-29) — not needed for this
    probe, which is about header/scope behaviour, not BCC delivery.

RUN:
    uv run python -m agents.test.gmail_probe
Then, when prompted, open Gmail as barry@aiadaptive.co, send the draft titled
"[AIA PROBE] ignore", and press Enter. Paste the printed V1/V2/V3 block back.
"""

from __future__ import annotations

import base64
import json
from email.message import EmailMessage

from agents._lib import creds

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.metadata",
]
TOUCH_HEADER = "X-AIA-Touch"
PROBE_TOKEN = "probe-0001"


def _service():
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


def _raw_draft() -> str:
    msg = EmailMessage()
    msg["To"] = "barry@aiadaptive.co"       # send it to yourself; this is a probe
    msg["Subject"] = "[AIA PROBE] ignore"
    msg[TOUCH_HEADER] = PROBE_TOKEN         # V1: does this survive draft -> sent?
    msg.set_content("Probe draft. Send me by hand, then delete.")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def main() -> int:
    svc = _service()
    me = "me"

    # Watermark BEFORE creating/sending, so history.list has a start point (V2).
    profile = svc.users().getProfile(userId=me).execute()
    start_history_id = profile["historyId"]

    draft = svc.users().drafts().create(
        userId=me, body={"message": {"raw": _raw_draft()}}).execute()
    draft_id = draft["id"]
    draft_msg_id = draft["message"]["id"]
    thread_id = draft["message"].get("threadId")
    print(f"\nDraft created: draft_id={draft_id} message_id={draft_msg_id} "
          f"thread_id={thread_id}")
    print("→ Open Gmail as barry@aiadaptive.co, SEND the '[AIA PROBE] ignore' "
          "draft, then press Enter here.")
    input()

    # V2a: can gmail.metadata call history.list at all?
    v2_history = "PASS"
    sent_id = None
    try:
        hist = svc.users().history().list(
            userId=me, startHistoryId=start_history_id).execute()
        for h in hist.get("history", []):
            for m in h.get("messagesAdded", []):
                mid = m["message"]["id"]
                labels = m["message"].get("labelIds", [])
                if "SENT" in labels:
                    sent_id = mid
    except Exception as exc:  # noqa: BLE001
        v2_history = f"FAIL — history.list denied: {exc!r}"

    # Fallback: find the sent message by query if history didn't surface it.
    if sent_id is None:
        res = svc.users().messages().list(
            userId=me, q="subject:[AIA PROBE] in:sent").execute()
        msgs = res.get("messages", [])
        sent_id = msgs[0]["id"] if msgs else None

    # V1 + V2b: does metadata format return the custom X- header on the SENT msg?
    v1 = v2_header = "UNKNOWN"
    v3 = "UNKNOWN"
    if sent_id:
        meta = svc.users().messages().get(
            userId=me, id=sent_id, format="metadata",
            metadataHeaders=[TOUCH_HEADER, "Subject"]).execute()
        headers = {h["name"]: h["value"]
                   for h in meta.get("payload", {}).get("headers", [])}
        got = headers.get(TOUCH_HEADER)
        v1 = f"PASS — {TOUCH_HEADER}={got!r} survived" if got == PROBE_TOKEN \
            else f"FAIL — {TOUCH_HEADER} not on sent msg (headers seen: {list(headers)})"
        v2_header = "PASS — metadata format returns custom X- headers" if got \
            else "FAIL — metadata format did not return the custom X- header"
        v3 = f"draft message_id {'==' if sent_id == draft_msg_id else '!='} sent id " \
             f"(draft={draft_msg_id}, sent={sent_id})"
    else:
        v1 = v2_header = "BLOCKED — could not locate the sent message"

    print("\n================ V1–V3 RESULTS (paste this back) ================")
    print(f"V1 (X-AIA-Touch survives draft→sent): {v1}")
    print(f"V2 (metadata scope allows history.list): {v2_history}")
    print(f"V2 (metadata scope returns X- headers):  {v2_header}")
    print(f"V3 (draft message.id preserved on send): {v3}")
    print("=================================================================")
    print("\nCleanup: delete the '[AIA PROBE] ignore' message from Sent, and "
          "delete this file once the results are recorded in gmail.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
