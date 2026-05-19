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

## Phases

The system is built in 13 phases. See `architecture/70-build-order.md` for
sequencing, dependencies, and acceptance criteria for each phase.

Phase 1 (Foundation) is **complete** as of 2026-05-19. No agents have been built yet — Phase 2 (Telemetry primitives) is next.
