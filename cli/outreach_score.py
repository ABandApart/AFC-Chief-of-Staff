"""Score a target on S2-S5 and set its function state (Track O, `35-` §4).

The only way to record the judgement half of the rubric until NocoDB lands
(increment 3). Everything here is **Tier 3 by design** — `35-` is explicit that
evidence *informs* S4/S5 and does not *set* them, and that the two-tab
diagnostic is five minutes of human judgement that cannot be bought.

So this tool does not score anything. It shows you the evidence, refuses invalid
values, writes what you decide, and tells you what it did to the treatment.

    uv run python -m cli.outreach_score --list
    uv run python -m cli.outreach_score --target 7 --show
    uv run python -m cli.outreach_score --target 7 --s2 5 --s3 3 --s4 5 --s5 5 \
        --function-state vacant_seat

Rubric: `playbooks/outreach-scoring.md` and `playbooks/outreach-function-state.md`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db

# The CHECK constraint's values (`outreach_targets_scores_ck`). Enforced here too
# so a typo fails with a usable message rather than a constraint violation.
VALID_SCORES = (1, 3, 5)
VALID_FUNCTION_STATES = ("self_covered", "under_led", "vacant_seat")

SIGNALS = {
    "s2": ("s2_stage_fit", "Stage fit"),
    "s3": ("s3_sector_match", "Sector match"),
    "s4": ("s4_leadership_gap", "Leadership gap"),
    "s5": ("s5_team_build_below", "Team build below"),
}


def fetch(conn: object, target_id: int | None = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT v.*,
                   (SELECT count(*) FROM outreach_evidence e
                     WHERE e.target_id = v.id AND e.closed_at IS NULL) AS open_facts
            FROM v_outreach_scored v
            WHERE (%(tid)s::bigint IS NULL OR v.id = %(tid)s::bigint)
              AND v.status NOT IN ('archived', 'dropped', 'engaged')
            ORDER BY v.score DESC NULLS LAST, v.company_name
            """,
            {"tid": target_id},
        )
        return cur.fetchall()


def show_evidence(conn: object, target_id: int) -> None:
    """The observed facts, so the S4/S5 judgement is made against something."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT fact_kind, payload, age_days, days_since_confirmed, freshness, "
            "source_url FROM v_outreach_evidence_display "
            "WHERE target_id = %s AND closed_at IS NULL ORDER BY first_seen_at",
            (target_id,),
        )
        rows = cur.fetchall()
    if not rows:
        print("  (no open evidence — tab 2 is empty; check the board is supported)")
        return
    for row in rows:
        title = (row["payload"] or {}).get("title") or row["fact_kind"]
        print(f"  · {title}  — open {row['age_days']}d, "
              f"confirmed {row['days_since_confirmed']}d ago [{row['freshness']}]")


def summarize(row: dict[str, Any]) -> str:
    score = row["score"] if row["score"] is not None else "—"
    treatment = row["treatment"] or "unscored"
    missing = [k for k, (col, _) in SIGNALS.items() if row[col] is None]
    gap = f"  needs: {', '.join(missing)}" if missing else ""
    fs = row["function_state"] or "function_state NOT SET"
    compound = "  ⚡" if row.get("compound_signal") else ""
    return (f"  #{row['id']:<4} {row['company_name'][:28]:<28} "
            f"{str(score):>4}/25  {treatment:<9} {fs:<20}{compound}{gap}")


def apply_scores(conn: object, target_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Write the scored signals. Stamps `signals_observed_at` — that is what
    starts the 30-day re-check clock S4/S5 are measured against (§4)."""
    sets = [f"{col} = %({col})s" for col in values]
    if any(col.startswith("s4") or col.startswith("s5") for col in values):
        sets.append("signals_observed_at = CURRENT_DATE")
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"UPDATE outreach_targets SET {', '.join(sets)} WHERE id = %(id)s "
            f"RETURNING id",
            {**values, "id": target_id},
        )
        if cur.fetchone() is None:
            raise KeyError(f"no outreach target {target_id}")
        cur.execute("SELECT * FROM v_outreach_scored WHERE id = %s", (target_id,))
        return cur.fetchone()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score an outreach target on S2-S5 and set its function state."
    )
    parser.add_argument("--target", type=int, help="target id")
    parser.add_argument("--list", action="store_true", help="list targets and their scores")
    parser.add_argument("--show", action="store_true",
                        help="show one target's evidence before you judge it")
    for key, (_, label) in SIGNALS.items():
        parser.add_argument(f"--{key}", type=int, choices=VALID_SCORES, help=f"{label} (1/3/5)")
    parser.add_argument("--function-state", choices=VALID_FUNCTION_STATES,
                        help="the two-tab diagnostic result")
    args = parser.parse_args()

    if not args.list and args.target is None:
        parser.error("give --list, or --target with values to set")

    with db.connection() as conn:
        if args.list or (args.target is not None and args.show):
            rows = fetch(conn, args.target)
            if not rows:
                print("no targets found", file=sys.stderr)
                return 1
            print(f"  {'id':<5} {'company':<28} {'score':>4}      "
                  f"{'treatment':<9} {'function state':<20}")
            for row in rows:
                print(summarize(row))
            if args.target is not None:
                print("\nOpen evidence (tab 2 — tab 1 is their team page, "
                      "which only you can read):")
                show_evidence(conn, args.target)
                print("\nRubric: playbooks/outreach-scoring.md · "
                      "playbooks/outreach-function-state.md")
            return 0

        values: dict[str, Any] = {}
        for key, (col, _) in SIGNALS.items():
            if (v := getattr(args, key)) is not None:
                values[col] = v
        if args.function_state:
            values["function_state"] = args.function_state
        if not values:
            parser.error("nothing to set — pass --s2/--s3/--s4/--s5 and/or --function-state")

        try:
            row = apply_scores(conn, args.target, values)
        except KeyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    print(f"\n  {row['company_name']}")
    print(summarize(row))
    if row["score"] is None:
        print("\n  Score stays NULL until all of S2-S5 are set — a partial rubric "
              "must not read as a low score.")
    elif row["treatment"] == "work":
        print("\n  → scores to WORK. An intake card posts on the next poll "
              "(capacity permitting).")
    elif row["treatment"] == "watch":
        print("\n  → WATCH. Parked; S1 may still lift it as the trigger ages.")
    else:
        print("\n  → DROP. Below 14.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        db.close_pool()
