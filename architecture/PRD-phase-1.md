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

By the end of Phase 1, a Python script running on the Mac mini `barry-agent` account can connect to a locally-hosted PostgreSQL 17 instance, read and write all 18 tables defined in the v1 schema. No automation, no LLM calls, no Discord bot — just the plumbing.

This phase is deliberately small. Its job is to make Phase 2 (telemetry primitives) possible without retrofitting.

**Note:** the brain was originally specified as hosted Supabase. We pivoted to local Postgres 17 before any infrastructure was provisioned; see `70-build-order.md` decision log and the Deviations section of the root README for rationale.

---

## Goal &amp; Non-Goals

<goals>

**Goal**: A working substrate. Specifically:

1. Local Postgres 17 cluster running under Homebrew's LaunchAgent with `vector` + `pg_trgm` enabled and `aiadaptive_cos` database owned by the `barry_agent` role.
2. Schema migration 0001 applied: all 18 tables, indexes, and extensions.
3. Private GitHub repo `ABandApart/AFC-Chief-of-Staff` initialized with directory scaffolding.
4. macOS account separation operational: `barry-admin` (build) and `barry-agent` (run).
5. All credentials stored in `barry-agent` Keychain, retrievable from Python.
6. Repo cloned to `barry-agent` account at `~/agents/`.
7. Connectivity test passes: a Python script on `barry-agent` reads and writes the local Postgres.

</goals>

<non_goals>

**Not in this phase**:

- No LLM calls of any kind. (The cost helper that wraps all LLM calls is Phase 2.)
- No Discord bot. (Phase 3.)
- No scheduled jobs in launchd. (Earliest is Phase 3 for the briefing skeleton.)
- No agent code beyond the connectivity smoke test.
- No backups beyond the manual one this PRD specifies at the end.
- No multi-environment setup (dev/staging/prod). Single local Postgres database; the brain doesn't need staging at this scale.

</non_goals>

---

## Acceptance Criteria

<acceptance>

The phase is done when all of the following are true. Each is independently verifiable.

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | Local Postgres 17 running with `vector` and `pg_trgm` extensions enabled in `aiadaptive_cos` | `brew services list \| grep postgresql@17` shows started; `psql aiadaptive_cos -c '\dx'` lists both extensions |
| AC2 | All tables in migration 0001 exist with correct columns and indexes | Run `verify_schema.sql` (provided) — output shows 18 tables, no errors |
| AC3 | Private GitHub repo exists with the directory structure | `git ls-tree -d HEAD` from a fresh clone shows architecture/, migrations/, agents/, agents/_lib/, cli/, scripts/, checklists/ |
| AC4 | `barry-admin` and `barry-agent` macOS accounts both exist and are separated | `dscl . list /Users \| grep -E '^barry-(admin\|agent)$'` returns both |
| AC5 | All 8 required credentials are in `barry-agent`'s keychain | `bash scripts/keychain_verify.sh` (as `barry-agent`) returns 8 `OK` lines, 0 `MISSING` |
| AC6 | Repo cloned to `/Users/barry-agent/agents/` | Directory exists; `git remote -v` points at `github.com:ABandApart/AFC-Chief-of-Staff` |
| AC7 | `uv` and `psql` are reachable from the `barry-agent` shell | `uv --version` and `psql --version` both succeed; `uv run python -c "print('ok')"` returns `ok` |
| AC8 | Smoke test connects to local Postgres from `barry-agent` account | `uv run python scripts/smoke_test.py` returns `OK connected at <timestamp>, 18 expected tables visible` |
| AC9 | Manual snapshot backup taken | `~/agents/backups/phase1_*.sql.gz` exists and is > 0 bytes |
| AC10 | README.md committed to the repo documenting the layout | `cat README.md` displays the layout section and current Deviations note |

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

- Local Postgres 17 cluster at `/opt/homebrew/var/postgresql@17/` with `aiadaptive_cos` database, `barry_agent` role, and both extensions enabled.
- Keychain entries on the `barry-agent` account: `db-url`, `gemini-api-key`, `anthropic-api-key`, `github-personal-token`.
- Time Machine exclusion configured for `/opt/homebrew/var/postgresql@17/`.
- A manual `.sql.gz` snapshot in `~/agents/backups/`.

---

## Task Breakdown (sequenced)

<tasks>

The seven tasks below are sequenced so each one's prerequisites are complete. Estimated total: 6–8 hours over 3–4 sittings.

### Task 1: Install local Postgres 17

**Outcome**: PostgreSQL 17 running on the Mac mini under a Homebrew-managed LaunchAgent, application database `aiadaptive_cos` created, role `barry_agent` owns the database, `vector` (pgvector) and `pg_trgm` extensions enabled, password for `barry_agent` stored in keychain.

**Why local (deviation from baseline PRD)**: see `70-build-order.md` decision log entry "Reversed: local Postgres 17 on Mac mini for Phase 1–5". Phase 6 will revisit reachability.

**Steps** (run as `barry-admin`):

1. Install Postgres 17 + pgvector via Homebrew:
   ```bash
   brew install postgresql@17 pgvector
   ```
2. Start the service. Homebrew registers a LaunchAgent under `barry-admin` that survives logout (via Fast User Switching) and reboot (so long as `barry-admin` auto-logs-in or stays logged in):
   ```bash
   brew services start postgresql@17
   ```
3. Verify cluster is reachable. The Homebrew `initdb` creates a superuser role matching the OS user that ran it (so `barry-admin` is auto-created as cluster superuser):
   ```bash
   /opt/homebrew/opt/postgresql@17/bin/psql postgres -c '\conninfo'
   ```
4. Generate a 32-character random password for the `barry_agent` DB role and stash it in keychain (never echoed to terminal or shell history):
   ```bash
   PASS=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32)
   security add-generic-password -a "$USER" -s cos-db-password-staging \
       -w "$PASS" -T "" -U
   unset PASS
   ```
5. Create the application database, role, and extensions:
   ```bash
   PASS=$(security find-generic-password -a "$USER" -s cos-db-password-staging -w)
   createdb aiadaptive_cos
   psql aiadaptive_cos <<SQL
     CREATE ROLE barry_agent WITH LOGIN PASSWORD '$PASS';
     ALTER DATABASE aiadaptive_cos OWNER TO barry_agent;
     GRANT ALL PRIVILEGES ON DATABASE aiadaptive_cos TO barry_agent;
     CREATE EXTENSION IF NOT EXISTS vector;
     CREATE EXTENSION IF NOT EXISTS pg_trgm;
   SQL
   unset PASS
   ```
6. Construct the full DB URL and store under the keychain item name agents expect (`db-url`):
   ```bash
   PASS=$(security find-generic-password -a "$USER" -s cos-db-password-staging -w)
   security add-generic-password -a "$USER" -s db-url \
       -w "postgresql://barry_agent:$PASS@localhost:5432/aiadaptive_cos" \
       -T "" -U
   unset PASS
   ```
   The same `db-url` item will be copied to `barry-agent`'s keychain in Task 5.

7. **Add the live data directory to Time Machine exclusions**: System Settings → General → Time Machine → Options → "Exclude these items" → add `/opt/homebrew/var/postgresql@17/`. Reason: Postgres data files are constantly mid-write, so TM file-by-file snapshots produce inconsistent state. The proper backup is `scripts/snapshot_backup.sh` (run in Task 7); its `.sql.gz` output lives in `~/agents/backups/` which TM **does** back up.

**Verification**: All four checks pass:
- `brew services list` shows `postgresql@17` as `started`
- `psql postgres -c '\conninfo'` connects and reports current user/database
- `psql aiadaptive_cos -c '\dx'` lists both `vector` and `pg_trgm` extensions
- `security find-generic-password -a "$USER" -s db-url -w | grep -c '@localhost:5432/aiadaptive_cos'` returns 1

**Estimated time**: 25 minutes.

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

**Outcome**: All 8 required credentials are in `barry-agent`'s login keychain, retrievable via `security find-generic-password`.

**Credential inventory** (post-pivot — local Postgres + per-agent Anthropic keys):

| Keychain item | What it is | Where you got it |
|---|---|---|
| `db-url` | full Postgres URI `postgresql://barry_agent:PW@localhost:5432/aiadaptive_cos` | Task 1, step 6 (copied from `barry-admin` keychain) |
| `gemini-api-key` | Gemini API key (Tartt — Phase 4 news scraping + embeddings) | aistudio.google.com → API keys |
| `anthropic-key-ted` | Anthropic key for Ted (Phase 11 health checks + alert summarization) | console.anthropic.com → API Keys |
| `anthropic-key-keeley-strategy` | Anthropic key for Keeley Strategy (Phase 8 content triage) | console.anthropic.com → API Keys |
| `anthropic-key-keeley-content` | Anthropic key for Keeley Content (Phase 8 drafting) | console.anthropic.com → API Keys |
| `anthropic-key-roy-kent` | Anthropic key for Roy Kent (Phase 6 inbound qualifier) | console.anthropic.com → API Keys |
| `anthropic-key-nate-shelley` | Anthropic key for Nate Shelley (Phase 10 ICP synthesis) | console.anthropic.com → API Keys |
| `anthropic-key-higgins` | Anthropic key for Higgins (Phase 11 weekly dashboard) | console.anthropic.com → API Keys |
| `github-personal-token` *(optional)* | only needed if `barry-agent` clones via HTTPS instead of sharing `barry-admin`'s SSH key | github.com → Settings → Developer settings |

Per-agent Anthropic keys give spend attribution at the provider side, complementing the per-call `agent_runs.agent_name` ledger that Phase 2's cost helper writes. The cost helper will look up the right key by agent name. See decision-log entry "Per-agent Anthropic API keys" in `70-build-order.md`.

Anthropic keys for agents not yet listed (`sam`, `briefing`, `capture`, `meeting-processor`, etc.) can be created when their respective phases come up; they don't block Phase 1. Discord and Buffer tokens are deferred to Phase 3 and Phase 9 respectively.

**Steps** (orchestrated from `barry-admin`):

All 8 required items are first staged in `barry-admin`'s keychain during Task 1 + the conversation in which keys are received. They are then copied into `barry-agent`'s keychain in one sudo'd bulk operation:

1. From a `barry-admin` terminal, bulk-copy every required item:
   ```bash
   for name in db-url gemini-api-key \
               anthropic-key-ted anthropic-key-keeley-strategy \
               anthropic-key-keeley-content anthropic-key-roy-kent \
               anthropic-key-nate-shelley anthropic-key-higgins; do
       VAL=$(security find-generic-password -a "$USER" -s "$name" -w)
       sudo -u barry-agent security add-generic-password \
           -a barry-agent -s "$name" -w "$VAL" -T "" -U
       unset VAL
   done
   ```
   (sudo prompts once for `barry-admin`'s password; subsequent items reuse the credential timestamp.)

2. Run the verifier as `barry-agent`:
   ```bash
   sudo -u barry-agent bash /Users/barry-admin/code/aiadaptive-cos/scripts/keychain_verify.sh
   ```
   Expected: 8 `OK` lines under "Required items", `absent github-personal-token` under "Optional items", `exit 0`.

3. Once verified, delete the staged copies from `barry-admin`'s keychain (the values now live only in `barry-agent`'s — the safer location since `barry-agent` is the runtime account):
   ```bash
   for name in db-url gemini-api-key \
               anthropic-key-ted anthropic-key-keeley-strategy \
               anthropic-key-keeley-content anthropic-key-roy-kent \
               anthropic-key-nate-shelley anthropic-key-higgins; do
       security delete-generic-password -a "$USER" -s "$name"
   done
   ```

**If new Anthropic keys need to be added later** (Sam, Briefing, Capture, Meeting Processor in Phases 3/7/8): `barry-agent` runs `bash scripts/keychain_setup.sh` interactively to add one item, OR the same bulk-copy pattern is used from `barry-admin`.

**Verification**: `keychain_verify.sh` returns 8 OK lines (required), exit 0.

**Estimated time**: 10 minutes (assuming keys are already gathered).

---

### Task 6: Apply Schema Migration

**Outcome**: All 18 tables, indexes, and extensions exist in `aiadaptive_cos`. `verify_schema.sql` returns a clean report.

**Steps** (run as `barry-agent`):

1. Clone the repo to `barry-agent`'s home directory using the PAT from keychain:
   ```bash
   TOKEN=$(security find-generic-password -a "$USER" -s github-personal-token -w)
   git clone "https://${TOKEN}@github.com/ABandApart/AFC-Chief-of-Staff.git" ~/agents
   unset TOKEN
   cd ~/agents
   ```
2. Apply migration 0001 via `psql`:
   ```bash
   export DB_URL=$(security find-generic-password -a "$USER" -s db-url -w)
   psql "$DB_URL" -f migrations/0001_initial_schema.sql
   ```
   Expect: a sequence of `CREATE TABLE` / `CREATE INDEX` / `COMMIT` lines and no `ERROR:` lines.
3. Verify:
   ```bash
   psql "$DB_URL" -f migrations/verify_schema.sql
   ```
   Expected output: 18 tables listed, both extensions confirmed, dashboard singleton seeded, no `FAIL` lines.

**Verification**: `verify_schema.sql` runs clean.

**Troubleshooting**:
- *"extension pgvector does not exist"*: Task 1 step 5 (or `brew install pgvector`) was incomplete. Re-run `CREATE EXTENSION vector;` from `psql aiadaptive_cos`.
- *"role 'barry_agent' is not permitted to log in"*: the role was created without `LOGIN`. Run `ALTER ROLE barry_agent LOGIN;` from a `barry-admin` `psql postgres` session.
- *Partial migration on first try*: drop and recreate the schema, then rerun:
  ```sql
  DROP SCHEMA public CASCADE;
  CREATE SCHEMA public;
  GRANT ALL ON SCHEMA public TO barry_agent;
  ```
- *"connection refused on port 5432"*: `brew services list` should show `postgresql@17` started under `barry-admin`. If it isn't, `brew services start postgresql@17`.

**Estimated time**: 20 minutes.

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
| pgvector HNSW indexes slow at first | Low | Low | They're empty in Phase 1; tune after Phase 4 has real volume |
| Postgres not running when `barry-agent` logs in alone | Medium | High — agents can't reach the brain | `brew services` installs a LaunchAgent scoped to `barry-admin`; keep `barry-admin` logged in via Fast User Switching, or migrate to a system-level LaunchDaemon in Phase 12 |
| `pgvector` version mismatch with `postgresql@17` | Low | Medium — `CREATE EXTENSION` fails | Use `brew install pgvector` (not building from source) so brew handles the version pinning |
| Apple Silicon Homebrew permissions for `barry-agent` | Low | Low — only running existing binaries, not installing | Binaries in `/opt/homebrew/bin` are world-executable; `barry-agent` only needs PATH set |
| Forgetting to commit pyproject.toml lockfile | Medium | Medium — env drift between admin and agent | `uv.lock` is committed to git; verify with `git status` after `uv sync` |
| Credentials echoed to shell history | Medium | High — credential leak | `keychain_setup.sh` uses `read -s` for all secrets; password generation uses `unset PASS` immediately after use |
| Schema applied to wrong database | Low | High — recovery requires drop + reapply | Always verify `DB_URL` host/db before applying migrations: `psql "$DB_URL" -c '\conninfo'` |
| Migration partially applied on first try | Medium | Low — recoverable | The `DROP SCHEMA public CASCADE` recipe in Task 6 troubleshooting handles this |
| Time Machine snapshots inconsistent live Postgres files | Medium | High — silent corruption on restore | Exclude `/opt/homebrew/var/postgresql@17/` from TM (Task 1, step 7); rely on `pg_dump` snapshots in `~/agents/backups/` for restores |
| Mac mini disk failure with no offsite backup | Low | Catastrophic — total data loss | Phase 1 has TM as the only offsite path; Phase 12 hardening adds cloud-sync of `~/agents/backups/` |

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
