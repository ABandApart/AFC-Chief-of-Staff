# Phase 1 Checklist — COMPLETE

Final state of the Phase 1 build. All 10 acceptance criteria satisfied.
Each item maps to an acceptance criterion in `architecture/PRD-phase-1.md`.

This checklist reflects the **post-pivot** plan: local Postgres 17 instead
of hosted Supabase, with per-agent Anthropic API keys. Decision-log entries
are in `architecture/70-build-order.md` (2026-05-19).

## Pre-flight

- [x] PRD-phase-1.md read end-to-end
- [x] Password manager open and ready
- [x] OpenClaw gateway stopped and UTM VM shut down

## Task 1: Local Postgres 17 install

- [x] `brew install postgresql@17 pgvector` succeeds (required `brew reinstall postgresql@17` once to fix initial file layout issue)
- [x] `brew services start postgresql@17` — LaunchAgent registered under barry-admin
- [x] Cluster reachable: `psql postgres -c '\conninfo'` connects as barry-admin
- [x] 32-char password generated for `barry_agent` DB role; folded into `db-url` URI then staging copy deleted
- [x] `aiadaptive_cos` database created
- [x] `barry_agent` role created with LOGIN, owns `aiadaptive_cos`
- [x] `CREATE EXTENSION vector;` succeeds in `aiadaptive_cos` (pgvector 0.8.2)
- [x] `CREATE EXTENSION pg_trgm;` succeeds in `aiadaptive_cos` (1.6)
- [x] Full `db-url` constructed and stored in barry-admin keychain (staged)
- [ ] `/opt/homebrew/var/postgresql@17/` added to Time Machine exclusions (**manual step — user**, not blocking phase completion)
- [x] **Pre-flight**: `uv run python scripts/smoke_test.py` from barry-admin succeeds (full stack: psycopg + TCP + password auth + read/write)

## Task 2: Repo scaffold

- [x] Local repo initialized on barry-admin
- [x] Directory scaffold created (architecture/, migrations/, agents/_lib/, cli/, scripts/, checklists/)
- [x] 10 architecture .md files copied into architecture/
- [x] README.md created at repo root with Deviations + Backup-posture sections
- [x] .gitignore created
- [x] Per-directory READMEs in place
- [x] pyproject.toml created (later updated to `[dependency-groups]`)
- [x] Private GitHub repo `ABandApart/AFC-Chief-of-Staff` created
- [x] First commit made: "Phase 1: initial scaffold and architecture docs"
- [x] Pushed to GitHub
- [x] Pivot commit pushed: "Phase 1: pivot to local Postgres 17"
- [x] Per-agent keys commit: "Phase 1: per-agent Anthropic keys + cred inventory expansion"
- [x] Postgres-install commit: "Phase 1: Postgres installed, schema applied, smoke test passes"
- [x] **AC3 ✓**: fresh clone shows the expected directory structure

## Task 3: macOS account separation

- [x] `barry-admin` and `barry-agent` accounts both exist
- [x] `barry-agent` is NOT in admin group
- [x] `barry-agent` home directory exists at `/Users/barry-agent`
- [x] Password for `barry-agent` recorded in password manager
- [x] **AC4 ✓**: both accounts verified

## Task 4: Agent toolchain

- [x] Homebrew installed system-wide at /opt/homebrew (under barry-admin)
- [x] `uv` installed system-wide via brew (uv 0.11.14)
- [x] `psql` (via libpq) installed and force-linked (psql 18.4)
- [x] `gh` installed (gh 2.92.0)
- [x] barry-admin .zshrc updated with brew shellenv + libpq path
- [x] barry-agent .zshrc updated with brew shellenv + libpq path (via onboarding script)
- [x] As barry-agent: `git --version`, `uv --version`, `psql --version` all return versions
- [x] **AC7 ✓**: `uv run python -c 'print("ok")'` returns `ok` from barry-agent (proven via smoke test)

## Task 5: Keychain credentials (8 required items + 1 optional)

- [x] Gemini API key obtained from aistudio.google.com (Tartt)
- [x] 6 per-agent Anthropic keys obtained: Ted, Keeley Strategy, Keeley Content, Roy Kent, Nate Shelley, Higgins
- [x] GitHub auth decision: share barry-admin's SSH key (Option A — no PAT)
- [x] 8 required items present in barry-admin keychain (staged)
- [x] Bulk-copy from barry-admin → barry-agent keychain (via onboarding script step 3)
- [x] As barry-agent: `bash scripts/keychain_verify.sh` returns 8 OK + 0 MISSING (required)
- [x] Staged copies deleted from barry-admin keychain (post-onboarding cleanup)
- [x] **AC5 ✓**: all 8 required credentials retrievable from barry-agent's keychain

## Task 6: Schema migration

- [x] Repo cloned to barry-agent at `~/agents` (after prior contents moved to `~/agents.pre-phase-1-backup`)
- [x] `psql "$DB_URL" -f migrations/0001_initial_schema.sql` succeeds with no ERROR lines (run from barry-admin during install; idempotent — barry-agent's clone connects to same DB)
- [x] `psql "$DB_URL" -f migrations/verify_schema.sql` shows 18 tables, vector + pg_trgm, dashboard singleton
- [x] No FAIL lines in the verification output
- [x] **AC1 ✓**: extensions enabled
- [x] **AC2 ✓**: schema verified

## Task 7: Smoke test & snapshot

- [x] `uv sync` run in `~/agents` — produces `.venv/` and `uv.lock` (committed at barry-admin pre-flight)
- [x] `uv.lock` committed to git
- [x] `uv run python scripts/smoke_test.py` returns success (from barry-agent)
- [x] Manual snapshot: `bash scripts/snapshot_backup.sh phase1` from barry-agent
- [x] `~/agents/backups/phase1_20260519T181144Z.sql.gz` exists (5.3 KB — empty-table dump, schema only)
- [x] **AC6 ✓**: repo cloned to barry-agent at `/Users/barry-agent/agents`
- [x] **AC8 ✓**: smoke test passes from barry-agent
- [x] **AC9 ✓**: backup file exists
- [x] **AC10 ✓**: README documents the layout and the pivot

## Done

- [x] All 10 acceptance criteria checked
- [x] `cos-db-password-staging` keychain item deleted from barry-admin
- [x] 8 staged credential copies deleted from barry-admin keychain (now live only in barry-agent's)
- [x] OpenClaw onboarding tempfiles in /tmp/aiadaptive-onboard/ self-destructed
- [ ] Final commit: "Phase 1: foundation complete"  ← this commit
- [ ] Pushed to GitHub
- [ ] Memory file refreshed (`project_afc_richmond.md`)
- [ ] Ready to start Phase 2 (Telemetry primitives)

## Notes / issues encountered

- 2026-05-19: Pivoted from hosted Supabase to local Postgres 17 before any infra was provisioned. Reasoning in `architecture/70-build-order.md` decision log. PRD Task 1 was rewritten.
- 2026-05-19: Anthropic API keys split per-agent (Ted, Keeley Strategy, Keeley Content, Roy Kent, Nate Shelley, Higgins). Credential inventory grew from 4 to 8 required items. Implication for Phase 2 cost helper: must dispatch keys by agent name.
- 2026-05-19: `brew install postgresql@17 pgvector` left the postgresql@17 share dir incompletely populated (missing `pg_hba.conf.sample`, etc.). A second `brew reinstall postgresql@17` fixed it. Suspected to be a transient brew formula issue.
- 2026-05-19: Source files copied from `Documents/AFC Richmond/phase-1-foundation/` had HTML entities (`&gt;` / `&lt;` / `&amp;`) baked in — broke smoke_test.py Python parsing and would have broken shell-script redirections. Scrubbed in commit `773ed08`.
- 2026-05-19: `/Users/barry-agent/agents/` pre-existed with March-vintage draft architecture files owned by barry-admin. barry-agent could not rename (insufficient permission despite owning the parent dir — root cause likely ACL or extended attribute). Resolved by `sudo mv` from barry-admin's session, renamed to `~/agents.pre-phase-1-backup` for archival.
