"""The daily Notion inputs run (`PRD-notion-integration.md` N9, `notion-inputs-sync`).

    uv run python -m agents.notion.inputs_sync            # the loop runs this, daily 05:15
    uv run python -m agents.notion.inputs_sync --manual   # a run outside the schedule

One run does two things:

1. **Task Tinder cards in To-Do** (I1, `cards_sync`): new candidates become card
   rows; Accept, Decline, and Defer set in Notion are applied.
2. **Outcomes, Capture, and Sources** (I5, `records_sync`).

Its notices (refusals, failures) go to the day's Briefing page. A refusal because
someone other than the operator edited a row is also an urgent alert (N9 I6).
Every run writes one `agent_runs` row (`function_label='inputs_sync'`).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Any

FUNCTION_LABEL = "inputs_sync"
FOREIGN = "other than the operator"
logger = logging.getLogger(__name__)


def run_once(client: Any, *, connection: Any, tinder: Any, today: date,
             brief: Any = None, alert: Any = None) -> str:
    """One run. `tinder` is `task_tinder`; `brief(text)` appends to the day's
    Briefing page; `alert(text)` raises an urgent system note."""
    from agents.notion import cards_sync, operator, records_sync

    with connection() as conn:
        allowed = operator.allowed_editors(client, conn)
    lines, notices = [], []
    cards, card_notices = cards_sync.run(client, connection=connection, tinder=tinder,
                                         allowed=allowed, today=today)
    lines.append(cards)
    notices += card_notices
    records, record_notices = records_sync.run(client, connection=connection,
                                               allowed=allowed, today=today)
    lines.append(records)
    notices += record_notices
    if notices and brief:
        brief("\n".join(notices))
    if alert and (foreign := [n for n in notices if FOREIGN in n]):
        alert("Notion edits refused because someone other than the operator made them: "
              + "; ".join(foreign))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily Notion inputs sync.")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="act as if today were this date (testing only)")
    args = parser.parse_args()
    today = args.date or date.today()

    from agents._lib import db, task_tinder
    from agents._lib.notion_client import NotionClient, NotionError
    from agents.notion import briefing_page
    from agents.notion.ledger import notion_run

    try:
        client = NotionClient.from_keychain()

        def brief(text: str) -> None:
            try:
                briefing_page.append(client, db.connection, today, "Notion inputs", text,
                                     key="inputs")
            except Exception:  # noqa: BLE001 - the Briefing page must not stop the run
                logger.exception("inputs: Briefing append failed")

        def alert(text: str) -> None:
            briefing_page.system_note(client, db.connection, today, text, urgent=True)

        with notion_run(FUNCTION_LABEL, trigger_kind="manual" if args.manual else "scheduled"):
            print(run_once(client, connection=db.connection, tinder=task_tinder, today=today,
                           brief=brief, alert=alert))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
