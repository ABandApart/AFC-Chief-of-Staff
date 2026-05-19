# Phase 3.1: Discord Bot Skeleton — PRD & Build Instructions

<doc:meta>
  <doc:phase>3.1</doc:phase>
  <doc:parent_phase>3 — Capture and Recall</doc:parent_phase>
  <doc:theme>Bot infrastructure — connect, identify, announce</doc:theme>
  <doc:duration>~2 hours of work + ~5 minutes of API spend ($0)</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>ready to execute</doc:status>
  <doc:depends_on>Phase 2 — cost helper + agent_runs (`e46eee9`)</doc:depends_on>
  <doc:blocks>Phase 3.2 (#capture flow), 3.3 (recall), 3.4 (/outcome), 3.5 (briefing + launchd)</doc:blocks>
</doc:meta>

## TL;DR

A minimal Discord bot that connects to the AFC Richmond server, posts an
"online" announcement to `#system` on startup, and stays connected. No
interaction handling yet — just proof that the bot wiring works end-to-end.
This sub-phase is the foundation every other 3.x sub-phase builds on.

**No LLM calls in 3.1.** The cost helper is untouched here; LLM integration
arrives with `#capture` in 3.2.

---

## Goal & Non-Goals

<goals>

**Goal**: `barry-agent` can run `uv run python -m agents.discord_bot.run`,
the bot connects to AFC Richmond, posts a single startup message to
`#system`, and stays running until Ctrl+C. Bot's avatar appears as "Online"
in the server's member list.

</goals>

<non_goals>

**Not in this sub-phase**:

- **Message listeners** (capture, approvals, task-tinder) — 3.2 / later sub-phases
- **Slash commands** (/outcome, /brain query) — 3.4
- **launchd plist** (auto-start, KeepAlive) — 3.5
- **Briefing** (6am post) — 3.5
- **`brain.py`** Postgres helpers — 3.2 (when we need to write `facts` rows)
- **Cogs structure beyond `system.py`** — added as each sub-phase needs them
- **Error handling beyond bare minimum** — reconnection logic and resilience
  is Phase 12 hardening; 3.1 just exits if Discord drops the connection
- **Multi-server support** — single guild, single operator

</non_goals>

---

## Acceptance Criteria

<acceptance>

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | `discord-bot-token` in `barry-agent`'s keychain | `bash scripts/keychain_verify.sh` includes it as PRESENT |
| AC2 | `discord.py` installed via `uv sync` | `uv run python -c "import discord; print(discord.__version__)"` succeeds, version ≥ 2.4 |
| AC3 | All 6 channels exist in AFC Richmond server | Visual check; Developer Mode → channel right-click → "Copy Channel ID" works for each |
| AC4 | Channel IDs + Guild ID configured in `agents/discord_bot/config.py` | `grep -E 'GUILD_ID|SYSTEM_CHANNEL_ID' agents/discord_bot/config.py` shows the values |
| AC5 | Bot connects when run as `barry-agent` | Console shows `Logged in as <bot-name> (id: <app-id>)` within ~5s of start |
| AC6 | Bot posts to `#system` on startup | A message like `🟢 AI Adaptive CoS bot online — 2026-05-19T14:23:00Z` appears in the channel |
| AC7 | Bot stays online (does not exit on its own) | After 60 seconds running, Discord member list still shows the bot as Online |
| AC8 | Bot exits cleanly on Ctrl+C | No traceback; "Bot shutting down" log line; exit code 0 |

</acceptance>

---

## Deliverables Manifest

```
aiadaptive-cos/
├── pyproject.toml                                  # +discord.py
├── uv.lock                                         # regenerated
├── agents/
│   └── discord_bot/                                # NB: underscore, not hyphen
│       │                                          # (Python module rules)
│       ├── __init__.py
│       ├── run.py                                  # entry point
│       ├── config.py                               # GUILD_ID + channel IDs
│       └── cogs/
│           ├── __init__.py
│           └── system.py                           # startup announcement
├── architecture/
│   └── PRD-phase-3.1.md                            # this file
└── checklists/
    └── CHECKLIST-phase-3.1.md                      # live tracker
```

Note: `discord-bot/` from the architecture spec is renamed `discord_bot/`
because Python module imports require underscores. The internal naming
across docs uses "discord-bot" colloquially.

---

## Architectural Decisions

### 1. discord.py over alternatives (interactions.py, hikari)

Stack consistency. The architecture spec calls out discord.py explicitly.
discord.py 2.4+ has button/modal/slash-command support, automatic
reconnection, and the maintainership question that plagued it 2021–2022
has long been resolved.

### 2. Channel IDs in code (`config.py`), not in keychain

Discord Snowflakes (IDs) are non-secret. Anyone with bot access can see
them. Hardcoding in `config.py` makes them visible in code review and
removes a class of "keychain item missing" failure mode. The bot **token**
remains in keychain — that's the only Discord secret.

### 3. `cogs/system.py` owns the startup announcement

Even though 3.1 is minimal, structure the cog right from the start. When
3.3 adds error/alert forwarding from other agents into #system, that code
lives in the same cog. No restructuring needed across sub-phases.

### 4. Manual run in 3.1, launchd in 3.5

Running `uv run python -m agents.discord_bot.run` from a barry-agent
terminal proves the wiring. launchd adds:
- Auto-start on session login
- Restart-on-crash (KeepAlive)
- Stdout/stderr to log files

None of those matter until we have a real workload (3.2 and later). 3.1
keeps the test surface minimal.

### 5. No `brain.py` (Postgres helper) yet

3.1 makes zero DB writes. The bot doesn't even know about Postgres. 3.2
introduces `brain.py` when `cogs/capture.py` needs to insert into `facts`.

### 6. Bot uses async with discord.py's `client.run()` pattern

Standard. `client.run(token)` blocks the main thread, handles asyncio,
catches signals. No reason to roll a custom event loop.

---

## Risk Register

<risks>

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Bot token rejected by Discord (typo, expired, etc.) | Low | Blocks 3.1 entirely | Token decode showed valid app ID 150637955019020706; **Reset Token** is one click away to regenerate |
| `MESSAGE CONTENT` intent not enabled in Developer Portal | Low (already confirmed) | Bot can't read #capture in 3.2 | Verified during bot setup. Not a 3.1 issue since 3.1 doesn't read messages. |
| Channel ID typo in config.py | Medium | Bot can't find channel; startup message fails | Code verifies channel exists at startup, logs clear error if not found |
| `discord.py` version drift breaks our usage | Low | discord.py 2.4+ is stable | Pin minor version in pyproject |
| Bot disconnects after a few minutes | Medium | Bot offline in member list | discord.py auto-reconnects; 3.1 just logs reconnects. KeepAlive in 3.5 covers process death. |
| User forgets to invite bot or invites without proper scopes | Low (already done) | Bot can't see channels | Verified install completed via Guild Install |

</risks>

---

## Task Breakdown (sequenced)

<tasks>

### Task 1: Create the 6 channels in AFC Richmond — **user action**

In your Discord client, in the AFC Richmond server, create these channels
(text channels, category-organized is optional):

| Channel | Purpose |
|---|---|
| `#briefing` | Daily 6am briefing (3.5) |
| `#task-tinder` | Task candidates (Phase 5) |
| `#approvals` | Content drafts (Phase 8) |
| `#capture` | Free-form thoughts → facts (3.2) |
| `#system` | Health alerts; **3.1 posts here** |
| `#archive` | Old briefings/approvals (later) |

Set them all to private (you're the only member that can see them). Bot
already has permissions via the OAuth install.

### Task 2: Capture the IDs — **user action**

1. Discord → User Settings → Advanced → toggle **Developer Mode** ON
2. Right-click the AFC Richmond server name → **Copy Server ID** → paste below
3. Right-click each channel → **Copy Channel ID** → paste below

Send me:
```
GUILD_ID:        <paste>
#briefing:       <paste>
#task-tinder:    <paste>
#approvals:      <paste>
#capture:        <paste>
#system:         <paste>
#archive:        <paste>
```

(IDs are non-secret — they show up in URLs whenever you link to a channel.)

### Task 3: Add discord.py dep — **Claude action**

Update `pyproject.toml`:
- `discord.py>=2.4.0,<3.0`

`uv sync`. Commit `uv.lock`.

### Task 4: Stage `discord-bot-token` in barry-agent's keychain — **Claude + user**

From a barry-admin terminal (Claude runs this; user enters sudo password
once):

```bash
VAL=$(security find-generic-password -a "$USER" -s discord-bot-token -w)
sudo -u barry-agent security add-generic-password \
    -a barry-agent -s discord-bot-token -w "$VAL" -T "" -U
unset VAL
# Then delete the staged copy from barry-admin's keychain:
security delete-generic-password -a "$USER" -s discord-bot-token
```

Verify as barry-agent: `bash scripts/keychain_verify.sh` should now list
`discord-bot-token` (we'll add it to the required ITEMS list in 3.1).

### Task 5: Write the bot — **Claude action**

`agents/discord_bot/run.py`, `config.py`, `__init__.py`,
`cogs/__init__.py`, `cogs/system.py`. ~150 lines total.

### Task 6: Smoke run — **user action (as barry-agent)**

```bash
cd ~/agents
git pull
uv sync
uv run python -m agents.discord_bot.run
```

Expected output:
```
INFO:discord.client:logging in using static token
INFO:discord.gateway:Shard ID None has connected to Gateway
INFO:agents.discord_bot.run:Logged in as AFC Richmond CoS (id: 150637955019020706)
INFO:agents.discord_bot.run:Posting startup message to #system
INFO:agents.discord_bot.run:Online and idle. Ctrl+C to exit.
```

In Discord:
- Bot avatar shows as Online in member list
- `#system` channel has a new message: `🟢 AI Adaptive CoS bot online — <ISO timestamp>`

Press Ctrl+C → expect:
```
^C
INFO:agents.discord_bot.run:Shutting down...
```

Exit code 0.

### Task 7: Close 3.1 — **Claude action**

Commit + push: `"Phase 3.1: Discord bot skeleton online"`. Update memory
file and 3.1 CHECKLIST.

</tasks>

---

## Definition of Done

<dod>

3.1 is complete when:

1. All 8 acceptance criteria pass.
2. The repo's `main` branch contains a commit `"Phase 3.1: Discord bot skeleton online"`.
3. You can `uv run python -m agents.discord_bot.run` from barry-agent
   and the bot announces itself in #system.
4. The bot does NOT do anything beyond that — no message listeners, no
   slash commands, no Postgres writes. (If it does, you've drifted into 3.2.)

</dod>

---

## What Phase 3.2 Will Do With This

3.2 (Capture flow) adds:
- `agents/discord_bot/brain.py` — Postgres connection helpers
- `agents/discord_bot/cogs/capture.py` — listens to `#capture`, reacts ⏳,
  extracts facts via cost helper (`agent_run("fact-extraction", ...)`),
  embeds via Gemini, writes to `facts`, replaces ⏳ with ✅
- New keychain item: `anthropic-key-fact-extraction` (you provide it)
- New `KEY_BY_AGENT` entry in `runs.py`: `"fact-extraction"` → that key
- New `DAILY_CEILINGS` entry: `"fact-extraction"` → $2.00

The bot skeleton from 3.1 is unchanged in 3.2 — we just load an
additional cog at startup.
