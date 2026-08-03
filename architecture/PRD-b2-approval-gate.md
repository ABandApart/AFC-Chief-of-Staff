# B2: Approval Gate (`#approvals`) — PRD & Build Spec

<doc:meta>
  <doc:phase>B2 (trust boundary) — foundation for all outbound</doc:phase>
  <doc:theme>The human "yes" gate: nothing world-affecting ships without an explicit approval</doc:theme>
  <doc:duration>~1–1.5 days</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>drafted — build next (with B3), before Phase 4 outbound work</doc:status>
  <doc:depends_on>3.1 bot skeleton; `approval_queue` table (migration 0001); APPROVALS_CHANNEL_ID (config)</doc:depends_on>
  <doc:blocks>Phase 8/9 (content pipeline → publish), Drive doc output, email replies, any agent outbound</doc:blocks>
</doc:meta>

## TL;DR

A reusable Discord approval cog. Any agent that wants to take a **world-affecting
action** (publish content, create a Drive doc, send an email, post to Buffer)
does not act directly — it **enqueues an approval request**, the cog posts it to
`#approvals` with Approve / Reject / Edit buttons, and the action executes **only**
after a human clicks Approve. This is trust boundary **B2** (`25-target-state.md`)
made concrete, and it gates every outbound feature that follows.

## Goal & Non-Goals

**Goal:** a generic `approval_queue`-backed cog + a small dispatch API that any
agent can call — `request_approval(item_type, payload, summary)` → human decides
in Discord → on Approve, a registered handler for `item_type` runs.

**Non-goals:** no auto-approval ever; no approval *policies* (thresholds,
allow-lists) in v1 — every item is a manual click; not a general workflow engine.

## Design

### Data (exists — migration 0001, `approval_queue`)
`id, item_type, item_ref_id, payload JSONB, discord_message_id, status
('pending'|'approved'|'rejected'|'edited'), posted_at, decided_at, edit_notes`.
No schema change needed.

### The dispatch pattern (the core decision)
Decouple **requesting** an action from **executing** it, via a handler registry
keyed by `item_type`:

- `agents/_lib/approvals.py`:
  - `request_approval(*, item_type, payload, summary, ref_id=None) -> int` —
    inserts a `pending` row, returns its id. (The cog picks it up to post.)
  - `HANDLERS: dict[str, Callable]` + `register_handler(item_type, fn)` — an agent
    registers how its `item_type` is executed on approve. The handler receives the
    (possibly edited) payload and performs the outbound act.
- `agents/discord_bot/cogs/approvals.py`:
  - On startup, posts any `pending` rows not yet in Discord and **re-attaches
    persistent Views** (discord.py `View(timeout=None)` + stable `custom_id`s keyed
    by row id) so buttons survive bot restarts.
  - **Approve** → `status='approved'`, `decided_at=now()`, then dispatch
    `HANDLERS[item_type](payload)`; report success/failure back in-thread.
  - **Reject** → `status='rejected'`; nothing executes.
  - **Edit** → opens a modal to amend the payload/text → `status='edited'` +
    `edit_notes`, then dispatch with the edited payload.
- Idempotency: the click handler guards on `status='pending'` (a second click on a
  decided row is a no-op) — one action, one execution.

### Trust-boundary rules baked in
- The **human click is the only authority**; no code path executes an
  `item_type` handler without a row transitioning to approved/edited via Discord.
- Ingested/untrusted content can *populate a payload* but can never *approve* it
  (B1 stays upstream; B2 is the exit gate).
- Handlers are the only place an outbound side-effect lives; agents never act
  outside a handler.

## Build outline

1. `approvals.py` lib: `request_approval`, the handler registry, the DB helpers.
2. `cogs/approvals.py`: persistent-View posting, the three buttons, the edit modal,
   dispatch-on-approve, startup re-attach.
3. A trivial demo handler (`item_type='noop_echo'`) to smoke the full loop before
   any real outbound exists.
4. Tests (pure): the state-transition guard (pending→approved/rejected, double-click
   no-op), payload edit merge, handler-registry lookup/missing-handler error.

## Open decisions (recommend, confirm at build)

- **Buttons vs reactions:** buttons + a persistent View (recommended — cleaner
  than reaction handlers, and `#task-tinder`/content already assume buttons in
  `50-channel-layer.md`).
- **Edit in v1?** Yes, minimal (a modal that replaces the draft text) — cheap and
  high-value for content.
- **Handler execution model:** synchronous dispatch on click (recommended for v1;
  a slow handler runs in a thread and reports back in-thread).

## Verification
Demo `noop_echo`: an agent calls `request_approval` → card appears in `#approvals`
→ Approve → the handler runs + confirms; Reject → nothing runs; restart the bot →
the pending card's buttons still work (persistent View re-attached).
