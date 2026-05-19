"""
smoke_test.py — Phase 1 connectivity verification.

Confirms:
  1. Keychain has the necessary credentials.
  2. The local Postgres instance is reachable from this account.
  3. All 18 tables exist.
  4. A test row can be written and deleted (read/write privileges work).

No LLM calls. No agent_runs writes. This script exists only to prove
the substrate works.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

import psycopg


EXPECTED_TABLES = {
    "agent_runs",
    "approval_queue",
    "buffer_posts",
    "content_items",
    "content_pipeline",
    "dashboard",
    "decisions",
    "facts",
    "follow_ups",
    "icp_signals",
    "interest_signals",
    "meeting_transcripts",
    "outcomes",
    "people",
    "prospects",
    "sources",
    "task_candidates",
    "tasks",
}


def keychain_get(item_name: str) -> str:
    """Fetch a credential from the macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", item_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain item '{item_name}' not found. "
            f"Run scripts/keychain_setup.sh."
        )
    return result.stdout.strip()


def main() -> int:
    print("Phase 1 smoke test")
    print("=" * 50)

    # Step 1: Pull credentials.
    try:
        db_url = keychain_get("db-url")
    except RuntimeError as e:
        print(f"FAIL: {e}")
        return 1

    # Step 2: Connect and run checks.
    try:
        with psycopg.connect(db_url, autocommit=False) as conn:
            with conn.cursor() as cur:
                # Confirm we're talking to a real Postgres.
                cur.execute("SELECT now() AT TIME ZONE 'UTC'")
                ts = cur.fetchone()[0]

                # Inventory tables in public schema.
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                found_tables = {row[0] for row in cur.fetchall()}

                missing = EXPECTED_TABLES - found_tables
                extra = found_tables - EXPECTED_TABLES

                if missing:
                    print(f"FAIL: missing tables: {sorted(missing)}")
                    return 1

                if extra:
                    # Extra tables are not a failure — could be from a future
                    # migration applied ahead of this script. Just note them.
                    print(f"note: extra tables present (ok): {sorted(extra)}")

                print(
                    f"OK    connected at {ts.isoformat()}Z, "
                    f"{len(EXPECTED_TABLES)} expected tables visible"
                )

                # Step 3: Write & delete test row.
                cur.execute(
                    """
                    INSERT INTO facts (content, source_type, source_ref, domain, confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        "Phase 1 smoke test marker — safe to delete",
                        "manual",
                        f"smoke-test-{datetime.now(timezone.utc).isoformat()}",
                        "system",
                        1.0,
                    ),
                )
                test_id = cur.fetchone()[0]
                print(f"OK    test row inserted to facts (id={test_id})")

                cur.execute("DELETE FROM facts WHERE id = %s", (test_id,))
                conn.commit()
                print("OK    test row deleted; brain is clean")

    except psycopg.OperationalError as e:
        print(f"FAIL: cannot connect to Postgres: {e}")
        return 1
    except Exception as e:
        print(f"FAIL: unexpected error: {type(e).__name__}: {e}")
        return 1

    print("=" * 50)
    print("Phase 1 substrate verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
