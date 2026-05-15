# Phase 1 Checklist

Use this as the live tracking document during the Phase 1 build. Check items
off as you complete them. Each item maps to an acceptance criterion in
`PRD-phase-1.md`.

## Pre-flight

- [ ] PRD-phase-1.md read end-to-end
- [ ] Password manager open and ready to capture credentials
- [ ] Mac mini reachable; can SSH or sit at it directly
- [ ] Laptop reachable; can clone the repo there too

## Task 1: Supabase provisioning

- [ ] Project created on Pro plan, region chosen
- [ ] Database password generated and stored in password manager
- [ ] pgvector extension enabled
- [ ] pg_trgm extension enabled
- [ ] Project URL captured
- [ ] anon public key captured
- [ ] service_role secret key captured
- [ ] Direct DB URI captured (with ?sslmode=require)

## Task 2: Repo scaffold

- [ ] Local repo initialized on admin account
- [ ] Directory scaffold created (architecture/, migrations/, agents/_lib/, cli/, scripts/)
- [ ] 10 architecture .md files copied into architecture/
- [ ] README.md created at repo root
- [ ] .gitignore created
- [ ] Per-directory READMEs in place
- [ ] pyproject.toml created
- [ ] Private GitHub repo `aiadaptive-cos` created
- [ ] First commit made: "Phase 1: initial scaffold and architecture docs"
- [ ] Pushed to GitHub
- [ ] **AC3 ✓**: fresh clone shows the expected directory structure

## Task 3: macOS account separation

- [ ] `admin` and `agent` accounts both exist (`dscl . list /Users | grep -E '^(admin|agent)$'`)
- [ ] `agent` is NOT in admin group (`dseditgroup -o checkmember -m agent admin`)
- [ ] `agent` home directory exists at `/Users/agent`
- [ ] Password for `agent` recorded in password manager
- [ ] **AC4 ✓**: both accounts verified

## Task 4: Agent toolchain

- [ ] Logged in as `agent` (or `su - agent`)
- [ ] Homebrew installed and on PATH
- [ ] `git --version` returns a version
- [ ] `uv --version` returns a version
- [ ] `psql --version` returns a version
- [ ] **AC7 ✓**: smoke-test venv works (`uv run python -c 'print("ok")'` → `ok`)

## Task 5: Keychain credentials

- [ ] Gemini API key obtained from aistudio.google.com
- [ ] Anthropic API key obtained from console.anthropic.com (billing set up)
- [ ] GitHub personal access token obtained
- [ ] `bash scripts/keychain_setup.sh` run; all 8 items entered
- [ ] `bash scripts/keychain_verify.sh` returns 8 OK lines, 0 MISSING
- [ ] **AC5 ✓**: all credentials retrievable

## Task 6: Schema migration

- [ ] Repo cloned to `agent`: `~/agents` exists with the scaffold
- [ ] `agent` can pull/push to GitHub (with the keychain-stored PAT)
- [ ] Migration 0001 applied via Supabase SQL Editor (first time, for inline visibility)
- [ ] Verification SQL run via psql: `psql "$DB_URL" -f migrations/verify_schema.sql`
- [ ] Verification output shows 18 tables, vector and pg_trgm enabled, dashboard singleton present
- [ ] No FAIL lines in the verification output
- [ ] **AC1 ✓**: extensions enabled
- [ ] **AC2 ✓**: schema verified

## Task 7: Smoke test &amp; snapshot

- [ ] `uv sync` run in `~/agents` — produces `.venv/` and `uv.lock`
- [ ] `uv.lock` committed to git
- [ ] `uv run python scripts/smoke_test.py` returns success
- [ ] Manual snapshot: `bash scripts/snapshot_backup.sh phase1`
- [ ] `~/agents/backups/phase1_*.sql.gz` exists and is &gt; 0 bytes
- [ ] **AC6 ✓**: repo cloned to both Mac mini and laptop
- [ ] **AC8 ✓**: smoke test passes
- [ ] **AC9 ✓**: backup file exists
- [ ] **AC10 ✓**: README documents the layout

## Done

- [ ] All 10 acceptance criteria checked
- [ ] Final commit: "Phase 1: foundation complete"
- [ ] Pushed to GitHub
- [ ] Ready to start Phase 2 (Telemetry primitives)

## Notes / issues encountered

_Record anything that didn't go as the PRD described — useful for refining the
PRD for whoever comes after, even if that's you next phase._

-

-

-
