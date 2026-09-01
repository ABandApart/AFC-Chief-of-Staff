---
name: outreach-gmail-capture
schedule: "*/15 * * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.outreach.gmail_capture
description: Detect sent touches via Gmail history + BCC, mark them sent, capture the body (Track O send half, §8).
---

# Outreach send capture (Track O send half)

Runs `agents/outreach/gmail_capture.py` **every 15 minutes**. Two capture paths,
both idempotent, correlating a sent message to its touch by the keys the V1–V3
probe left standing (threadId + the BCC token — the custom header does not survive
send):

1. **Gmail `history.list`** (`gmail.metadata` scope, G2 — headers only, no bodies):
   finds newly SENT messages and marks the matching touch `sent_at` /
   `sent_via='gmail_api'` / `gmail_message_id`. This **advances the arc** — a touch
   marked sent lets the sequence progress and the next touch schedule.
2. **BCC mailbox over IMAP** (`bcc@aiadaptive.co`, a separate inbox): reads the
   verbatim BCC copy and writes `sent_body` for the brain (§3), setting
   `sent_via='bcc'` when the history path hasn't already claimed the send. Skips
   gracefully when the `gmail-bcc-imap` credential is absent.

**Not-ready captures record anyway (§6).** An observed send has already gone;
refusing the write would only lose the record. The `outreach_touch_ready_guard()`
(migration 0025) exempts the capture paths, and a not-ready capture is surfaced as
a Ted alert (a marked `WARNING` until Ted's scanner lands, `35-` §14).

**Runs where the token lives (barry-agent).** Needs the `gmail` group and the
keychain items `gmail-oauth-client` / `gmail-refresh-token` (history), plus
`gmail-bcc-imap` (the `bcc@` IMAP app password) for the body path. No LLM, no spend.

**Ships DISABLED.** Enable once drafting is live and a send has been confirmed to
capture end to end. The first run bootstraps the history watermark and captures
nothing (there is no history before "now"). Run by hand:

```
uv run python -m agents.outreach.gmail_capture
```

Trust: reads Gmail headers + the dedicated BCC inbox, writes only `sent_*` columns
on `outreach_touches` and the `gmail_channel_state` watermark. It sends nothing.
