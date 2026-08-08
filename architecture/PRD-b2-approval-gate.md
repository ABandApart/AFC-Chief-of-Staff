# B2: Approval Gate (`#approvals`) — PRD & Build Spec

<doc:meta>
  <doc:phase>B2 (trust boundary) — foundation for all outbound</doc:phase>
  <doc:theme>The human "yes" gate: nothing world-affecting ships without an explicit approval</doc:theme>
  <doc:duration>~1–1.5 days</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>BUILT + live (2026-08-03). **AMENDED 2026-08-08 — see "Amendment 1: operator identity" below; requires a code change to the shipped cog.**</doc:status>
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

## Amendment 1: operator identity on the click (2026-08-08)

<amendment id="A1" name="Operator identity check" status="required — shipped cog needs this change">

**The gap.** The shipped gate guards *idempotency* (`UPDATE … WHERE
status='pending'`) but never guards *identity*. Any account that can see
`#approvals` can approve outbound email, publishing, and Drive writes. The
control today is guild invite hygiene, not code — and since B1, B3, and B4 all
funnel to this click, **a Discord account compromise defeats every other
boundary in the architecture at once.** This is the cheapest total bypass
available against the system.

**The fix — one guard, at the top of every button handler:**

```python
# agents/discord_bot/cogs/approvals.py
OPERATOR_ID = int(config.OPERATOR_DISCORD_ID)   # config, not hardcoded

async def _authorized(interaction) -> bool:
    if interaction.user.id == OPERATOR_ID:
        return True
    await interaction.response.send_message(
        "Not authorized.", ephemeral=True)
    log.warning("approval_denied user=%s row=%s",
                interaction.user.id, self.row_id)   # → #system
    return False
```

Called as the first line of the Approve, Reject, and Edit handlers. Three call
sites, one helper.

**Design notes that matter:**

- **Deny loudly, not silently.** An unauthorized click is a security event: log
  it to `#system`. Silent ignores make a real attempt indistinguishable from a
  UI glitch.
- **The check belongs in the handler, not the View construction.** Discord
  buttons are not access control — anyone who can see the message can invoke the
  interaction. Gating at render time is cosmetic.
- **`OPERATOR_DISCORD_ID` lives in config**, alongside the channel IDs. A list
  (`OPERATOR_IDS`) is fine if a second trusted account is ever needed; the point
  is that the allowlist is explicit and reviewable in git (B4).
- **Applies equally to Task Tinder and the four outreach card types**
  (`50-channel-layer.md`) — those write state rather than acting outbound, so
  the consequence is lower, but the guard is the same line and there is no reason
  to omit it.

**Typed confirmation for high-consequence item types.** For `item_type` values
whose handler contacts a third party — `email_send`, `content_publish`,
`drive_doc_create` — a bare button click is too cheap, particularly on mobile
where a mis-tap is a real failure mode. Those types open a **modal requiring the
operator to type a confirmation token** (the recipient's domain, or the literal
word `SEND`) before dispatch. Cheap to implement (the Edit modal already exists),
and it converts an accidental tap into a deliberate act.

**Not proposed:** approval policies, thresholds, or allow-lists that let anything
execute without a human — the original non-goal stands. This amendment narrows
*who* may say yes; it does not widen *what* may proceed without one.

**Architectural consequence, to be recorded in `25-target-state.md`:** 2FA on
the operator's Discord account is now a **stated architectural requirement**, not
personal hygiene. B2's integrity reduces to that account's integrity.

</amendment>

---

## Verification
Demo `noop_echo`: an agent calls `request_approval` → card appears in `#approvals`
→ Approve → the handler runs + confirms; Reject → nothing runs; restart the bot →
the pending card's buttons still work (persistent View re-attached).

**Amendment 1 verification:** a click from a non-operator account is refused
ephemerally, the row stays `pending`, nothing dispatches, and a `approval_denied`
line appears in `#system`. Pure test: `_authorized` returns False for a foreign
id and the state machine is never entered.
