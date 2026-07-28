"""Phase 2 smoke test agent.

Exercises the cost helper end-to-end with **real** API calls:
  - 5 Claude Haiku 4.5 calls (each summarizes one short paragraph)
  - 5 Gemini 2.5 Flash calls (same task)

After running, verifies 10 rows landed in agent_runs with non-null tokens/cost.

Run from the `barry-agent` account where the keychain holds `anthropic-api-key`,
`gemini-api-key`, and `db-url`:
    uv run python -m agents.test.run_smoke

Budget: under $0.01 total spend. Phase-2-smoke daily ceiling is $0.50.

(Phase 3.7 / W1.2: the old G1 token-cap step was removed — pre-flight per-call
refusal is deprecated with the cognee pivot.)
"""

from __future__ import annotations

import sys

import psycopg

from agents._lib.runs import _keychain_get, agent_run

# Short paragraphs to summarize. Bounded inputs keep the smoke cost minimal.
PARAGRAPHS = [
    "The AI Adaptive Chief of Staff system is a persistent operational layer "
    "built on four separable architectural layers: channel, action, memory, "
    "and telemetry. Each layer can evolve independently.",

    "The brain is a Postgres-backed memory store with pgvector for semantic "
    "recall. Selective vectorization keeps cost predictable: only content "
    "that benefits from embedding similarity gets embedded.",

    "Agents run on a Mac mini under macOS account separation. The barry-agent "
    "user runs scheduled launchd jobs and event-triggered work. The barry-admin "
    "user is for building, committing, and operational oversight.",

    "Telemetry is the fourth architectural layer, not an afterthought. Every "
    "LLM call goes through a cost helper that writes one row to agent_runs and "
    "enforces a soft daily spend breaker.",

    "The north star is sustainable long-term contract engagements. Every "
    "workflow ties to at least one of three key results: new engagements per "
    "quarter, dollar value per engagement, and project-to-maintenance conversion.",
]


def summarize_with_anthropic(text: str, idx: int) -> str:
    """Summarize one paragraph via Claude Haiku 4.5."""
    with agent_run(
        "phase-2-smoke",
        "infrastructure",
        correlation_id=f"smoke-anthropic-{idx}",
        correlation_kind="smoke_test",
    ) as run:
        return run.call_anthropic(
            messages=[
                {
                    "role": "user",
                    "content": f"Summarize this in one sentence:\n\n{text}",
                }
            ],
            model="claude-haiku-4-5",
            max_output_tokens=100,
        )


def summarize_with_gemini(text: str, idx: int) -> str:
    """Summarize one paragraph via Gemini 2.5 Flash."""
    with agent_run(
        "phase-2-smoke",
        "infrastructure",
        correlation_id=f"smoke-gemini-{idx}",
        correlation_kind="smoke_test",
    ) as run:
        return run.call_gemini(
            prompt=f"Summarize this in one sentence:\n\n{text}",
            model="gemini-2.5-flash",
            max_output_tokens=100,
        )


def verify_rows_in_db(expected_count: int) -> bool:
    """Check that the expected number of phase-2-smoke rows exist today."""
    db_url = _keychain_get("db-url")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    status,
                    COUNT(*) AS n,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(usd_cost), 0)::float AS usd
                FROM agent_runs
                WHERE agent_name = 'phase-2-smoke'
                  AND correlation_kind = 'smoke_test'
                  AND started_at >= NOW() - INTERVAL '10 minutes'
                GROUP BY status
                ORDER BY status
                """
            )
            rows = list(cur.fetchall())

    print()
    print("=== Rows written to agent_runs (last 10 min, this smoke run) ===")
    total_n = 0
    total_usd = 0.0
    for status, n, inp, out, usd in rows:
        print(f"  {status:<25} {n:>4} rows  {inp:>6} in {out:>6} out  ${usd:.6f}")
        total_n += n
        total_usd += usd
    print(f"  {'TOTAL':<25} {total_n:>4} rows  total ${total_usd:.6f}")

    return total_n >= expected_count


def main() -> int:
    print("=" * 60)
    print("Phase 2 cost-helper smoke test")
    print("=" * 60)
    print()
    print("[1/2] Running 5 Anthropic + 5 Gemini calls...")
    for i, paragraph in enumerate(PARAGRAPHS, 1):
        a_result = summarize_with_anthropic(paragraph, i)
        print(f"  Anthropic call {i}/5: {a_result[:70]}...")
        g_result = summarize_with_gemini(paragraph, i)
        print(f"  Gemini    call {i}/5: {g_result[:70]}...")

    print()
    print("[2/2] Verifying agent_runs rows...")
    ok = verify_rows_in_db(expected_count=10)

    print()
    if ok:
        print("✅ Phase 2 smoke test PASSED")
        return 0
    else:
        print("❌ Phase 2 smoke test FAILED — expected >= 10 rows")
        return 1


if __name__ == "__main__":
    sys.exit(main())
