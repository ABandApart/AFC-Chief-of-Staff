"""Create the master OKR document and the dashboard in Notion (`PRD-notion-integration.md`).

    uv run python -m cli.notion_setup --dry-run      # read-only: print the plan
    uv run python -m cli.notion_setup                # create what's missing
    uv run python -m cli.notion_setup --seed-q4      # …and enter the Q4 2026 OKRs once
    uv run python -m cli.notion_setup --resume-seed  # finish a seed that failed partway

Under the shared page "AFC Richmond Chief-of-Staff" it creates the page
"OKRs — Master" with three linked databases: Objectives, Key Results, and
Initiatives (N2). Beside it go the To-Do database (N3), the dashboard's KR
Scorecard, Initiative Status, and Weekly Snapshots databases, and the current
quarter's dashboard page with its linked views (N4). Safe to re-run: it creates
only what is missing, never edits a database that exists, and seeds only into an
empty Objectives database.

Run as barry-agent: it needs keychain `notion-api-token` and `db-url` (the ids
it creates are recorded in `notion_sync`, entity_table `notion_schema`).
Logic lives in `agents/notion/setup.py`; this is the thin caller.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from agents._lib import db, kr_metrics
from agents._lib.notion_client import NotionClient, NotionError
from agents.notion import dashboard_page
from agents.notion.setup import SYNC_TABLE, SetupError, SetupResult, run_setup


def _record(key: str, notion_id: str) -> None:
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, last_pushed_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (entity_table, entity_id)
            DO UPDATE SET notion_page_id = EXCLUDED.notion_page_id, last_pushed_at = now()
            """,
            (SYNC_TABLE, key, notion_id),
        )


def _dashboard(client: Any, r: SetupResult, root: str, sources: dict[str, str],
               dry_run: bool) -> None:
    quarter = kr_metrics.quarter_of(date.today())
    lookup, record = dashboard_page.recorder(db.connection, quarter)
    dashboard_page.ensure_page(client, root=root, quarter=quarter, sources=sources,
                               lookup=lookup, record=record, say=r.say, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the master OKR document in Notion.")
    parser.add_argument("--dry-run", action="store_true",
                        help="read only: print what would be created")
    parser.add_argument("--seed-q4", action="store_true",
                        help="enter the Q4 2026 objectives and KRs (only into an empty database)")
    parser.add_argument("--resume-seed", action="store_true",
                        help="create only the seed rows whose names are missing "
                             "(after a seed failed partway)")
    parser.add_argument("--root-id", help="id of the shared root page, if its title is ambiguous")
    args = parser.parse_args()

    try:
        result = run_setup(
            NotionClient.from_keychain(), record=_record, root_id=args.root_id,
            dry_run=args.dry_run, seed=args.seed_q4, resume_seed=args.resume_seed,
            dashboard=_dashboard,
        )
    except (RuntimeError, SetupError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    print("\n".join(result.lines))
    return 1 if result.drift else 0


if __name__ == "__main__":
    sys.exit(main())
