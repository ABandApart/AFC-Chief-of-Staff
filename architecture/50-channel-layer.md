# Channel Layer

<doc:layer>implementation</doc:layer>
<doc:stability>medium — edit when channels or interaction patterns change</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 30-memory-layer.md, 40-action-layer.md</doc:depends_on>
<doc:referenced_by>60-content-pipeline.md, 70-build-order.md</doc:referenced_by>

## Purpose

This file defines the Discord bot, channel layout, interaction patterns (Task Tinder, approvals, capture), and the wire between Discord events and brain writes. The bot is a router; all logic lives in the action layer.

---

## Discord Server Layout

<server_layout>

A single Discord server (guild) with category-organized channels. All channels are private to the operator.

```
AI Adaptive COS (guild)
├── #briefing            Morning briefing posted by Briefing agent
├── #task-tinder         Candidate tasks with ✅/❌/⏰ buttons
├── #approvals           Content drafts with ✅/❌/✏️ buttons
├── #capture             You → bot; messages extracted to facts
├── #system              Health alerts, errors, rate-limit warnings
└── #archive             Old briefings, processed approvals (auto-moved)
```

**Why private to the operator**: This is operational infrastructure, not a community. There is no second user in v1. Sharing access to specific channels (e.g., briefing read-only for a family member) is a future RLS-gated feature.

</server_layout>

---

## Bot Architecture

<bot_architecture>

- **Library**: `discord.py` (Python). Rationale: stack consistency with all other agents; well-maintained; supports modern Discord features including buttons via `discord.ui.View`.
- **Process model**: Single long-running Python process under launchd `KeepAlive`. See `40-action-layer.md` for the plist.
- **State**: Stateless. Every restart is clean. All state in the brain. Discord message IDs are stored in `task_candidates.discord_message_id` and `approval_queue.discord_message_id` to bridge reaction events back to brain rows.
- **Permissions required**: Read Messages, Send Messages, Manage Messages (for editing posted messages), Embed Links, Add Reactions, Use External Emojis, Read Message History.
- **Intents**: `message_content` (for #capture), `reactions`, `guild_messages`.

</bot_architecture>

---

## Interaction Patterns

<interaction id="task_tinder">

### Task Tinder

**Trigger**: When `task_candidates` has new rows with `status = 'pending'`, the Discord bot posts them to `#task-tinder`. Triggered by:
- Briefing agent at 6am (batched: top 5 pending candidates)
- Discord bot polling every 15 minutes for high-confidence candidates (`confidence > 0.8`) to surface in near-real-time
- Manual trigger from a `/tasks pending` slash command

**Message format**:

```
**Task candidate** (confidence: 0.74)

> "I'll send you the draft positioning doc by Friday"

Source: Discovery call with Alex Mendez, 2026-05-13
Suggested action: Send Alex Mendez the positioning document

[ ✅ Accept ]  [ ❌ Decline ]  [ ⏰ Defer 24h ]
```

**Button handlers**:
- **✅ Accept**: Update `task_candidates.status = 'accepted'`. Insert into `tasks`. Insert into `follow_ups` with `owner = 'self'` and `escalation_level = 0`. If a deadline was extracted from evidence text, set `follow_ups.deadline`. Edit the Discord message to show "✅ Accepted at HH:MM" with buttons removed.
- **❌ Decline**: Update `task_candidates.status = 'declined'`. Edit message to "❌ Declined at HH:MM". No learning in v1 (per principle P6).
- **⏰ Defer 24h**: Update `task_candidates.status = 'deferred'` with `decided_at = now() + 24h`. Edit message to "⏰ Deferred to YYYY-MM-DD". A nightly job resets deferred candidates back to `pending` once `decided_at` passes.

**Acknowledgment timing**: Edit message within 2 seconds of button click. If brain write fails, edit message to indicate error and leave buttons active.

</interaction>

<interaction id="approvals">

### Approvals

**Trigger**: When `approval_queue` has new rows with `status = 'pending'`. Posted immediately by the agent that creates the approval (typically Sam after passing a draft).

**Message format** (for a content draft):

```
**Content draft for approval** — channel: LinkedIn

Source: "Why most AI adoption fails" (TechCrunch, 2026-05-13)
Sam evaluation: ✅ all criteria passed
Estimated post time: tomorrow 09:00 if approved now

—— DRAFT ——
[draft text, up to 1500 chars; full draft in thread if longer]
—— END ——

[ ✅ Approve ]  [ ❌ Reject ]  [ ✏️ Edit ]
```

**Button handlers**:
- **✅ Approve**: Update `approval_queue.status = 'approved'`, `decided_at = now()`. This triggers Keeley Distribution event-driven invocation. Edit message to "✅ Approved at HH:MM — scheduled to Buffer". On Buffer confirmation, edit again to show Buffer post URL.
- **❌ Reject**: Update `approval_queue.status = 'rejected'`. Edit message to "❌ Rejected at HH:MM". Content pipeline row marked `declined`. No re-draft in v1.
- **✏️ Edit**: Open a thread on the message. Operator types the edited version as a thread reply. Bot watches for the next message in the thread, captures it as `approval_queue.edit_notes`, and re-posts the message with the edited version and Approve/Reject buttons (no Edit again — one edit per draft in v1).

**Why no in-line modal edit**: Discord modals are limited in length and don't support rich text. Thread reply is simpler and more flexible.

</interaction>

<interaction id="capture">

### Capture

**Trigger**: Any message in `#capture`. The operator types whatever they want to remember.

**Bot behavior**:
1. React to the message with ⏳ to acknowledge receipt
2. Forward to fact extraction job (Claude Haiku) — extracts atomic claims, identifies domain, attaches source provenance (`source_type='discord'`, `source_ref=<message_id>`)
3. Each extracted fact written to `facts` table with embedding (Gemini text-embedding-004)
4. Replace ⏳ with ✅ once stored; reply in thread with a one-line summary of facts extracted

**Edge cases**:
- Empty message or message-with-only-link: bot replies "I need text. Send the link with a sentence about why it matters."
- Long message (>4000 chars): Claude Haiku may chunk the extraction. Bot still acknowledges with a single ✅.
- Message with attached file: v1 ignores attachments. Future support for image/PDF capture is a v2 feature.

</interaction>

<interaction id="briefing">

### Briefing

**Trigger**: Briefing agent at 6am daily.

**Bot role**: Receive a structured payload from the Briefing agent and post it as a Discord message. No interactive elements — briefing is read-only context.

**Format** (sections; use Discord markdown):

```
## Morning briefing — Thursday, May 14

### Priorities
- ⚠️ Overdue: Send Alex Mendez the positioning doc (committed 2026-05-08, 6 days ago)
- ⚠️ Approaching: Roundtable prep for May 20 (5 days out)

### New today (top reading)
- [Title 1](url) — why it matters in 1 sentence
- [Title 2](url) — why it matters in 1 sentence

### New facts captured
- 3 facts from yesterday's discovery call with Pat Kim
- 1 fact from #capture: Q3 newsletter theme decided

### Pending in Task Tinder
4 candidates — head to #task-tinder

### System
✅ All agents healthy. Last Tartt run: 5:02am, 47 items collected.
```

</interaction>

<interaction id="system">

### System

**Trigger**: Any agent writing an alert. Ted is the primary contributor; other agents emit errors here.

**Format**: Plain markdown messages. No interactive elements. Bot does not respond to messages in this channel.

**Pinned message**: A status summary updated by Ted every 6 hours, edited in place rather than reposted.

</interaction>

---

## Slash Commands (v1 minimal set)

<slash_commands>

| Command | Purpose | Response |
|---------|---------|----------|
| `/tasks pending` | Re-post pending task candidates to #task-tinder | Ephemeral confirmation, then public re-post |
| `/approvals pending` | List pending approvals with links | Ephemeral list |
| `/brain query <text>` | Hybrid search over facts | Ephemeral results, top 5 |
| `/follow-ups overdue` | List overdue follow-ups | Ephemeral list |
| `/health` | Show last successful run time per agent | Ephemeral status |

All slash commands respond ephemerally (visible only to the operator) unless they trigger a public action.

</slash_commands>

---

## Reaction Handling Robustness

<reaction_robustness>

Discord's reaction events have known reliability gotchas. Defensive patterns:

1. **Idempotency**: Every reaction handler reads the current state of the target row before writing. If the row is already in the target state, the handler exits silently. Prevents double-processing if Discord delivers the event twice.

2. **Race condition on rapid clicks**: If the operator clicks multiple buttons quickly, only the first one wins. Subsequent clicks find the row in a non-pending state and exit. Bot edits the message to remove buttons after the first action, but Discord may serve cached UI; the brain is the source of truth.

3. **Bot restart in-flight**: If the bot crashes mid-handler, the brain row stays in its prior state. On restart, the operator clicks again and the handler runs cleanly.

4. **Message age limit**: Discord doesn't deliver reactions on messages older than the bot's cache. Bot fetches old messages on startup for any `task_candidates` or `approval_queue` rows still pending. Prevents stale UI from being unresponsive after a restart.

</reaction_robustness>

---

## Bot Module Structure

<bot_module_structure>

```
~/agents/discord-bot/
├── run.py              # entry point, loads cogs, connects
├── config.py           # channel IDs, command prefix
├── brain.py            # Supabase client helpers (read/write)
├── cogs/
│   ├── task_tinder.py   # buttons + reactions for #task-tinder
│   ├── approvals.py     # buttons + thread handling for #approvals
│   ├── capture.py       # #capture listener + fact extraction trigger
│   ├── briefing.py      # endpoint that Briefing agent posts to
│   ├── system.py        # endpoint that other agents post alerts to
│   └── slash.py         # slash command implementations
└── requirements.txt
```

Each cog is self-contained. Adding a new interaction pattern (e.g., a meeting-prep digest) means adding a new cog without touching existing ones.

</bot_module_structure>

---

## Laptop Channel Access

<laptop_access>

The operator interacts with the brain from the laptop primarily through Claude Code sessions, not Discord (though Discord is reachable from the laptop too).

Claude Code session pattern:
- Session reads from Supabase using the anon key (RLS-scoped where applicable; v1 has no RLS)
- Writes for things the operator decides explicitly (a new decision, a new fact, a new person record) go through dedicated CLI helpers in `~/agents/cli/`
- High-privilege writes (modifying agent code, changing source trust scores) happen on the laptop only through Claude Code editing the git repo; deployment to the Mac mini is a `git pull` on the agent account

There is no "laptop Discord bot." The bot runs on the Mac mini and the laptop just connects to Discord as any client would.

</laptop_access>

---

## What This Channel Layer Does NOT Do

<non_goals>

- **Group communication**: One operator, no team channels.
- **Real-time conversation with an agent**: The bot does not maintain conversational threads. Each interaction is a request/response or a button click. For conversation, the operator uses Claude Code.
- **Mobile push beyond Discord**: No SMS, no email digests, no native notifications. Discord mobile is the mobile surface.
- **Voice or video**: Not in v1. Not planned.
- **Webhooks for external systems**: External services don't post directly to Discord. They go through the brain, and agents post on their behalf.

</non_goals>
