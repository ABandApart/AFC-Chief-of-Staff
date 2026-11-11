---
name: docent-export
schedule: "30 2 * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.docent.export
description: Push themes and market segments to docent's Supabase database, after a PII check (PRD-docent-export.md).
---

# Docent export

Runs `agents/docent/export.py` every night at 02:30. Each run reads
`export.themes` and `export.market_segments`, checks every value for PII, and
replaces docent's `richmond.themes` and `richmond.market_segments` in one
transaction. A PII match writes nothing and records a `failed` run.

**What activation starts:** nothing billable. Three SQL reads on the Mac mini,
one transaction of a few dozen rows to Supabase, one `agent_runs` row
(`agent_name='docent-export'`, `usd_cost=0`), and one heartbeat ping.

**Before activating:** docent's Supabase project exists with
`migrations/docent/0001_richmond_schema.sql` applied, `docent-export-db-url` is
in barry-agent's keychain, and a `--manual` run has succeeded.

Health check any time:

```
uv run python -m agents.docent.export --dry-run   # row counts and the PII result
psql -U barry_agent aiadaptive_cos -c "SELECT started_at, status, error_text FROM agent_runs WHERE agent_name = 'docent-export' ORDER BY id DESC LIMIT 3;"
```
