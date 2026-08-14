"""Gaps and exceptions across the outreach target profiles (Track O).

Read-only. Answers one question: **which targets cannot currently support the
arc, and why.** The operator asked for this (2026-08-14) because researching and
refining candidates by hand is the plan, and the failures worth catching are the
quiet ones — a target that accrues no evidence looks identical to a healthy one
until its packet turns out empty weeks later.

Ordered by severity, because they cost different things:

  * **blocker** — the arc cannot run at all (no stage → cannot sequence; no
    contact → nothing to address).
  * **evidence** — the target will never accrue posting age, which is the datum
    T10 and S4 both rest on and the one thing that cannot be backfilled. A
    silently unsupported board is the most expensive failure here, since every
    day it goes unnoticed is a day of history not collected.
  * **integrity** — the profile asserts something the evidence does not support.
    `trigger_kind = request_open_past_45_days` with no open-role evidence is the
    live example: the packet would want to quote a posting date it cannot see.
  * **staleness** — evidence exists but has not been confirmed recently (R19).
  * **incomplete** — scoring or contact detail missing; the target works, but
    not yet.

Run:
    uv run python -m cli.outreach_gaps                # profile checks only
    uv run python -m cli.outreach_gaps --check-boards # also fetch each board (slow, live)
    uv run python -m cli.outreach_gaps --severity blocker,evidence
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db, outreach
from agents.outreach import adapters

# Severity order — also the display order.
SEVERITIES = ("blocker", "evidence", "integrity", "staleness", "incomplete")

# Triggers that assert an open req. If one of these is set and no open-role
# evidence exists, the profile is claiming something the packet cannot show.
_ROLE_IMPLYING_TRIGGERS = frozenset({"request_open_past_45_days", "new_executive_hire"})

# Matches `35-` §3's stale tier: >14 days since last confirmation.
STALE_AFTER_DAYS = 14

_GAPS_SQL = """
    SELECT t.id, t.company_name, t.careers_url, t.stage, t.trigger_kind, t.status,
           t.contact_name, t.contact_first_name, t.contact_email, t.function,
           (t.s2_stage_fit IS NULL OR t.s3_sector_match IS NULL
            OR t.s4_leadership_gap IS NULL OR t.s5_team_build_below IS NULL) AS unscored,
           count(e.id) FILTER (WHERE e.fact_kind = 'open_role'
                                 AND e.closed_at IS NULL)          AS open_roles,
           max(CURRENT_DATE - e.last_seen_at) FILTER (
               WHERE e.closed_at IS NULL)                          AS days_since_confirmed
    FROM outreach_targets t
    LEFT JOIN outreach_evidence e ON e.target_id = t.id
    WHERE t.status NOT IN ('archived', 'dropped', 'engaged')
    GROUP BY t.id
    ORDER BY t.company_name
"""


def find_gaps(row: dict[str, Any], *, board_ok: bool | None = None) -> list[tuple[str, str]]:
    """Every gap in one target profile as (severity, message). Pure.

    `board_ok` is the result of a live board fetch when `--check-boards` ran, and
    None when it did not — so "we did not look" never reads as "the board works".
    """
    gaps: list[tuple[str, str]] = []

    # --- blockers -------------------------------------------------------------
    if not row.get("stage"):
        gaps.append(("blocker",
                     "no stage — cannot enter a sequence (outreach_targets_seq_ck), "
                     "and slot 1 has no stage-specific template"))
    if not row.get("contact_name"):
        gaps.append(("blocker", "no contact name — nothing to address the message to"))
    elif not row.get("contact_first_name"):
        # Offered, never applied: the report suggests so filling it is quick,
        # but a wrong guess lands in the greeting, which is the first line read.
        hint = outreach.suggest_first_name(row["contact_name"])
        suggestion = f" (suggestion from contact_name: {hint!r})" if hint else ""
        gaps.append(("blocker",
                     f"no contact_first_name — [First Name] cannot resolve, so the "
                     f"greeting blocks{suggestion}"))
    if not row.get("contact_email"):
        gaps.append(("blocker", "no contact email — no way to send"))

    # --- evidence acquisition -------------------------------------------------
    careers_url = row.get("careers_url")
    if not careers_url:
        gaps.append(("evidence",
                     "no careers_url — this target will never accrue posting age, "
                     "and posting age cannot be backfilled"))
    elif adapters.detect_board(careers_url) is None:
        gaps.append(("evidence",
                     f"careers_url is on an unsupported platform ({careers_url[:60]}) — "
                     "no evidence will accrue; find the company's real ATS board"))
    elif board_ok is False:
        gaps.append(("evidence",
                     "board detected but unreachable — the URL's handle is probably "
                     "wrong; nothing will accrue until it is fixed"))

    # --- integrity: the profile asserts what the evidence cannot support ------
    trigger = row.get("trigger_kind")
    if trigger in _ROLE_IMPLYING_TRIGGERS and not row.get("open_roles"):
        gaps.append(("integrity",
                     f"trigger is '{trigger}' but no open-role evidence exists — "
                     "the packet cannot quote the posting date the angle rests on"))

    # --- staleness (R19) ------------------------------------------------------
    days = row.get("days_since_confirmed")
    if days is not None and days > STALE_AFTER_DAYS:
        gaps.append(("staleness",
                     f"evidence last confirmed {days} days ago — past the stale tier, "
                     "so it is excluded from the arithmetic and blocks `ready`"))

    # --- incomplete -----------------------------------------------------------
    if not row.get("function"):
        gaps.append(("incomplete",
                     "no function — [function] is the pack's most-used placeholder "
                     "(57 uses); set it, or wait for an open leadership req the "
                     "poller can derive it from"))
    if row.get("unscored"):
        gaps.append(("incomplete",
                     "S2-S5 not fully scored — no score, no treatment, cannot reach "
                     "the intake gate"))
    if careers_url and adapters.detect_board(careers_url) and not row.get("open_roles"):
        gaps.append(("incomplete",
                     "board is supported but currently lists no open roles — "
                     "nothing to observe yet (not an error)"))

    return gaps


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report gaps and exceptions across outreach target profiles."
    )
    parser.add_argument(
        "--check-boards", action="store_true",
        help="also fetch each detected board live (slower; catches dead handles)",
    )
    parser.add_argument(
        "--severity", default=",".join(SEVERITIES),
        help=f"comma-separated subset of {','.join(SEVERITIES)}",
    )
    args = parser.parse_args()
    wanted = {s.strip() for s in args.severity.split(",") if s.strip()}
    if unknown := wanted - set(SEVERITIES):
        print(f"error: unknown severity {sorted(unknown)}", file=sys.stderr)
        return 1

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_GAPS_SQL)
            rows = cur.fetchall()

    totals = dict.fromkeys(SEVERITIES, 0)
    clean = 0
    for row in rows:
        board_ok = None
        if args.check_boards and row.get("careers_url"):
            if adapters.detect_board(row["careers_url"]):
                board_ok = adapters.fetch_open_roles(row["careers_url"]).ok

        gaps = [g for g in find_gaps(row, board_ok=board_ok) if g[0] in wanted]
        if not gaps:
            clean += 1
            continue
        print(f"\n{row['company_name']}  (#{row['id']}, {row['status']})")
        for severity, message in sorted(gaps, key=lambda g: SEVERITIES.index(g[0])):
            totals[severity] += 1
            print(f"    [{severity:<10}] {message}")

    print(f"\n{'-' * 72}")
    print(f"{len(rows)} target(s) · {clean} with no gaps in the selected severities")
    print("  " + " · ".join(f"{s}: {totals[s]}" for s in SEVERITIES if s in wanted))
    if not args.check_boards:
        print("  (boards not fetched — re-run with --check-boards to catch dead handles)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        db.close_pool()
