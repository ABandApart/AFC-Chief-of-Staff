# Phase 1 Checklist

Use this as the live tracking document during the Phase 1 build. Check items
off as you complete them. Each item maps to an acceptance criterion in
`architecture/PRD-phase-1.md`.

This checklist reflects the **post-pivot** plan: local Postgres 17 instead
of hosted Supabase. The decision-log entry is in
`architecture/70-build-order.md` (2026-05-19).

## Pre-flight

- [x] PRD-phase-1.md read end-to-end
- [x] Password manager open and ready
- [x] OpenClaw gateway stopped and UTM VM shut down

## Task 1: Local Postgres 17 install

- [x] `brew install postgresql@17 pgvector` succeeds (required `brew reinstall postgresql@17` once to fix initial file layout issue)
- [x] `brew services start postgresql@17` — LaunchAgent registered under barry-admin
- [x] Cluster reachable: `psql postgres -c '\conninfo'` connects as barry-admin
- [x] 32-char password generated for `barry_agent` DB role; stored in `cos-db-password-staging` keychain item (deleted after Task 1 step 6)
- [x] `aiadaptive_cos` database created
- [x] `barry_agent` role created with LOGIN, owns `aiadaptive_cos`
- [x] `CREATE EXTENSION vector;` succeeds in `aiadaptive_cos` (pgvector 0.8.2)
- [x] `CREATE EXTENSION pg_trgm;` succeeds in `aiadaptive_cos` (1.6)
- [x] Full `db-url` constructed and stored in barry-admin keychain
- [ ] `/opt/homebrew/var/postgresql@17/` added to Time Machine exclusions (manual step — user)
- [x] **Pre-flight**: `uv run python scripts/smoke_test.py` from barry-admin succeeds (full stack: psycopg + TCP + password auth + read/write)

## Task 2: Repo scaffold

- [x] Local repo initialized on barry-admin
- [x] Directory scaffold created (architecture/, migrations/, agents/_lib/, cli/, scripts/, checklists/)
- [x] 10 architecture .md files copied into architecture/
- [x] README.md created at repo root
- [x] .gitignore created
- [x] Per-directory READMEs in place
- [x] pyproject.toml created
- [x] Private GitHub repo `ABandApart/AFC-Chief-of-Staff` created
- [x] First commit made: "Phase 1: initial scaffold and architecture docs"
- [x] Pushed to GitHub
- [ ] Pivot commit pushed: "Phase 1: pivot to local Postgres 17"
- [x] **AC3 ✓**: fresh clone shows the expected directory structure

## Task 3: macOS account separation

- [x] `barry-admin` and `barry-agent` accounts both exist
- [x] `barry-agent` is NOT in admin group
- [x] `barry-agent` home directory exists at `/Users/barry-agent`
- [x] Password for `barry-agent` recorded in password manager
- [x] **AC4 ✓**: both accounts verified

## Task 4: Agent toolchain

- [x] Homebrew installed system-wide at /opt/homebrew (under barry-admin)
- [x] `uv` installed system-wide via brew
- [x] `psql` (via libpq) installed and force-linked
- [x] `gh` installed (used for any future browser-auth flows)
- [x] barry-admin .zshrc updated with brew shellenv + libpq path
- [ ] barry-agent .zshrc updated with brew shellenv + libpq path
- [ ] As barry-agent: `git --version`, `uv --version`, `psql --version` all return versions
- [ ] **AC7 ✓**: `uv run python -c 'print("ok")'` returns `ok` from barry-agent

## Task 5: Keychain credentials (4 items, not 8)

- [ ] Gemini API key obtained from aistudio.google.com
- [ ] Anthropic API key obtained from console.anthropic.com (billing confirmed)
- [ ] Fine-grained GitHub PAT obtained (scoped to ABandApart/AFC-Chief-of-Staff, Contents R/W)
- [ ] `db-url` copied from barry-admin keychain to barry-agent keychain (sudo step)
- [ ] As barry-agent: `bash scripts/keychain_setup.sh` run; 3 remaining items entered
- [ ] As barry-agent: `bash scripts/keychain_verify.sh` returns 4 OK lines, 0 MISSING
- [ ] **AC5 ✓**: all 4 credentials retrievable

## Task 6: Schema migration

- [ ] Repo cloned to barry-agent at `~/agents`
- [x] `psql "$DB_URL" -f migrations/0001_initial_schema.sql` succeeds with no ERROR lines (run from barry-admin during install)
- [x] `psql "$DB_URL" -f migrations/verify_schema.sql` shows 18 tables, vector + pg_trgm, dashboard singleton
- [x] No FAIL lines in the verification output
- [x] **AC1 ✓**: extensions enabled
- [x] **AC2 ✓**: schema verified

## Task 7: Smoke test & snapshot

- [ ] `uv sync` run in `~/agents` — produces `.venv/` and `uv.lock`
- [ ] `uv.lock` committed to git
- [ ] `uv run python scripts/smoke_test.py` returns success
- [ ] Manual snapshot: `bash scripts/snapshot_backup.sh phase1`
- [ ] `~/agents/backups/phase1_*.sql.gz` exists and is > 0 bytes
- [ ] **AC6 ✓**: repo cloned to barry-agent
- [ ] **AC8 ✓**: smoke test passes
- [ ] **AC9 ✓**: backup file exists
- [ ] **AC10 ✓**: README documents the layout and the pivot

## Done

- [ ] All 10 acceptance criteria checked
- [ ] `cos-db-password-staging` keychain item deleted from barry-admin (no longer needed; password lives only inside the `db-url` URI now)
- [ ] Final commit: "Phase 1: foundation complete"
- [ ] Pushed to GitHub
- [ ] Memory file refreshed (`project_afc_richmond.md` no longer points at OpenClaw)
- [ ] Ready to start Phase 2 (Telemetry primitives)

## Notes / issues encountered

_Record anything that didn't go as the PRD described — useful for refining the
PRD for whoever comes after, even if that's you next phase._

- 2026-05-19: Pivoted from hosted Supabase to local Postgres 17 before any infra was provisioned. Reasoning in `architecture/70-build-order.md` decision log. PRD Task 1 was rewritten; credential inventory shrank from 8 to 4 items.

-

-
