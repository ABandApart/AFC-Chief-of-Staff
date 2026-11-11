# Docent export: nightly push of themes and market segments — PRD & Build Spec

<doc:meta>
  <doc:theme>A one-way, allowlist-only export from the local brain to docent's Supabase database</doc:theme>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>DRAFT 2026-11-11. BUILT 2026-11-11 (migration 0037 applied; loop `docent-export` shipped disabled). Not deployed: needs the docent Supabase project, the `docent-export-db-url` keychain item, and a first manual run.</doc:status>
  <doc:depends_on>`migrations/0008_brain_reader_and_read_views.sql` (the read-only role), `loops/README.md` (activation), `80-telemetry-layer.md` (heartbeat)</doc:depends_on>
</doc:meta>

## Problem

Docent is the operator's web app, hosted on Supabase. It must show broad themes and market segments from the CoS. It must never be able to read details or personal information (PII).

`aiadaptive_cos` and `aiadaptive_cognee` stay on the Mac mini (operator, 2026-11-11). The Supabase free tier allows 2 projects, which rules out hosting both brains plus docent.

## S1. Outcome

When this is done:

1. Every night at 02:30 local, docent's `richmond.themes` and `richmond.market_segments` tables match `export.themes` and `export.market_segments` in `aiadaptive_cos`. One row in `richmond.export_runs` records each push.
2. Docent's credentials can read only the `richmond` schema's three tables. No credential that docent holds can reach the Mac mini.
3. A run whose data matches a known person or company name, an email address, a phone number, or a URL writes nothing to docent. It records a `failed` row in `agent_runs` and pings the heartbeat failure check.

## Non-goals

- Migrating `aiadaptive_cos` or `aiadaptive_cognee` to the cloud.
- Exporting anything from Cognee. Its `InterestSignal` labels appear to index the same topics as `interest_signals` (an inference, not confirmed).
- Exporting outcomes, OKRs, prospects, or any free-text field (`rationale`, `description`, `summary`, `problem`).
- Incremental sync. The data is a few dozen rows, so each run replaces both tables.

## Settled decisions (operator, 2026-11-11)

| # | Decision |
|---|---|
| DX-1 | Source databases stay on the Mac mini. Docent's Supabase project holds the exported data. |
| DX-2 | Export only themes and market segments. |
| DX-3 | Push once a day, overnight. |
| DX-4 | Supabase free tier. Region `us-east-2` (Ohio), the closest to the operator. |

## Design

**Allowlist.** Migration 0037 creates the `export` schema with two views. Each view names its columns. The job reads only these views, through `brain_reader`, so a new column in a source table never reaches docent.

| Docent table | Source | Columns |
|---|---|---|
| `richmond.themes` | `interest_signals` | `topic_label`, `weight`, `last_reinforced_at` |
| `richmond.market_segments` | `outreach_segments` where `in_list`, joined to `outreach_segment_scores` | `label`, the six score columns, `rated_at` |

`agents/docent/export.py` pins the same column lists in `COLUMNS`. A run fails when a view's columns differ from `COLUMNS`, and `tests/test_docent_export.py` pins `COLUMNS`. Adding a published field takes three deliberate edits: the view, `COLUMNS`, and the test.

**PII gate.** Before any write, the job checks every exported string for email, phone, and URL patterns. It also checks for whole-word matches against the names in `people`, `prospects`, `outreach_targets`, and `clients`, read over the `barry_agent` connection. Any match stops the run. The gate fails closed: a false positive costs one night's export.

**Push.** One transaction on docent deletes and reinserts both tables and adds the `export_runs` row. A deletion or redaction at the source disappears from docent on the next run.

**Docent side.** `migrations/docent/0001_richmond_schema.sql` creates the schema, the tables, and the `richmond_export` writer role. The writer can select, insert, and delete in `richmond` only. Docent's `authenticated` role gets `SELECT` through row-level security. The job connects with `prepare_threshold=None`, so it works through either Supabase pooler.

**Ledger and alerting.** Each non-dry run writes one `agent_runs` row (`agent_name='docent-export'`, `usd_cost=0`). Success pings the `cos-docent-export` heartbeat; failure calls `ping_fail`.

## S3. Verification

1. `uv run pytest tests/test_docent_export.py` passes. It covers the PII gate, the column lock, the push SQL, and the grant rules in the docent setup SQL.
2. Builder side (done 2026-11-11): the setup SQL applied to a scratch Postgres 17 cluster, plus a real run of `run_once` from `aiadaptive_cos` into it, as `richmond_export`.
3. Runtime: `uv run python -m agents.docent.export --dry-run` as barry-agent prints row counts and `PII check: clean`. Then one manual run, and a `select * from richmond.export_runs` in the Supabase SQL editor.

## Open decisions

- **OD-1. Who reads docent's data.** The setup SQL grants read access to `authenticated` only. If docent pages are public, grant `anon` too. The exported data carries no PII by design, so this is a product choice, not a safety one.
- **OD-2. Hypothesis segments.** The 2 segments with `in_list = false` are excluded. Including them is a one-line change to `export.market_segments`.

## Known gap (2026-11-11)

`interest_signals` has 0 rows and `outreach_segment_scores` has 0 rows. The first export carries 6 segment labels with no scores and no themes. Docent stays mostly empty until the theme-tracking and segment-scoring work fills those tables.
