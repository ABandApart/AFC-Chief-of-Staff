# Phase 1: Foundation — PRD &amp; Build Instructions

<doc:meta>
  <doc:phase>1</doc:phase>
  <doc:theme>Foundation</doc:theme>
  <doc:duration>1 week</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>ready to execute</doc:status>
  <doc:depends_on>none — this is the first phase</doc:depends_on>
  <doc:blocks>Phase 2 (Telemetry primitives) and every subsequent phase</doc:blocks>
</doc:meta>

## TL;DR

By the end of Phase 1, a Python script running on the Mac mini `agent` account can connect to Supabase, read and write all 18 tables defined in the v1 schema, and the same repo exists on the laptop for ad-hoc Claude Code sessions. No automation, no LLM calls, no Discord bot — just the plumbing.

This phase is deliberately small. Its job is to make Phase 2 (telemetry primitives) possible without retrofitting.

---

## Goal &amp; Non-Goals

<goals>

**Goal**: A working substrate. Specifically:

1. Hosted Supabase Pro project provisioned with pgvector and pg_trgm enabled.
2. Schema migration 0001 applied: all 18 tables, indexes, and extensions.
3. Private GitHub repo `aiadaptive-cos` initialized with directory scaffolding.
4. macOS account separation operational: `admin` (build) and `agent` (run).
5. All credentials stored in `agent` Keychain, retrievable from Python.
6. Repo cloned to both `agent` account (`~/agents/`) and laptop.
7. Connectivity test passes: a Python script on `agent` reads and writes Supabase.

</goals>

<non_goals>

**Not in this phase**:

- No LLM calls of any kind. (The cost helper that wraps all LLM calls is Phase 2.)
- No Discord bot. (Phase 3.)
- No scheduled jobs in launchd. (Earliest is Phase 3 for the briefing skeleton.)
- No agent code beyond the connectivity smoke test.
- No backups beyond the manual one this PRD specifies at the end.
- No multi-environment setup (dev/staging/prod). Single hosted Supabase project; the brain doesn't need staging at this scale.

</non_goals>

---

## Acceptance Criteria

<acceptance>

The phase is done when all of the following are true. Each is independently verifiable.

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | Supabase project exists on the Pro plan with pgvector and pg_trgm enabled | Supabase dashboard → Database → Extensions shows both enabled |
| AC2 | All tables in migration 0001 exist with correct columns and indexes | Run `verify_schema.sql` (provided) — output shows 18 tables, no errors |
| AC3 | Private GitHub repo `aiadaptive-cos` exists with the directory structure | `git ls-tree -d HEAD` from a fresh clone shows architecture/, migrations/, agents/, agents/_lib/, cli/, scripts/ |
| AC4 | `admin` and `agent` macOS accounts both exist and are separated | `dscl . list /Users \| grep -E '^(admin\|agent)$'` returns both |
| AC5 | All credentials in the inventory are in `agent`'s Keychain | `security find-generic-password -a $USER -s <name>` succeeds for each item (script provided) |
| AC6 | Repo cloned to `~/agents/` on `agent` account and to the laptop | Both directories exist and `git remote -v` matches the GitHub URL |
| AC7 | `uv` is installed on the `agent` account and a smoke-test venv works | `uv --version` returns a version; `uv run python -c "print('ok')"` returns `ok` |
| AC8 | Smoke test connects to Supabase from `agent` account | `python scripts/smoke_test.py` returns `OK — connected at <timestamp>, 18 tables visible` |
| AC9 | Manual snapshot backup taken | `~/agents/backups/snapshot_phase1.sql.gz` exists and is > 0 bytes |
| AC10 | README.md committed to the repo documenting the layout | `cat README.md` displays the layout section |

</acceptance>

---

## Out-of-Scope but Worth Noting

These show up in later phases. Mentioning here so you don't accidentally do them in Phase 1:

- The `agent_runs` table and `outcomes` table exist in the schema but no code writes to them yet. (Phase 2 adds the helper that writes to `agent_runs`. Phase 3 adds the `/outcome` slash command that writes to `outcomes`.)
- The cost-emission helper (`agents/_lib/runs.py`) is mentioned in the directory scaffold but is empty in Phase 1. Phase 2 fills it.
- Row-level security is not configured. Single-user system in v1; RLS is a v2 concern when multi-context support is added.

---

## Deliverables Manifest

Files created in this phase:

```
aiadaptive-cos/
├── README.md
├── .gitignore
├── architecture/                       # the 10 .md files from prior phase
│   ├── 00-INDEX.md
│   ├── 10-strategy.md
│   ├── 20-architecture-overview.md
│   ├── 30-memory-layer.md
│   ├── 40-action-layer.md
│   ├── 50-channel-layer.md
│   ├── 60-content-pipeline.md
│   ├── 70-build-order.md
│   ├── 80-telemetry-layer.md
│   └── 90-workflows.md
├── migrations/
│   ├── 0001_initial_schema.sql         # this phase
│   ├── verify_schema.sql               # this phase (idempotent check)
│   └── README.md                       # how to apply migrations
├── agents/
│   ├── _lib/
│   │   ├── __init__.py
│   │   └── README.md                   # placeholder; Phase 2 fills runs.py
│   └── README.md
├── cli/
│   └── README.md
├── scripts/
│   ├── smoke_test.py                   # this phase
│   ├── keychain_setup.sh               # this phase
│   ├── keychain_verify.sh              # this phase
│   ├── snapshot_backup.sh              # this phase
│   └── README.md
└── pyproject.toml                      # this phase — uv-managed
```

Outside the repo:

- Supabase project (URL and keys recorded in 1Password / your password manager).
- Keychain entries on the `agent` account.
- A manual `.sql.gz` snapshot in `~/agents/backups/`.

---

## Task Breakdown (sequenced)

<tasks>

The seven tasks below are sequenced so each one's prerequisites are complete. Estimated total: 6–8 hours over 3–4 sittings.

### Task 1: Provision Supabase

**Outcome**: Hosted Supabase Pro project exists with pgvector and pg_trgm enabled, project URL and service-role key captured.

**Steps**:

1. Log in to supabase.com. Create a new project under your organization.
   - Name: `aiadaptive-cos`
   - Region: closest to where the Mac mini lives (lower query latency)
   - Plan: Pro ($25/mo) — needed for backups and pgvector at scale
   - Database password: generate a strong one; store in password manager
2. Wait for project provisioning (typically 1–2 minutes).
3. Database → Extensions:
   - Enable `vector` (pgvector)
   - Enable `pg_trgm`
4. Settings → API: copy and store the following in your password manager:
   - Project URL (e.g. `https://abcdefgh.supabase.co`)
   - `anon` public key
   - `service_role` secret key
   - Database password (already stored from step 1)
5. Settings → Database → Connection string → URI: copy and store the direct connection string (used by `pg_dump` for backups).

**Verification**: Database → Extensions shows both `vector` and `pg_trgm` with status "enabled". You have 4 credentials captured for the next task.

**Estimated time**: 20 minutes.

---

### Task 2: Initialize Repo and Push Architecture Docs

**Outcome**: Private GitHub repo `aiadaptive-cos` exists, contains the architecture/ directory and a README, and is cloneable.

**Steps**:

1. On your `admin` account (laptop is fine), create a new directory and initialize git:
   ```bash
   mkdir -p ~/code/aiadaptive-cos
   cd ~/code/aiadaptive-cos
   git init -b main
   ```
2. Create the directory scaffold:
   ```bash
   mkdir -p architecture migrations agents/_lib cli scripts
   ```
3. Copy the 10 architecture markdown files into `architecture/`. (You have these from the prior conversation as `chief-of-staff-architecture.zip`.)
4. Create the `README.md` (content provided in the appendix of this PRD).
5. Create the `.gitignore` (content provided in the appendix).
6. Create placeholder READMEs in each subdirectory (one-liner each, also in appendix).
7. Create the GitHub repo: on github.com, create a new **private** repo named `aiadaptive-cos`. Do not initialize with a README (we have one already).
8. Wire up remote and push:
   ```bash
   git add .
   git commit -m "Phase 1: initial scaffold and architecture docs"
   git remote add origin git@github.com:<your-username>/aiadaptive-cos.git
   git push -u origin main
   ```

**Verification**: `git ls-tree -r HEAD --name-only` from a fresh clone shows the directory structure listed in the manifest.

**Estimated time**: 30 minutes.

---

### Task 3: Confirm macOS Account Separation

**Outcome**: `admin` and `agent` accounts exist, are separate, and `agent` is non-admin.

This task is largely already done per the existing OpenClaw-style security setup. The point is to verify, not redo.

**Steps**:

1. On the Mac mini, log in as `admin` (or open Terminal under admin).
2. Confirm both accounts exist:
   ```bash
   dscl . list /Users | grep -E '^(admin|agent)$'
   ```
   Expected output: two lines, `admin` and `agent`.
3. Confirm `agent` is NOT in the admin group:
   ```bash
   dseditgroup -o checkmember -m agent admin
   ```
   Expected output: `no agent is NOT a member of admin`.
4. Confirm you can switch to `agent` and that its home directory exists:
   ```bash
   su - agent -c 'pwd && whoami'
   ```
   Expected: `/Users/agent` and `agent`.

**If `agent` does not exist**: create it via System Settings → Users &amp; Groups → Add Account. Set it as a Standard (not Administrator) account. Document the password in your password manager. Log in once to initialize the home directory.

**Verification**: All three commands above return expected output.

**Estimated time**: 10 minutes if accounts exist; 20 if creating `agent` from scratch.

---

### Task 4: Install Toolchain on Agent Account

**Outcome**: `git`, `uv`, and `psql` are installed and accessible from the `agent` shell.

**Steps**:

1. Log in as `agent` (or `su - agent`).
2. Install Homebrew if not present (it's per-user on Apple Silicon):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   Follow the post-install instructions to add brew to your shell PATH.
3. Install the required tools:
   ```bash
   brew install git uv libpq
   brew link --force libpq    # makes psql available
   ```
4. Verify each:
   ```bash
   git --version
   uv --version
   psql --version
   ```

**Note on per-user Homebrew**: Apple Silicon installs to `/opt/homebrew` and is global. If your `agent` account doesn't have write access, install Homebrew to `/Users/agent/.brew` instead. The `brew` binary on Apple Silicon doesn't require admin rights for installation in `~/`.

**Verification**: All three version commands return version numbers.

**Estimated time**: 30 minutes (most of it Homebrew install time).

---

### Task 5: Configure Keychain Credentials on Agent Account

**Outcome**: All 8 credentials from the inventory are in `agent`'s login keychain, retrievable via `security find-generic-password`.

**Credential inventory** (from `40-action-layer.md`):

| Keychain item | What it is | Where you got it |
|---|---|---|
| `supabase-service-key` | service_role key | Task 1, step 4 |
| `supabase-anon-key` | anon public key | Task 1, step 4 |
| `supabase-db-password` | database password | Task 1, step 1/4 |
| `supabase-db-url` | direct Postgres URI | Task 1, step 5 |
| `supabase-project-url` | https://...supabase.co | Task 1, step 4 |
| `gemini-api-key` | Gemini API key | aistudio.google.com → API keys |
| `anthropic-api-key` | Anthropic API key | console.anthropic.com → API keys |
| `github-personal-token` | for git operations from agent | github.com → Settings → Developer settings |

(Discord and Buffer tokens are deferred to Phase 3 and Phase 9 respectively.)

**Steps**:

1. As `agent`, run the provided `scripts/keychain_setup.sh`. The script prompts for each credential and stores it (it does not echo or log anything).
   ```bash
   cd ~/agents  # or wherever the repo is cloned in Task 6
   bash scripts/keychain_setup.sh
   ```
2. Run the verifier:
   ```bash
   bash scripts/keychain_verify.sh
   ```
   Expected output: 8 lines, each saying `OK <item-name>`. No `MISSING` lines.

**If you haven't yet obtained Gemini or Anthropic API keys**:
- Gemini: aistudio.google.com → "Get API key" → create a new key for the project. Free tier is fine; usage will be billed once Tartt runs in Phase 4.
- Anthropic: console.anthropic.com → API Keys → Create Key. Set up billing; usage starts in Phase 2 with the cost helper smoke test.

**Verification**: `keychain_verify.sh` returns 8 OK lines.

**Estimated time**: 20 minutes (mostly waiting on API console pages).

---

### Task 6: Apply Schema Migration

**Outcome**: All 18 tables, indexes, and extensions exist in the Supabase database. `verify_schema.sql` returns a clean report.

**Steps**:

1. Clone the repo to `agent`'s home directory:
   ```bash
   # As agent
   cd ~
   gh auth login   # if using gh; otherwise use HTTPS clone with token
   git clone git@github.com:<your-username>/aiadaptive-cos.git agents
   cd agents
   ```
2. Apply migration 0001 via the Supabase SQL Editor (recommended for first migration so you see the output inline):
   - Open the Supabase dashboard → SQL Editor → New query
   - Paste the entire content of `migrations/0001_initial_schema.sql`
   - Run
   - Expect: "Success. No rows returned." If errors, see Troubleshooting below.
3. Apply via `psql` for repeatability (also recommended — bake the muscle memory now, the team will use this path for every future migration):
   ```bash
   export DB_URL=$(security find-generic-password -a $USER -s supabase-db-url -w)
   psql "$DB_URL" -f migrations/verify_schema.sql
   ```
   Expected output: 18 tables listed, 3 extensions confirmed, no FAIL lines.

**Verification**: `verify_schema.sql` runs clean.

**Troubleshooting**:
- *"extension pgvector does not exist"*: Task 1 step 3 was incomplete. Enable from dashboard, retry.
- *"relation 'sources' does not exist" when creating content_items*: the migration creates tables in dependency order. If you see this, the migration has been modified or partially applied. Drop everything and rerun:
  ```sql
  DROP SCHEMA public CASCADE;
  CREATE SCHEMA public;
  GRANT ALL ON SCHEMA public TO postgres, anon, authenticated, service_role;
  ```
  Then rerun the migration.
- *Connection refused via psql*: the connection string from Supabase includes `?sslmode=require`. Don't strip it.

**Estimated time**: 30 minutes.

---

### Task 7: Smoke Test and Snapshot Backup

**Outcome**: A Python script on `agent` reads and writes Supabase. Manual backup snapshot saved.

**Steps**:

1. As `agent`, set up the project-level Python environment using `uv`:
   ```bash
   cd ~/agents
   uv sync   # reads pyproject.toml, creates .venv, installs deps
   ```
2. Run the smoke test:
   ```bash
   uv run python scripts/smoke_test.py
   ```
   Expected output:
   ```
   OK — connected at 2026-05-15T20:30:00Z, 18 tables visible
   Test row inserted to facts (id=1)
   Test row deleted; brain is clean
   ```
3. Run the manual backup:
   ```bash
   bash scripts/snapshot_backup.sh
   ```
   This produces `~/agents/backups/snapshot_phase1_<timestamp>.sql.gz`. Verify it exists and is at least a few KB.

**Verification**: smoke_test.py prints success, backup file exists and is non-empty.

**Estimated time**: 30 minutes.

</tasks>

---

## Risk Register

<risks>

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Supabase free → Pro upgrade timing | Low | Low — $25 vs free is small | Just start on Pro; the backup feature alone is worth it |
| pgvector HNSW indexes slow at first | Low | Low | They're empty in Phase 1; tune after Phase 4 has real volume |
| Apple Silicon Homebrew permissions for `agent` | Medium | Medium — blocks Task 4 | Per-user install in `~/.brew` is the fallback |
| Forgetting to commit pyproject.toml lockfile | Medium | Medium — agent vs laptop env drift | `uv.lock` is committed to git; verify with `git status` after `uv sync` |
| Credentials echoed to shell history | Medium | High — credential leak | `keychain_setup.sh` uses `read -s` for all secrets; verify `history` after running |
| Schema applied to wrong project | Low | High — would require fresh project | Always verify `DB_URL` host before applying migrations: `psql "$DB_URL" -c '\conninfo'` |
| Migration partially applied on first try | Medium | Low — recoverable | The `DROP SCHEMA public CASCADE` recipe in Task 6 troubleshooting handles this |

</risks>

---

## Definition of Done

<dod>

Phase 1 is complete when:

1. All 10 acceptance criteria pass.
2. The repo's `main` branch contains a commit message `"Phase 1: foundation complete"`.
3. You can answer "where is X" for any of: a credential, the schema definition, the smoke test, the architecture documents, the migration history.
4. You have not written any agent code, made any LLM calls, or scheduled any jobs. (If you have, you've drifted into Phase 2 prematurely — revert.)

</dod>

---

## What Phase 2 Will Do With This

Phase 2 (Telemetry Primitives) builds on Phase 1's substrate by:

- Filling in `agents/_lib/runs.py` — the cost-emission helper that wraps all LLM calls.
- Adding `scripts/cost_query.py` for ad-hoc spend inspection.
- Writing the first real `agent_runs` row from a test agent.

No schema changes in Phase 2 (`agent_runs` and `outcomes` are already in migration 0001). No new credentials beyond what Phase 1 stored. No new accounts.

The handoff is clean.

---

## Appendix A: File Contents

The following files are provided alongside this PRD:

- `migrations/0001_initial_schema.sql` — the full schema migration
- `migrations/verify_schema.sql` — idempotent verification query
- `scripts/smoke_test.py` — connectivity smoke test
- `scripts/keychain_setup.sh` — interactive credential storage
- `scripts/keychain_verify.sh` — credential existence check
- `scripts/snapshot_backup.sh` — manual backup with encryption
- `README.md` — root readme for the repo
- `.gitignore` — standard ignore patterns
- `pyproject.toml` — uv-managed Python deps for Phase 1

All are minimal — Phase 1 doesn't need much code. The schema migration is the biggest artifact at ~250 lines.
