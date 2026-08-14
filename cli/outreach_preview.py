"""Preview what the Selector would materialise for each target (Track O).

Read-only. Shows, per target, the five touches the intake gate *would* create
and **why each template was chosen** — before any of it is written. The point is
that the Selector's reasoning is inspectable in advance rather than discovered
after five touches exist with the wrong angle in them.

Nothing here writes: no touches, no packets, no evidence. Sequence
materialisation is the intake card's job (`35-` §5), gated on a human decision.

Run:
    uv run python -m cli.outreach_preview              # every target
    uv run python -m cli.outreach_preview --target 7   # one, with placeholders
"""

from __future__ import annotations

import argparse
import sys

from psycopg.rows import dict_row

from agents._lib import db, selector

# The facts the Selector's conditions are evaluated against. Deliberately all
# derived from data the system holds — evidence rows and the target row — so a
# preview and a real intake resolve identically.
_FACTS_SQL = """
    SELECT t.id, t.company_name, t.stage, t.trigger_kind,
           CURRENT_DATE - t.trigger_date AS days_since_trigger,
           max(CURRENT_DATE - e.first_seen_at) FILTER (
               WHERE e.closed_at IS NULL AND e.fact_kind = 'open_role'
           ) AS open_role_age_days
    FROM outreach_targets t
    LEFT JOIN outreach_evidence e ON e.target_id = t.id
    -- Explicit cast: Postgres cannot infer a bare parameter's type from
    -- `$1 IS NULL` alone (AmbiguousParameter).
    WHERE (%(target_id)s::bigint IS NULL OR t.id = %(target_id)s::bigint)
    GROUP BY t.id
    ORDER BY t.company_name
"""


def fetch_facts(conn: object, target_id: int | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_FACTS_SQL, {"target_id": target_id})
        return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview the Selector's five-touch plan per target (writes nothing)."
    )
    parser.add_argument("--target", type=int, default=None, help="one target id")
    parser.add_argument(
        "--placeholders", action="store_true",
        help="also show each touch's auto / observed / operator placeholder split",
    )
    args = parser.parse_args()

    templates = selector.load_templates()
    config = selector.load_config()

    with db.connection() as conn:
        rows = fetch_facts(conn, args.target)
    if not rows:
        print("no targets found", file=sys.stderr)
        return 1

    for row in rows:
        age = row["open_role_age_days"]
        evidence = f"open role {age}d" if age is not None else "no open-role evidence"
        print(f"\n{row['company_name']}  ·  stage={row['stage'] or 'UNKNOWN'}  "
              f"·  {row['trigger_kind']}  ·  {evidence}")
        try:
            sequence = selector.select_sequence(row["stage"], row)
        except selector.SelectorError as e:
            # A target that cannot be sequenced is exactly what this preview is
            # for: it surfaces here, not halfway through materialising touches.
            print(f"    ✗ cannot sequence — {e}")
            continue

        for choice in sequence:
            template = templates.get(choice.template_code)
            title = template.title if template else "(MISSING TEMPLATE)"
            print(f"    {choice.slot}. {choice.slot_name:<14} {choice.template_code}")
            print(f"       {title}")
            print(f"       ↳ {choice.because}")
            if args.placeholders and template:
                parts = selector.partition_placeholders(template, config)
                for cls in (selector.AUTO, selector.OBSERVED, selector.OPERATOR):
                    if parts[cls]:
                        marker = "  (blocks ready)" if cls == selector.OPERATOR else ""
                        print(f"         {cls:<9}{marker}: {', '.join(parts[cls])}")
            if choice.alternates:
                print(f"       alternates: {len(choice.alternates)} "
                      f"(need operator knowledge — see selector.yaml)")
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        db.close_pool()
