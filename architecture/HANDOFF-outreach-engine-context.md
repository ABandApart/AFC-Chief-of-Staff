# Context handoff → outreach-engine dev thread

**From:** barry-admin · 2026-09-03 · `main`@`9b2c612` · repo `~/code/aiadaptive-cos`

Orientation for a thread adding functionality to the Track O outreach engine.
This is the lay of the land — specs, files, schema, conventions, current state,
and the sharp edges. Bring your feature spec; this tells you where it plugs in.

---

## 1. Read order (specs)

1. **`architecture/37-outreach-workflow.md`** — the map. Read first; it's the
   picture of the whole workflow.
2. **`architecture/35-outreach-crm.md`** — the specification, dense, §1–16. The
   sections you'll reach for:
   | § | Topic | § | Topic |
   |---|---|---|---|
   | 2 | Schema (targets/evidence/packets/touches) | 8 | Closing the loop · capacity |
   | 3 | Staleness (freshness tiers) | 9 | Surfaces (Discord) |
   | 4 | Scoring (S1–S5) | 10 | Watchlist — Trent Crimm |
   | 5 | Intake | 11 | Ingest hardening |
   | 6 | Evidence acquisition | 13 | Tiers & trust boundaries |
   | 7 | The work packet | 14 | Loops, playbooks, config |
3. **PRDs** for the two deeper sub-builds: `PRD-outreach-company-profile.md`
   (enrichment/profile) and `PRD-outreach-gmail-channel.md` (the send half).
4. **`/Users/Shared/afc-richmond/PHASE-TRACK-O-OUTREACH.md`** — the living
   append-only coordination log (barry-admin ↔ barry-agent). The newest entries
   (~line 1590+) are the current state of play; skim them for what's in flight.

**Track O is structured in Parts** (referenced throughout the code):
Part 0 = discovery, Part 1 = profile/news observation, Part 2 = news
classification, Part 3 = Apollo enrichment, Part 4 = selection learning.

---

## 2. The two-box workflow (read before touching anything)

Two machines share this repo and a Postgres brain:

- **barry-admin (build box, you're likely here):** no runtime credentials. Builds
  code, runs `uv run pytest -q` + `ruff`, writes migrations. DB access is the
  local socket superuser — `psql aiadaptive_cos` (no `$DB_URL`). Cwd for the
  human is `/Users/barry-admin/Tools`; the repo is `~/code/aiadaptive-cos`.
- **barry-agent (runtime):** holds the credentials (Anthropic/Gemini/Apollo/Gmail
  keychain items, `db-url`), runs the loops + Discord bot live from `~/agents`.
  **It applies migrations** (`psql "$DB_URL" -f …`) and restarts the bot.

Coordinate through `PHASE-TRACK-O-OUTREACH.md` (chmod 666 the file, 777 the dir
after writing). Land code on `main`; barry-agent pulls. Loop-enable flips often
ship as their own PR (last was **#3**, merged). Don't assume you can run live —
hand runtime steps to barry-agent.

---

## 3. Where the code lives (by pipeline stage)

All under `agents/outreach/` unless noted. Shared core is **`agents/_lib/outreach.py`**
(target upsert + evidence poll semantics) — most stages call it.

| Stage | Modules | Loop / surface |
|---|---|---|
| **Discovery** (Part 0) | `discover.py`, `discovery/{seed_list,news_query,apollo_search,extract}.py`, `_lib/outreach_discovery.py`, `cli/discovery_import.py` | `outreach-discover`, cog `outreach_discovery` |
| **ICP / verify / learn** | `icp.py` (fit scoring), `verify.py` (R0.5 checks), `learn.py` (Gate-0 accept-rate feedback), `classify.py` (news-signal promotion, Part 2) | — |
| **Evidence** (Part 0 core) | `evidence.py`, `adapters.py` (ATS board adapters) | `outreach-evidence` (12h) |
| **Profile / watchlist** (Part 1) | `profile.py`, `profile_graph.py`, `news.py` (pure helpers), Trent Crimm (§10) | `outreach-profile` |
| **Enrichment** (Part 3) | `apollo.py` (API client), `enrich.py` (storing adapter), `cli/outreach_enrich.py` | — |
| **Scoring / intake** | `_lib/outreach.py`, `_lib/outreach_intake.py`, `cli/{outreach_score,outreach_import,outreach_gaps,outreach_preview}.py` | cog `outreach_intake` |
| **Sequencing / packets / capacity** | `daily.py` (05:45 assembly) | `outreach-daily` |
| **Send half** | `gmail.py` (drafting), `gmail_capture.py` (send + BCC capture) | `outreach-gmail-draft` (06:00), `outreach-gmail-capture` (15m) |
| **Rescore** (O2) | `rescore.py`, `_lib/outreach_rescore.py` | `outreach-rescore` (Sun 18:00), cog `outreach_rescore` |

Tests mirror these: `tests/test_outreach_*.py` (24 files) + `test_apollo_*`. The
convention is **logic in `_lib/` (unit-tested), thin cogs**.

---

## 4. Data model

**Tables** (`aiadaptive_cos`): `outreach_targets`, `outreach_evidence`,
`outreach_touches`, `outreach_packets`, `outreach_events` (audit log),
`outreach_discoveries` (Gate-0 review queue), `outreach_segment_scores`,
`outreach_icp_models`, `outreach_watch_signals`, `sources`. (Tartt-adjacent:
`content_items`, `content_pipeline`.)

**Views:** `v_outreach_scored` (the scored target view — **see gotcha below**),
`v_outreach_capacity`, `v_outreach_evidence_display`, `v_prospect`,
`v_new_prospects`, `v_open_followups`, `v_pending_task_candidates`.

**Triggers / functions:**
- `outreach_log_event()` — audit trigger; diffs changed columns via `jsonb_each`
  into `outreach_events`. Any new column on an audited table is captured
  automatically.
- `outreach_touch_ready_guard()` — blocks a touch going "ready" prematurely;
  **exempts `sent_via IN ('gmail_api','bcc')`** (capture writes must land).
- `outreach_touch_updated_at()`, `outreach_s1(trigger_date, asof)` (S1 scoring).

**Migrations:** `0013`–`0025` are Track O (`0013` core schema, `0014` vocabulary,
`0016` intake, `0018` discoveries, `0019` segment scores, `0022` ICP models,
`0023` trigger-date acceptance, `0024` firmographic spine, `0025` Gmail channel).
`0026` widened Tartt sources. **Next free number: `0027`.**

---

## 5. Conventions & patterns (load-bearing)

- **Spec-driven.** Confirm an outcome-based spec (+ verification) before a build
  increment; correct the spec when reality contradicts it (35- §15 is the build
  order; the coordination log records deviations).
- **LLM telemetry.** Every model call goes through `runs.agent_run(agent, fn)`
  with a `DAILY_CEILINGS` entry (`agents/_lib/runs.py`). Outreach labels:
  `outreach-discover` $0.25, `trent-crimm` $0.30, `tartt-control` $0.20. Model is
  "record, don't refuse" — a soft post-hoc ceiling, not a pre-flight gate. Add a
  ceiling entry before running any new labeled agent (it raises otherwise).
- **Discovery channels.** `find(segment) -> [candidate]`, registered in
  `discovery.CHANNELS`; dedup against `known_domains` (targets ∪ pool). Add a
  channel by writing the module + registering it.
- **`db.connection()` is autocommit** (`agents/_lib/db.py`) — writes commit on
  execute; no explicit `commit()`.
- **Singleton guard** for one-row config: `only_row BOOLEAN PRIMARY KEY DEFAULT
  true CHECK (only_row)`.
- **Discord cogs:** persistent Views `timeout=None` + `_reattach_views` on
  startup; operator guard `OPERATOR_DISCORD_ID` (`agents/discord_bot/config.py`);
  `RadioGroup.value` (not `.values`). Register in `agents/discord_bot/run.py`.
- **B2 — the system NEVER sends mail.** Drafting composes; a human sends. Enforced
  by `tests/test_no_outbound_send.py` (CI grep for `.send(`/`smtplib`/`sendmail`).
  Any outbound-send path is a hard stop — route it through a human action.
- **barry-agent-only deps** live in `[dependency-groups]` (cognee, tartt, gmail,
  mcp); imported lazily so the build box stays green without them. `uv sync
  --inexact --group <name>` to avoid pruning siblings.
- **Migrations:** never edit an applied one; add a numbered idempotent file
  (`NOT EXISTS`/`IF NOT EXISTS`); barry-agent applies.

---

## 6. Current state (as of `9f2d077` + PRs #6/#7 in flight, 2026-09-10)

**Live (enabled loops):** `outreach-evidence`, `outreach-daily`, `outreach-discover`,
`outreach-profile`, `outreach-classify`, `outreach-gmail-draft`,
`outreach-gmail-capture`, `outreach-rescore` (+ `tartt-poll`) — **all 12 loops enabled**.
The full closed loop — discover → evidence → score → sequence → packet → Gmail draft →
send-capture → rescore — is built and running. The **#outreach daily contact surface**
(Contact/Defer cards, migration 0027, `outreach_today` cog) is live and verified.

**In flight — open PRs (merge #6 before #7):**
- **#6 — DB connection-pool bounce-resilience** (`agents/_lib/db.py`; spec
  `HANDOFF-2026-09-09-discord-pool-wedge.md`). A Postgres restart under the long-lived
  bot wedged the *entire* process — every cog, silently, until a manual restart — and
  Postgres bounces on every reboot here. Fix: `check` on checkout + `max_lifetime` +
  libpq keepalives on **both** pools. Unit/config test green; the live bounce test (S3 #2)
  is a Postgres-owner (barry-admin) step. **Until it merges, the wedge recurs** — recovery
  is "start Postgres (barry-admin) → restart the bot (barry-agent)."
- **#7 — Task Tinder / #outreach split, Increment 1** (`PRD-tasktinder-refinements.md`).
  All outreach decision cards — Gate-1 intake, Gate-0 review, O2 re-score — now post to
  **`#outreach`**, not `#task-tinder`; `#task-tinder` keeps content suggestions + inbound
  leads. A grep-guard pins the routing invariant. Takes effect on the next bot restart.

**Next build (specced in `PRD-tasktinder-refinements.md`, NOT built):**
- **Increment 2** — show the already-stored source link on content cards.
- **Increment 3** — accepted items → a bot-maintained **pinned checklist in a new `#tasks`
  channel** (`tasks WHERE status='open'`; content-accepted only — no outreach leak by
  construction). The largest of the three; could graduate to its own PRD.

**Runtime steps outstanding (barry-agent):** the pool-wedge live-verify (#6); and a bot
restart once #7 merges, for the channel move to take effect.

**Built, gated on external things:**
- **Apollo contacts** — `people/match` is a **paid** endpoint (403 on free/trial);
  firmographic `organizations/enrich` + `organizations/search` are free but
  credit-metered (monthly allotment). Contact enrichment waits on full paid.
- **BCC body capture** — needs a `gmail-bcc-imap` app password; capture advances
  arcs via the Gmail history path without it (body just doesn't land).
- **Loop watchdog** — `cos-scheduler`/`cos-outreach-evidence`/`cos-outreach-bcc`
  heartbeats wired; no-op until barry-agent provisions `healthchecks-ping-key`
  (`80-telemetry-layer.md` §PERF-4).

**Parked:** departure detection (OQ1). **Open decisions:** 35- §16.

---

## 7. Sharp edges (will bite if unknown)

- **`v_outreach_scored` has a frozen column list** (`SELECT t.*` was expanded at
  create time). Adding a column to `outreach_targets` does **not** appear in the
  view until you **DROP + CREATE** it in the same migration. Miss this and the new
  column is silently absent downstream.
- **Apollo tiering:** trial ≠ paid — a 14-day trial is limited exactly like free.
  `agents/outreach/apollo.py` translates the failures (`ApolloPlanError` 403,
  `ApolloRateLimitError` 429, `ApolloCreditsError` 422/credit).
- **The audit trigger fires on every write** to audited tables — bulk backfills
  generate an `outreach_events` row per changed column per row.
- **`outreach_discoveries` cog** uses edit-on-write rendering: a card keeps its
  pre-change look until the next decision on its page.

---

## 8. Verify your work

```bash
cd ~/code/aiadaptive-cos && uv run pytest -q      # ~936 passing on the build box
uv run ruff check <changed files>
```
For anything touching the schema, also apply your migration to the build DB
(`psql aiadaptive_cos -f migrations/00NN_*.sql`) and re-run — the build box has a
copy of both `aiadaptive_cos` and `aiadaptive_cognee` to validate against.

---
*Questions or a specific feature to place? The coordination log is the channel.*
