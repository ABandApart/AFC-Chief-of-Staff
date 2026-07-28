"""W2 runtime smoke — cognee stand-up on local Postgres (barry-agent).

Configures cognee (dedicated `aiadaptive_cognee` DB, M1 routing), cognifies two
short docs under a `labeled()` block, runs a GRAPH_COMPLETION query, and confirms
the labeling callback wrote `agent_runs` rows. Proves W2's exit criteria:
cognify + graph query succeed on local Postgres, and the ledger sees the calls.

Run from the repo root after `uv sync --group cognee` (barry-agent, where
`anthropic-api-key`, `gemini-api-key`, `db-url` are in keychain):
    uv run python -m agents.test.cognee_smoke

Writes only to `aiadaptive_cognee` (cognee stores) + `agent_runs` (ledger rows,
agent 'cognee'). Prune the smoke dataset before W4 go-live, or recreate the DB.
"""

from __future__ import annotations

import asyncio
import sys

from agents._lib import cognee_setup, creds
from agents._lib.telemetry_context import labeled

DOCS = [
    "Beacon Legal wants contract intake triage removed — Sarah asked for a "
    "fixed-scope build, data kept in their control.",
    "Harbor CPA's month-end reconciliation exceptions are the workflow Marcus "
    "would pay to remove; the memory across calls is the differentiator.",
]

DATASET = "w2_smoke"
QUERY = "What single workflow does each firm want removed, and who asked?"


async def main() -> int:
    cognee_setup.configure_cognee()
    import cognee
    from cognee import SearchType

    print("[1/3] cognify 2 docs (labeled → ledger)...")
    for i, doc in enumerate(DOCS, 1):
        with labeled("cognee", "customer_discovery", correlation_id=f"w2-smoke-{i}"):
            await cognee.add(doc, dataset_name=DATASET)
            await cognee.cognify(datasets=[DATASET])
        print(f"  cognified doc {i}/{len(DOCS)}")

    print("[2/3] GRAPH_COMPLETION query...")
    result = await cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=QUERY)
    print(f"  → {str(result)[:300]}")

    print("[3/3] verify ledger rows (labeling callback / M1)...")
    import psycopg
    db_url = creds.keychain_get("db-url")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT llm_provider, COUNT(*), COALESCE(SUM(usd_cost),0)::float "
                "FROM agent_runs WHERE correlation_kind = 'cognify_run' "
                "AND correlation_id LIKE 'w2-smoke-%' GROUP BY llm_provider"
            )
            rows = list(cur.fetchall())

    print("  agent_runs (cognify_run, this smoke):")
    total = 0
    for provider, n, usd in rows:
        print(f"    {provider:<12} {n:>3} calls  ${usd:.6f}")
        total += n
    if total == 0:
        print("  ❌ FAIL: no ledger rows — M1 routing not capturing cognee's calls")
        return 1
    print(f"  ✅ {total} calls captured — M1 routing works")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
