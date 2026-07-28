# Cognee spike harness (THROWAWAY)

Scopes/thresholds: `architecture/SPIKE-cognee-eval-2026-07.md`.
This dir lives only on branch `spike/cognee` and is deleted after the go/no-go call.

## What barry-admin already did (steps 1–2)
- Branch `spike/cognee`.
- Scratch DB **`cognee_spike`** created, owned by `barry_agent`, `vector` + `pg_trgm` enabled.
  - ⚠️ **Q1 pre-signal:** Apache **AGE is NOT in `pg_available_extensions`** on this
    box (only `vector`, `pg_trgm`). If cognee's Postgres graph backend needs AGE,
    that's a Q1 🟡/🔴 before you even run — the harness records the exact error.
- `spike` dependency group in `pyproject.toml` (`cognee[postgres]==1.4.0`), not synced by default.
- `spike/run_cognee.py` + 5 `sample_docs/`.

## Step 3 — run it (barry-agent, has the keys + db-url)
```bash
cd ~/agents
git fetch && git checkout spike/cognee
uv sync --group spike          # heavy tree — this install time/size is itself Q5 data
/usr/bin/time -l uv run python -m spike.run_cognee   # -l gives peak RSS for Q5
```
The script prints a `SPIKE FINDINGS` block — paste it into the SPIKE doc's FINDINGS table.

### If cognify errors immediately on the graph backend (Q1)
Edit `GRAPH_DATABASE_PROVIDER` (env or `configure_cognee`) and retry, in order:
`postgres` → `pgsql` → `networkx` → `kuzu`. Record which value (if any) worked —
needing `kuzu` means the single-Postgres premise fails → Q1 🟡.

### Cross-check Q2 against the real bill
After the run, read the Anthropic + Google dashboards for the spend delta and
compare to the harness's `total spend` line (local price map ≈, dashboard = truth).

## Teardown (either profile, after findings recorded)
```bash
dropdb cognee_spike
git checkout main && git branch -D spike/cognee     # nothing here ships
```
The harness writes ONLY to `cognee_spike` (label rows → scratch `spike_runs`),
never the production `agent_runs` ledger.
