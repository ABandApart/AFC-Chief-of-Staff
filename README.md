# aiadaptive-cos

AI Adaptive Chief of Staff — a persistent operational layer for AI Adaptive's
solo consultancy practice.

## Architecture

See `architecture/00-INDEX.md` for the entry point. The system has four
separable layers — channel, action, memory, telemetry — that share a
Postgres-backed brain.

## Directory layout

```
aiadaptive-cos/
├── architecture/      Markdown design documents (read these first)
├── migrations/        Numbered .sql files; apply in order
├── agents/            One subdirectory per agent
│   └── _lib/          Shared modules (cost-emission helper lives here)
├── cli/               Operator-facing command-line helpers
├── scripts/           Setup, verification, and backup scripts
└── pyproject.toml     uv-managed Python dependencies
```

## Setup (Phase 1)

The full Phase 1 build instructions are in `architecture/PRD-phase-1.md`.
TL;DR:

1. Install local Postgres 17 + pgvector + pg_trgm.
2. Set up macOS account separation (`admin` builds, `agent` runs).
3. Configure Keychain credentials: `bash scripts/keychain_setup.sh`
4. Apply migration: `psql "$DB_URL" -f migrations/0001_initial_schema.sql`
5. Verify substrate: `uv run python scripts/smoke_test.py`

## Deviations from baseline PRD

The baseline PRD assumed hosted Supabase. The execution pivoted before any
infrastructure was provisioned. The architecture documents in
`architecture/` still describe the original hosted-Supabase design as
historical reference; the decision-log entry in `architecture/70-build-order.md`
records the change. Current implementation choices:

- **Brain is local Postgres 17 on the Mac mini**, not hosted Supabase.
  Installed via Homebrew (`postgresql@17` + `pgvector`), running under
  `barry-admin`'s LaunchAgent. Application DB `aiadaptive_cos` is owned by
  the `barry_agent` DB role. Trade-off: no external reachability without a
  tunnel, no managed backups. Revisit at Phase 6 (Roy Kent / WordPress
  webhook) when external reach becomes a requirement.
- **Single-host build**: both the `admin` (build) and `agent` (runtime)
  accounts live on the same Mac mini — `barry-admin` and `barry-agent`. The
  PRD's "laptop + Mac mini" model collapses to one machine with account
  separation. Repo lives at `/Users/barry-admin/code/aiadaptive-cos`
  (build) and `/Users/barry-agent/agents/` (runtime clone).
- **OpenClaw retired**: the prior multi-agent Slack stack (OpenClaw gateway
  in a UTM Ubuntu VM) is decommissioned. All prior API keys are rotated;
  no credential or schema sharing with the new system.

## Backup posture

Time Machine alone is **not** sufficient to back up a running Postgres
instance — live data files are constantly mid-write and snapshotting them
file-by-file produces inconsistent state. The proper stack:

1. `scripts/snapshot_backup.sh` produces a transactionally-consistent
   `pg_dump` of `~/agents/backups/<label>_<timestamp>.sql.gz`.
2. Time Machine backs up `~/agents/backups/` (static files — safe).
3. The live data directory `/opt/homebrew/var/postgresql@17/` should be
   **excluded** from Time Machine. System Settings → General → Time Machine
   → Options → "Exclude these items".

Phase 1 takes one manual snapshot. Phase 12 (Hardening) schedules the
snapshot script via launchd and adds a restore-test routine.

## Conventions

- Migrations are applied in numerical order. Never edit an applied migration;
  add a new one.
- All agents call LLMs through `agents/_lib/runs.py` (cost helper). No direct
  SDK use. This is enforced by code review at the git-gate.
- Secrets are stored in macOS Keychain, never in `.env` files or committed
  to git.
- `admin` account builds and commits; `agent` account runs scheduled jobs.

## Adding a new agent (Phase 2+)

Every agent that calls an LLM must go through the cost helper. The integration
is three steps:

1. **Pick a slug** matching the agent's directory name (e.g. `tartt`,
   `keeley-strategy`). Used as `agent_runs.agent_name` and as the keychain
   item suffix.

2. **Register in `agents/_lib/runs.py`**:
   - Add a `DAILY_CEILINGS[slug]` entry (per-day spend cap; see
     `architecture/80-telemetry-layer.md` starting ceilings table).
   - If the agent calls Anthropic: add `KEY_BY_AGENT[slug]` pointing at the
     keychain item name (e.g. `"anthropic-key-tartt"`).
   - Create the corresponding key in `barry-agent`'s keychain using the
     standard naming (e.g. via `scripts/keychain_setup.sh` extended).

3. **Use the helper from the agent's code**:

   ```python
   from agents._lib.runs import agent_run

   def summarize_item(item_text: str, item_id: int) -> str:
       with agent_run(
           "tartt",                       # agent slug
           "news_aggregation",            # function label
           correlation_id=str(item_id),   # links the run to the entity
           correlation_kind="content_item",
       ) as run:
           return run.call_anthropic(
               messages=[{"role": "user", "content": f"Summarize:\n{item_text}"}],
               model="claude-haiku-4-5",
               max_input_tokens=4000,     # G1: per-run input cap
               max_output_tokens=500,
           )
   ```

That's it. The helper writes one row to `agent_runs` per call (success,
token_cap_exceeded, or failed) and refuses calls that would exceed the
agent's daily ceiling.

See `architecture/80-telemetry-layer.md` for the full design, including the
function-label taxonomy and three-metric-per-agent pattern.

For ad-hoc spend queries:
```bash
uv run python -m cli.spend                  # today by agent
uv run python -m cli.spend --by function    # today by function
uv run python -m cli.spend --since 7d       # last week by agent
```

## Phases

The system is built in 13 phases. See `architecture/70-build-order.md` for
sequencing, dependencies, and acceptance criteria for each phase.

Phases 1 (Foundation) and 2 (Telemetry primitives) are **complete**. Phase 3 (Capture and Recall) is in progress, built in sub-phases: **3.1 (bot skeleton), 3.2 (capture flow), and 3.3 (recall CLI) are complete** — you can post a thought in `#capture` (it becomes embedded facts) and find it back with `cli/recall.py` via hybrid full-text + vector search. Sub-phase 3.4 (`/outcome` slash command) is next, then 3.5 (briefing skeleton + launchd).
