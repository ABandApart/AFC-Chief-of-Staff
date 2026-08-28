"""Outreach enrichment — the Apollo V2 coverage probe (Track O Part 3).

**Read-only. This is a measurement, not the storing adapter.**
`PRD-outreach-company-profile.md` §3.3 requires V2 — run real targets through
Apollo and count how many of the nine §3.1 spine fields come back non-null —
*before* any integration code that writes provider data exists. This CLI is that
probe and nothing more: it fetches, maps, counts, and prints. It writes nothing
to `outreach_targets`, and there is deliberately no `--apply` here. The storing
adapter is the next slice, gated on this probe's result.

Runs on barry-agent (holds `db-url` and `apollo-api-key`); on the build box the
keychain lookup for the Apollo key raises, by design.

    uv run python -m cli.outreach_enrich --probe              # 5 active targets
    uv run python -m cli.outreach_enrich --probe --limit 5
    uv run python -m cli.outreach_enrich --probe --ids 18,27  # specific targets
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from psycopg.rows import dict_row

from agents._lib import creds, db
from agents.outreach import apollo

# V2 asks for "5 of the 14 real targets" (§3.3). Default the sample to 5.
DEFAULT_SAMPLE = 5

_TARGETS_SQL = """
    SELECT id, company_name, company_domain
      FROM outreach_targets
     WHERE company_domain IS NOT NULL
       AND status NOT IN ('archived', 'dropped')
     ORDER BY id
"""


def _select_targets(cur, ids: list[int] | None, limit: int) -> list[dict[str, Any]]:
    cur.execute(_TARGETS_SQL)
    rows = cur.fetchall()
    if ids:
        wanted = set(ids)
        return [r for r in rows if r["id"] in wanted]
    return rows[:limit]


def _fmt(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


def run_probe(ids: list[int] | None, limit: int) -> int:
    """Fetch + map + count. Returns a process exit code."""
    try:
        api_key = creds.keychain_get(apollo.APOLLO_KEY_ITEM)
    except RuntimeError as exc:
        print(
            f"error: {exc}\n"
            f"The V2 probe needs the Apollo API key in the keychain as "
            f"'{apollo.APOLLO_KEY_ITEM}'. Add it on the runtime account, then re-run.",
            file=sys.stderr,
        )
        return 2

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            targets = _select_targets(cur, ids, limit)

    if not targets:
        print("No matching targets.", file=sys.stderr)
        return 1

    mapped_rows: list[dict[str, Any]] = []
    raw_keys: set[str] = set()
    no_org: list[str] = []

    print(f"Apollo V2 coverage probe — {len(targets)} target(s)\n")
    for t in targets:
        org = apollo.enrich_organization(t["company_domain"], api_key)
        if org is None:
            no_org.append(t["company_name"])
            print(f"  {t['company_name']:<28} → no organization returned")
            continue
        raw_keys.update(org.keys())
        spine = apollo.map_organization(org)
        mapped_rows.append(spine)
        filled = sum(1 for f in apollo.SPINE_FIELDS if spine[f] not in (None, ""))
        print(
            f"  {t['company_name']:<28} → {filled}/{len(apollo.SPINE_FIELDS)} spine fields"
            f"  [sector={_fmt(spine['sector'])}, headcount={_fmt(spine['headcount'])}, "
            f"raised={_fmt(spine['total_raised_usd'])}, founded={_fmt(spine['founded_year'])}]"
        )

    n = len(targets)
    print(f"\nField coverage across {n} target(s):")
    counts = apollo.coverage(mapped_rows)
    for field in apollo.SPINE_FIELDS:
        c = counts[field]
        bar = "█" * c + "·" * (n - c)
        print(f"  {field:<18} {c}/{n}  {bar}")

    if no_org:
        print(f"\n{len(no_org)} target(s) returned no Apollo organization: "
              f"{', '.join(no_org)}")

    # The union of raw Apollo keys teaches the true schema for building the
    # storing adapter next — printed so the run itself is the source of truth,
    # not this file's field guesses.
    print(f"\nRaw Apollo organization keys observed ({len(raw_keys)}):")
    print("  " + ", ".join(sorted(raw_keys)) if raw_keys else "  (none)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe", action="store_true",
        help="Run the read-only V2 coverage probe (the only mode; no writes).",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_SAMPLE,
                        help=f"How many targets to sample (default {DEFAULT_SAMPLE}).")
    parser.add_argument("--ids", type=str, default=None,
                        help="Comma-separated target ids to probe instead of a sample.")
    args = parser.parse_args(argv)

    if not args.probe:
        parser.error("the only supported mode is --probe (the storing adapter is a "
                     "later slice, gated on V2)")

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    try:
        return run_probe(ids, args.limit)
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
