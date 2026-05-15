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

1. Provision Supabase Pro with pgvector and pg_trgm.
2. Set up macOS account separation (`admin` builds, `agent` runs).
3. Configure Keychain credentials: `bash scripts/keychain_setup.sh`
4. Apply migration: `psql "$DB_URL" -f migrations/0001_initial_schema.sql`
5. Verify substrate: `uv run python scripts/smoke_test.py`

## Deviations from baseline PRD

The PRD in `architecture/PRD-phase-1.md` is preserved as-written. We are
executing it with the following deliberate deviations:

- **Supabase tier**: starting on the **Free** plan instead of Pro. The Free
  tier supports pgvector and pg_trgm, which is all Phase 1 needs. Local
  snapshots (via `scripts/snapshot_backup.sh`) take the role that Supabase
  Pro's automated backups would have played. Upgrade trigger: connection
  limits, daily-backup retention, or DB size approaching the Free-tier
  ceiling.
- **Single-host build**: both the `admin` (build) and `agent` (runtime)
  accounts live on the same Mac mini — `barry-admin` and `barry-agent`. The
  PRD's "laptop + Mac mini" model collapses to one machine with account
  separation. Repo lives at `/Users/barry-admin/code/aiadaptive-cos`
  (build) and `/Users/barry-agent/agents/` (runtime clone).
- **OpenClaw retired**: the prior multi-agent Slack stack (OpenClaw gateway
  in a UTM Ubuntu VM) is decommissioned. All prior API keys are rotated;
  no credential or schema sharing with the new system.

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

Phase 1 (Foundation) is currently in progress. No agents have been built yet.
