"""Phase 2 smoke test agent.

Exercises the cost helper end-to-end with **real** API calls:
  - 5 Claude Haiku 4.5 calls (each summarizes one short paragraph)
  - 5 Gemini 2.5 Flash calls (same task)

After running:
  - Verifies 10 rows landed in agent_runs with non-null tokens/cost
  - Triggers G1 (token cap exceeded) with a deliberately tiny cap
  - Prints final summary + spend breakdown

Run from the `barry-agent` account where the keychain holds the agent keys:
    uv run python -m agents.test.run_smoke

Budget: under $0.01 total spend. Phase-2-smoke daily ceiling is $0.50.

This script is removed (or its daily ceiling is dropped to $0.00) once
Phase 2 is closed and real agents start running.
"""

from __future__ import annotations

import sys

import psycopg

from agents._lib.runs import TokenCapExceeded, _keychain_get, agent_run

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
    "enforces three guards: per-run token cap, per-day spend ceiling, and "
    "anomaly detection.",

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
            max_input_tokens=500,
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
            max_input_tokens=500,
            max_output_tokens=100,
        )


def trigger_token_cap_exceeded() -> None:
    """Deliberately exceed a token cap to prove G1 enforcement."""
    long_text = "Lorem ipsum dolor sit amet. " * 200  # ~1000 tokens
    try:
        with agent_run(
            "phase-2-smoke",
            "infrastructure",
            correlation_id="smoke-g1-test",
            correlation_kind="smoke_test",
        ) as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": long_text}],
                model="claude-haiku-4-5",
                max_input_tokens=50,  # impossibly low
                max_output_tokens=50,
            )
        print("  FAIL: G1 should have raised TokenCapExceeded")
    except TokenCapExceeded as e:
        print(f"  OK   G1 raised TokenCapExceeded ({e})")


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
    print("[1/3] Running 5 Anthropic + 5 Gemini calls...")
    for i, paragraph in enumerate(PARAGRAPHS, 1):
        a_result = summarize_with_anthropic(paragraph, i)
        print(f"  Anthropic call {i}/5: {a_result[:70]}...")
        g_result = summarize_with_gemini(paragraph, i)
        print(f"  Gemini    call {i}/5: {g_result[:70]}...")

    print()
    print("[2/3] Triggering G1 (per-run token cap exceeded)...")
    trigger_token_cap_exceeded()

    print()
    print("[3/3] Verifying agent_runs rows...")
    ok = verify_rows_in_db(expected_count=11)  # 10 + 1 G1 row

    print()
    if ok:
        print("✅ Phase 2 smoke test PASSED")
        return 0
    else:
        print("❌ Phase 2 smoke test FAILED — expected >= 11 rows")
        return 1


if __name__ == "__main__":
    sys.exit(main())
