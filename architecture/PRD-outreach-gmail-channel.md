# Outreach Gmail Channel — Draft Composition + Send Capture — PRD & Build Spec

<doc:meta>
  <doc:phase>Track O — replaces/absorbs build step 11 (BCC send capture); depends on step 9 (packet assembly)</doc:phase>
  <doc:theme>The packet becomes a Gmail draft you edit and send; the send records itself</doc:theme>
  <doc:duration>~2 days (1 draft composition, 1 send capture), after packet assembly exists</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>DRAFT 2026-08-12 — operator decisions taken (Workspace/Internal, metadata-only scope, CI-enforced no-send). Not built. Three API behaviours must be verified against the live account before any code is written (§7).</doc:status>
  <doc:depends_on>`35-outreach-crm.md` §7 packet assembly (the `body_filled` this drafts from), §8 BCC token, migration 0013 `outreach_touches`</doc:depends_on>
  <doc:blocks>nothing — the Shortcut and NocoDB paths remain as fallbacks; LinkedIn sends never route through here</doc:blocks>
</doc:meta>

## TL;DR

The morning loop turns each due, **ready** packet into a **Gmail draft** in the
operator's own mailbox. The operator writes the observation sentence directly in
the draft and sends it from Gmail as normal. A poller then sees the send and
writes `sent_at` / `sent_via='gmail_api'` back to the touch, correlating on the
thread id. The **verbatim body comes from the dedicated BCC mailbox** (`35-` §8),
not from Gmail — deliberately, so the system never holds read access to the
operator's client mail.

Outreach still never generates prose and still never sends. What changes is that
the copy-paste step between the work surface and the mail client disappears.

## Goal & Non-Goals

**Goal:** the operator opens Gmail, finds a draft already addressed and filled
with the template's `auto` slots resolved, types the one sentence only they can
write, and hits send. Everything else — that it went, when, and what was said —
records itself.

**Non-goals:** the system does not send, does not write the observation, does not
read the operator's mailbox contents, and does not become the only send path
(Shortcut and NocoDB stay; LinkedIn is permanently manual per `35-` §8).

---

## 1. Operator decisions taken (2026-08-12)

<decisions>

| # | Decision | Consequence |
|---|----------|-------------|
| **G1** | **Google Workspace on the operator's own domain**, OAuth app user type **Internal** | No Google verification / CASA assessment, and **non-expiring refresh tokens**. A personal `@gmail.com` would have forced either weekly manual re-auth (Testing-mode tokens expire in 7 days) or a slow, costly restricted-scope review — this is the difference between an unattended loop and a chore. |
| **G2** | **`gmail.metadata`, not `gmail.readonly`** | The agent can see message *headers* — enough to detect a send and match it to a touch — and **cannot read any message body in the operator's mailbox**. Client-confidential mail is out of reach by scope, not by policy. Cost: `sent_body` cannot come from Gmail (see §3). |
| **G3** | **Accept the send *capability*, forbid the send *call*, enforce it in CI** | There is no draft-only Gmail scope — `gmail.compose` grants send. The token is therefore send-capable. A build-failing grep (the `test_no_raw_retrieval.py` pattern) makes "this system never sends mail" a property of the repo rather than an intention. See §5. |

</decisions>

---

## 2. What the Gmail API actually provides

<api_capabilities>

| Need | Call | Scope |
|------|------|-------|
| Create the draft | `users.drafts.create` (MIME in `message.raw`) | `gmail.compose` |
| Refresh an untouched draft | `users.drafts.update` | `gmail.compose` |
| Read back our own draft (edit detection) | `users.drafts.get` | `gmail.compose` |
| Detect a send | `users.history.list` from a stored `historyId` → `messagesAdded` carrying the `SENT` label | `gmail.metadata` |
| Correlate + timestamp | `users.messages.get?format=metadata` → headers + `internalDate` | `gmail.metadata` |

Polling `history.list` on the existing 15-minute cadence avoids Cloud Pub/Sub
entirely — no push endpoint, no `users.watch` renewal every 7 days. Quota is
irrelevant at 12–15 live targets (`drafts.create` is 10 units against a
1.2M/day allowance).

</api_capabilities>

---

## 3. Where each field comes from

<field_sources>

This is the part G2 changes, and it is why the Gmail channel **complements** the
BCC design rather than replacing it.

| Field | Source | Why |
|-------|--------|-----|
| `sent_at` | Gmail `internalDate` on the SENT message | The real send time, not our poll time. |
| `sent_via` | `'gmail_api'` | New value alongside `bcc` / `shortcut` / `nocodb`. |
| `sent_body` | **The dedicated BCC mailbox, via IMAP** (`35-` §8) | Metadata scope cannot read bodies. The BCC mailbox is a *separate* account that receives nothing but outreach sends, so reading it in full has **no blast radius into client mail** — which is exactly the property G2 is protecting. |

> **This resolves `35-` §16 open decision #2 in favour of a dedicated mailbox,
> not Workspace plus-addressing.** Plus-addressing on the operator's own mailbox
> would land the BCCs in the mailbox we just spent G2 refusing to read. A
> separate account keeps both channels narrow.

**If the BCC is forgotten on a send**, Gmail still records `sent_at`/`sent_via`
and the touch is correctly marked sent — only `sent_body` is missing. Under the
BCC-only design that same slip loses the send record entirely. Two independent
signals is a robustness gain, not redundancy.

</field_sources>

---

## 4. Correlating a sent message to a touch

<correlation>

Ranked by how much the design should lean on each. **All three are cheap to
carry, so carry all three** and treat disagreement as an alert, not a coin flip.

1. **`threadId`** — returned by `drafts.create`, stable through send. The primary
   key. Stored as `outreach_touches.gmail_thread_id`.
2. **A custom header** — `X-AIA-Touch: <bcc_token>` set in the draft's raw MIME.
   Reuses the token the BCC matcher already keys on, so both channels agree by
   construction. **Verify it survives draft→send before writing code** (§7) —
   the same discipline `35-` §8 already demands of the BCC token.
3. **Message id** — recorded (`gmail_message_id`) but **not** relied on for
   matching. The draft's message id is *expected* to carry into SENT, but the
   RFC822 `Message-ID` header is known to be regenerated on send, and the
   distinction is not worth a silent mis-attribution.

**Why this matters more than it looks:** touch-of-first-reply is one of the
method's key metrics (`35-` §8, R10), and it is computed from which touch was
sent when. A matcher that is right most of the time corrupts that metric quietly,
which is precisely the failure `35-` refuses for the BCC path ("token-exact,
because heuristic matching corrupts touch-of-first-reply silently"). Same bar
here.

</correlation>

---

## 5. Trust boundaries

<trust>

**B2 — the honest version.** Before this, outreach could not cross B2 because the
system held no mail credential; "never sends" was structural. With
`gmail.compose` the capability exists. Per **G3** the mitigation is threefold:

1. **No code path calls it.** Nothing in the repo invokes `drafts.send` or
   `messages.send`.
2. **CI enforces it.** `tests/test_no_outbound_send.py` — a build-failing grep
   for `drafts().send`, `messages().send`, and `.send(userId=` anywhere under
   `agents/` and `cli/`, modelled on `test_no_raw_retrieval.py`. Including in
   docstrings and comments, per that test's precedent.
3. **`35-` §13 gains an amendment** recording that the capability is present, the
   call is forbidden, and any future system-initiated send is a **new B2 crossing
   requiring `#approvals`** — not an incremental use of a token we already hold.

**B1 — unchanged and worth stating.** A draft is assembled from the template plus
typed evidence. No ingested or scraped text is interpreted as an instruction, and
nothing generated goes into it. The residual R20 risk (hostile text in a job
posting, displayed and copied by a human) is unchanged by this channel — the
500-char excerpt cap and unicode hardening (H1/H2) still apply upstream.

**New: the draft itself is a pre-send artifact in the operator's mailbox.** It is
addressed to a third party and one click from going out. That is why §6's
readiness gate is a *precondition of creation*, not a check at send time.

</trust>

---

## 6. The readiness gate gets stronger, and the send guard gets weaker

<readiness>

This channel improves R1 (an unresolved `[operator]` placeholder reaching a
prospect) and forces a correction to how the `ready` flag is enforced.

**Prevention moves earlier.** Today `ready = false` blocks *marking* a touch
sent. That is a lagging control: by the time it fires the email has already gone.
With drafts, the control moves to **not creating the artifact at all** — no
draft, nothing to send, and the operator never sees a half-filled message they
might fire off. `outreach-daily` creates drafts **only** for packets with
`ready = true`.

**Recording must therefore stop refusing.** The `outreach_touch_ready_guard()`
trigger from migration 0013 raises when a not-ready touch is marked sent. That is
right for the pre-send paths (a Shortcut or NocoDB "mark as sent"), but wrong for
an *observed* send: the mail is already in the prospect's inbox, and refusing the
write does not un-send it — it just loses the record and leaves the arc thinking
the touch is still due.

**Rule:** capture paths (`sent_via IN ('gmail_api','bcc')` ) record
unconditionally and raise a **Ted alert**; assertion paths
(`'shortcut'`,`'nocodb'`) stay blocked by the guard. Implemented by making the
trigger exempt the capture values, so the invariant remains in the database
rather than migrating into application code.

**Open sub-question:** what happens when a packet goes not-ready *after* its
draft exists — evidence went stale overnight, say. Options: delete the draft
(destructive if the operator has started editing), leave it and alert, or update
it in place with a visible warning line. Recommend **leave it and alert**;
deleting a human's half-written work to satisfy a freshness rule is the wrong
trade. To be settled at build.

</readiness>

---

## 7. Verify before writing code

<verification>

`35-` §8's instruction — *"verify on day one that the token survives in a header
before writing any code"* — applies here three times over. Each of these is a
~15-minute manual check against the real Workspace account, and each one being
wrong changes the design:

| # | Check | If it fails |
|---|-------|-------------|
| **V1** | Does a custom `X-AIA-Touch` header set in `drafts.create` survive into the SENT message? | Fall back to `threadId` alone as the correlation key; the BCC token still corroborates. |
| **V2** | Does `gmail.metadata` scope permit `history.list` **and** return custom `X-` headers via `messages.get?format=metadata`? | If metadata cannot see `X-` headers, correlation is `threadId`-only. If it cannot call `history.list` at all, the channel needs `gmail.readonly` — which **reopens G2** and should come back to the operator, not be quietly upgraded. |
| **V3** | Is the draft's `message.id` preserved through send? | Already assumed unreliable (§4); confirming it either way just tells us whether to keep storing it. |

Do these as a throwaway script against the live account before the build, and
record the actual output in the implementation's docstring — the same discipline
used for the Track I stdio probe and the P4-1 vector-layout probe.

</verification>

---

## 8. What we build

<build>

| Step | Scope | Effort |
|------|-------|--------|
| 1 | **V1–V3 probes** against the live account (§7) | 0.5 day |
| 2 | **Migration 0014** — `outreach_touches` gains `gmail_draft_id`, `gmail_thread_id`, `gmail_message_id`, `draft_created_at`, `draft_body_hash`; `gmail_channel_state` (singleton `history_id` watermark, mirroring `channel_state` from 0007); ready-guard trigger amended per §6 | 0.5 day |
| 3 | **OAuth setup** — Internal app in the Workspace project, installed-app flow, refresh token → keychain as `gmail-oauth-client` + `gmail-refresh-token` (barry-agent; a **human step**, like every other keychain write) | 0.5 day |
| 4 | **`agents/outreach/gmail.py`** — draft composition from `outreach_packets.body_filled`, idempotent on `gmail_draft_id`, `drafts.update` only when `draft_body_hash` shows the operator hasn't started editing | 0.5 day |
| 5 | **Send capture** — `history.list` poller, correlation, `sent_at`/`sent_via` write, Ted alert on a not-ready capture | 0.5 day |
| 6 | **`tests/test_no_outbound_send.py`** — the CI send-guard (G3) | 0.25 day |
| 7 | Doc amendments: `35-` §13 b2_rule + §9 surfaces, `40-action-layer.md` credential inventory + Outreach_loops, `50-channel-layer.md` (Gmail as a non-Discord surface alongside NocoDB) | 0.25 day |

**Sequencing:** this depends on packet assembly (Track O increment 2) — there is
no `body_filled` to draft from until that exists. It replaces the BCC-only
version of increment 4 but does **not** remove the BCC mailbox, which now
supplies `sent_body` (§3).

</build>

---

## 9. Risks

<risks>

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| **G-R1** | The system holds a send-capable token; a future bug or careless change sends mail unattended | **High** | G3: no call site, CI grep, `35-` §13 amendment naming any system-initiated send as a fresh B2 crossing |
| **G-R2** | Mis-correlation attributes a send to the wrong touch, silently corrupting touch-of-first-reply | **Medium** | Three independent keys (§4); disagreement alerts rather than picking |
| **G-R3** | A draft is created for a packet that later goes stale, and is sent with a now-false claim | **Medium** | Creation gated on `ready` (§6); staleness alert on existing drafts; the §6 open sub-question |
| **G-R4** | Duplicate drafts accumulate — `outreach-daily` regenerates packets every morning | Low | Idempotent on `gmail_draft_id`; update-in-place only when unedited |
| **G-R5** | Refresh token revoked (password change, admin action) and the loop silently stops drafting | Low | Ted alert on auth failure; the loop is visible in the morning briefing line either way |
| **G-R6** | Scope creep back to `gmail.readonly` for convenience | Medium | G2 is an operator decision, not an implementation detail — V2 failing means **returning to the operator**, per §7 |

</risks>
