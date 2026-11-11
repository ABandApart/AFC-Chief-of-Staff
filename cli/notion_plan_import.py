"""Import a plan page from Notion into the CoS (`PRD-notion-integration.md` N7).

    uv run python -m cli.notion_plan_import <page-url-or-id> --dry-run
    uv run python -m cli.notion_plan_import <page-url-or-id>
    uv run python -m cli.notion_plan_import <page-url-or-id> --from 2026-11-23

The dry run prints each day's task count and minutes and writes nothing; check its
total against the page. A real run makes this the active plan (retiring any other)
and replaces the items of every day that has no task yet. `--from` skips earlier
days, for a cutover after the plan's first day. The `notion-plan-daily` loop turns
each day's items into tasks. Run as barry-agent.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date

from agents._lib import db
from agents._lib.notion_client import NotionClient, NotionError, title_of
from agents.notion import plan_import

_ID = re.compile(r"([0-9a-f]{32})|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def page_id(arg: str) -> str:
    """The page id in a Notion URL or id (the last 32-hex run)."""
    found = _ID.findall(arg.lower().split("?")[0])
    if not found:
        raise ValueError(f"no Notion page id in {arg!r}")
    raw = "".join(found[-1]).replace("-", "")
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a Notion plan page.")
    parser.add_argument("page", help="the plan page's URL or id")
    parser.add_argument("--from", dest="from_day", type=date.fromisoformat,
                        help="skip plan days before this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print each day's tasks and minutes; write nothing")
    args = parser.parse_args()

    try:
        pid = page_id(args.page)
        client = NotionClient.from_keychain()
        title = title_of(client.request("GET", f"/pages/{pid}"))
        blocks = plan_import.read_blocks(client, pid)
        with db.connection() as conn:
            plan = plan_import.parse(title, blocks, plan_import.active_initiatives(conn))
        if plan.errors:
            print(f"FAIL  {len(plan.errors)} problem(s) in {title!r}; nothing imported:")
            for e in plan.errors:
                print(f"      - {e}")
            return 1
        print(f"OK    {title!r}: {plan.starts_on} to {plan.ends_on}")
        for line in plan_import.report(plan):
            print(f"      {line}")
        if args.dry_run:
            return 0
        with db.connection() as conn:
            c = plan_import.save(conn, plan, pid, from_day=args.from_day)
        print(f"OK    imported {c['items']} task(s) over {c['days']} day(s); "
              f"{c['kept_days']} day(s) already started were left as they are; "
              f"{c['skipped_days']} day(s) before --from skipped")
    except (RuntimeError, ValueError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
