---
name: outreach-gmail-draft
schedule: "0 6 * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.outreach.gmail
description: Compose Gmail drafts for due touches from assembled packets (Track O send half, §8).
---

# Outreach Gmail drafting (Track O send half)

Runs `agents/outreach/gmail.py` at **06:00**, after `outreach-daily` (05:45)
assembles the day's packets — there is no `body_filled` to draft from until it
has. For each in-sequence touch with a packet, a contact, and no draft yet, it
creates ONE Gmail **draft** in the operator's mailbox, addressed to the contact
and BCC'd to `bcc+<token>@aiadaptive.co`.

**It never sends.** `gmail.compose` is send-capable, but there is no send call and
`tests/test_no_outbound_send.py` fails the build if one ever appears (G3). The
operator reads, edits and sends each draft by hand; `outreach-gmail-capture` then
records the send.

**Create-once (G-R4).** A touch that already carries a `gmail_draft_id` is skipped,
so a nightly re-draft never piles up duplicates. Drafts are created regardless of
`ready` — an unresolved `[operator]` slot is shown, not withheld (§6); the
`outreach_touch_ready_guard()` keeps a not-ready *assertion* off the wire.

**Runs where the token lives (barry-agent).** Needs the `gmail` dependency group
(`uv sync --group gmail`) and the keychain items `gmail-oauth-client` /
`gmail-refresh-token`. No LLM, no `agent_runs`, no spend — Gmail API only.

**Ships DISABLED.** Enable (`enabled: true`) once the OAuth token is in the keychain
and one draft has been confirmed end to end. Run by hand any time:

```
uv run python -m agents.outreach.gmail --dry-run   # lists draftable touches, writes nothing
uv run python -m agents.outreach.gmail             # creates the drafts
```

Trust: reads `outreach_packets`/`outreach_targets`, writes drafts to the operator's
own mailbox and `gmail_*` columns on `outreach_touches`. It contacts no prospect and
proposes nothing to a third party — a draft in your own mailbox is not a B2 crossing.
