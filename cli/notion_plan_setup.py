"""One-time Notion setup for the daily plan (`PRD-notion-integration.md` N7).

    uv run python -m cli.notion_plan_setup --dry-run
    uv run python -m cli.notion_plan_setup

Adds the CoS-written columns Minutes, Planned for, and Context, and the Source
option `plan`, to the To-Do database, which N3 made before they existed. It
changes nothing else in that database and is safe to re-run. Then it adds the
"📋 Daily plan" heading and view to the current quarter's dashboard page, under
the "Needs your attention" callout; the daily loop keeps the view's dates current.
Run as barry-agent.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from agents._lib import db
from agents._lib.notion_client import NotionClient, NotionError
from agents.notion import plan_daily
from agents.notion import todo_schema as todo


def todo_patch(props: dict[str, Any]) -> dict[str, Any]:
    """The properties to add to an existing To-Do data source (pure). Empty when
    nothing is missing."""
    patch: dict[str, Any] = {n: spec for n, spec in todo.PLAN_PROPERTIES.items()
                             if n not in props}
    options = ((props.get("Source") or {}).get("select") or {}).get("options") or []
    if "Source" in props and todo.PLAN_SOURCE not in {o.get("name") for o in options}:
        keep = [{k: o[k] for k in ("id", "name", "color") if k in o} for o in options]
        patch["Source"] = {"select": {"options": [
            *keep, {"name": todo.PLAN_SOURCE, "color": "default"}]}}
    return patch


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up Notion for the daily plan.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    args = parser.parse_args()

    try:
        client = NotionClient.from_keychain()
        with db.connection() as conn:
            source = plan_daily._todo_source(client, conn)
        props = client.retrieve_data_source(source).get("properties") or {}
        patch = todo_patch(props)
        if not patch:
            print("OK    To-Do already has Minutes, Planned for, Context, and Source 'plan'")
        elif args.dry_run:
            print(f"PLAN  add to To-Do: {', '.join(patch)}")
        else:
            client.update_data_source(source, patch)
            print(f"NEW   added to To-Do: {', '.join(patch)}")
        if args.dry_run:
            print("PLAN  add the 'Daily plan' heading and view to the dashboard if missing")
            return 0
        today = date.today()
        with db.connection() as conn:
            days = plan_daily.plan_days(conn, today) or [today]
        print(f"OK    {plan_daily.dashboard(client, db.connection, today, days, print)}")
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
