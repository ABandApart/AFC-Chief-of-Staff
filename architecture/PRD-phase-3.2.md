# Phase 3.2: Capture Flow — PRD & Build Instructions

<doc:meta>
  <doc:phase>3.2</doc:phase>
  <doc:parent_phase>3 — Capture and Recall</doc:parent_phase>
  <doc:theme>#capture → facts (first production LLM use)</doc:theme>
  <doc:duration>~3 hours of work + ~$0.02 smoke spend</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>ready to execute</doc:status>
  <doc:depends_on>3.1 (bot skeleton `2e86312`), Phase 2 cost helper</doc:depends_on>
  <doc:blocks>3.3 (recall — reads the facts this writes)</doc:blocks>
</doc:meta>

## TL;DR

The bot listens to `#capture`. When you post a thought, it reacts ⏳,
extracts atomic facts via Claude Haiku (through the cost helper), embeds each
fact via Gemini, writes them to the `facts` table, then swaps ⏳ for ✅ and
replies with a one-line summary. This is the **first production LLM usage** —
every call goes through `agent_run("fact-extraction", ...)`.

---

## Goal & Non-Goals

<goals>

**Goal**: Post a message in `#capture` → within a few seconds it becomes one
or more rows in `facts` (with embeddings), acknowledged by ⏳→✅ and a reply.

</goals>

<non_goals>

- **Recall / search** — 3.3 (`cli/recall.py` reads what 3.2 writes)
- **`/outcome` slash command** — 3.4
- **Briefing / launchd** — 3.5
- **Task extraction** ("I'll do X" → task_candidates) — Phase 5
- **Attachment / image / PDF capture** — v2 (ignore attachments)
- **Structured-output helper in the cost helper** — 3.2 parses JSON from a
  Haiku text response defensively; a first-class `call_anthropic_structured`
  is a later enhancement
- **Re-embedding / fact dedup / expiry** — facts are append-only in v1

</non_goals>

---

## Acceptance Criteria

<acceptance>

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | Posting in `#capture` reacts ⏳ then ✅ | Visual in Discord |
| AC2 | A captured thought produces ≥1 `facts` row with `source_type='discord'` and `source_ref=<message_id>` | `SELECT * FROM facts WHERE source_type='discord' ORDER BY created_at DESC` |
| AC3 | Each fact row has a non-null 768-dim `embedding` | `SELECT id, (embedding IS NOT NULL) FROM facts ...` |
| AC4 | Extraction + embedding each write an `agent_runs` row for `fact-extraction` | `SELECT * FROM agent_runs WHERE agent_name='fact-extraction'` shows anthropic + gemini rows |
| AC5 | Empty / link-only message gets a helpful reply, no facts written | Post just a URL → bot replies asking for text; no new facts row |
| AC6 | Bot replies in-thread with a one-line summary ("Captured N facts: ...") | Visual in Discord |
| AC7 | `anthropic-key-fact-extraction` registered in `KEY_BY_AGENT` and present in barry-agent keychain | code + `keychain_verify` |
| AC8 | Unit test for fact-JSON parsing passes (mocked) | `uv run pytest tests/test_capture.py` |

</acceptance>

---

## Deliverables Manifest

```
aiadaptive-cos/
├── agents/
│   ├── _lib/runs.py                         # +KEY_BY_AGENT["fact-extraction"]
│   └── discord_bot/
│       ├── brain.py                         # NEW — Postgres insert_fact helper
│       ├── run.py                           # load capture cog in setup_hook
│       └── cogs/
│           └── capture.py                   # NEW — #capture listener + extraction
├── tests/
│   └── test_capture.py                      # NEW — fact-JSON parsing tests
├── architecture/PRD-phase-3.2.md            # this file
└── checklists/CHECKLIST-phase-3.2.md
```

---

## Architectural Decisions

### 1. Two `agent_run` contexts per capture (extraction + embedding)

Extraction is an Anthropic call; embedding is a Gemini call. The cost
helper's `_RunState` keeps a single `llm_provider`/`llm_model` per row (last
writer wins), so mixing providers in one context would misattribute. We use
**separate `agent_run` contexts** — one Anthropic row, one Gemini row — each
correctly attributed. Both tagged `function_label="infrastructure"`,
`correlation_kind="discord_message"`.

### 2. JSON-from-text extraction, parsed defensively

The Haiku prompt asks for `{"facts": [{"content","domain","confidence"}]}`.
`capture.py` extracts the JSON (tolerant of markdown code fences) and
validates each item. A parse failure degrades gracefully: ✅ still posts, the
reply says "couldn't structure that one," and nothing is written rather than
writing garbage. A first-class structured-output method on the cost helper is
deferred (see non-goals).

### 3. pgvector literal as a string cast, no new adapter dep

Embeddings are inserted as `%s::vector` with the vector formatted
`'[v1,v2,...]'`. Avoids adding the `pgvector` Python adapter (and its numpy
pull) in 3.2. If vector handling spreads (Tartt, recall), revisit adopting
the adapter then.

### 4. `brain.py` lives in the discord_bot package

Per `50-channel-layer.md` module structure. It's the bot's DB surface.
Phase-4+ agents get their own DB helpers; there's no shared ORM in v1 — the
schema is small and SQL is explicit.

### 5. fact-extraction reuses the existing $2.00 daily ceiling

`DAILY_CEILINGS["fact-extraction"]` was set to $2.00 in Phase 2. Only the
`KEY_BY_AGENT` entry is new. Haiku at ~$0.001/capture means ~2000 captures/day
before the ceiling — far beyond realistic volume.

### 6. Ignore the bot's own messages and non-#capture channels

The `on_message` listener filters to `CAPTURE_CHANNEL_ID` and skips
`message.author.bot`. Prevents loops and cross-channel noise.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Haiku returns malformed JSON | Medium | Low | Tolerant parse (strip fences, `json.loads`, per-item validation); graceful degrade |
| Embedding dim mismatch (≠768) | Low | High — insert fails | text-embedding-004 is 768-dim, matches `vector(768)`; assert length before insert |
| `message_content` intent disabled | Low | High — can't read messages | Enabled in Portal (verified 3.1); bot needs it for `on_message` content |
| Reaction add/remove race on rapid posts | Low | Low | Each message handled independently; idempotent on the facts insert via source_ref |
| anthropic-key-fact-extraction missing | Medium | Blocks smoke | `MissingAgentKeyError` names it; clear barry-agent task to provision |
| Capturing secrets to the brain | Low | Medium | Operator-controlled channel; v1 has no redaction. Noted for v2. |

---

## Task Breakdown (sequenced)

<tasks>

### Task 1 — Register fact-extraction key dispatch — **[BARRY-ADMIN]**
Add `KEY_BY_AGENT["fact-extraction"] = "anthropic-key-fact-extraction"` in
`agents/_lib/runs.py`. (DAILY_CEILINGS entry already exists.)

### Task 2 — Write `brain.py` — **[BARRY-ADMIN]**
`insert_fact(...)` using psycopg, db-url from keychain, vector as `%s::vector`.

### Task 3 — Write `cogs/capture.py` — **[BARRY-ADMIN]**
`on_message` → filter #capture → ⏳ → extract (Haiku via cost helper) →
embed (Gemini via cost helper) → insert facts → ✅ + reply. Handle
empty/link-only.

### Task 4 — Load the cog in `run.py` — **[BARRY-ADMIN]**
Add `await self.load_extension("agents.discord_bot.cogs.capture")`.

### Task 5 — Unit test — **[BARRY-ADMIN]**
`tests/test_capture.py` for the fact-JSON parser (well-formed, fenced,
malformed, empty).

### Task 6 — Commit builder-side — **[BARRY-ADMIN]**
Push so barry-agent can pull.

### Task 7 — Provide the Anthropic key — **[USER]**
Create an Anthropic API key for fact extraction; hand it to the build.

### Task 8 — Store the key — **[BARRY-AGENT]**
Write `anthropic-key-fact-extraction` to barry-agent keychain (direct write
in the barry-agent session).

### Task 9 — Pull + smoke — **[BARRY-AGENT]**
`git pull`, `uv sync`, run the bot, post a test capture, verify ⏳→✅, a
`facts` row, and `agent_runs` rows. Report back.

### Task 10 — Close 3.2 — **[BARRY-ADMIN]**
Final commit, memory, create PHASE-3.3.md.

</tasks>

---

## Definition of Done

<dod>

3.2 is complete when a thought posted in `#capture` reliably becomes
embedded `facts` rows with ✅ acknowledgment, the cost is visible in
`agent_runs`, and an empty/link-only message is handled gracefully.

</dod>

## What Phase 3.3 Will Do With This

3.3 builds `cli/recall.py` — hybrid search (full-text + vector) over the
`facts` table this phase populates. The round-trip test for 3.3 is: capture a
thought in 3.2, then recall it from the laptop/barry-admin via the CLI.
