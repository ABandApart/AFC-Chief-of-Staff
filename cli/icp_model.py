"""Inspect and steer the ICP feedback loop (Track O, Part 4).

    uv run python -m cli.icp_model --report       # rates + sample sizes
    uv run python -m cli.icp_model --propose v2-2026-08-27
    uv run python -m cli.icp_model --diff v2-2026-08-27
    uv run python -m cli.icp_model --activate v2-2026-08-27

The loop PROPOSES; the operator disposes (Part 4 outcome 4). `--propose` writes a
new model version **inactive**; nothing it contains touches a score until
`--activate` makes it the one active model. Activation is the only way the active
model changes, and it is audited.

No LLM, no cognee — arithmetic over Gate 0 labels.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db
from agents.outreach import learn


def _report(conn: object) -> int:
    rates = learn.compute_rates(conn)
    print(f"ICP feedback — {rates['total_labels']} labelled decision(s), "
          f"base accept rate {rates['base_accept_rate']:.0%}, "
          f"min sample {rates['min_sample']}")
    report_only = set(rates.get("report_only", ()))
    for factor, cells in rates["factors"].items():
        tag = " (observed only — held out of scoring, R0.14)" if factor in report_only else ""
        print(f"\n  {factor}{tag}")
        if not cells:
            print("    (no labels yet)")
        for value, cell in sorted(cells.items(), key=lambda kv: -kv[1]["sample"]):
            mark = "✓" if cell["reportable"] else "·"
            rate = (f"{cell['smoothed_rate']:.0%}" if cell["smoothed_rate"] is not None
                    else "—")
            print(f"    {mark} {value:<22} n={cell['sample']:>3}  {rate}"
                  f"{'' if cell['reportable'] else '   (below min sample)'}")
    reportable = any(c["reportable"]
                     for cells in rates["factors"].values() for c in cells.values())
    if not reportable:
        print("\nNo cell has reached the minimum sample yet — nothing is "
              "reportable, so a proposal would carry no adjustments.")
    return 0


def _active_model(conn: object) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT version, factors FROM outreach_icp_models WHERE active")
        return cur.fetchone()


def _get_model(conn: object, version: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT version, active, factors, notes FROM outreach_icp_models "
            "WHERE version = %s", (version,))
        return cur.fetchone()


def _propose(conn: object, version: str) -> int:
    if _get_model(conn, version) is not None:
        print(f"version {version!r} already exists — pick a new name", file=sys.stderr)
        return 1
    factors = learn.propose_model(conn, version)
    if not factors["adjustments"]:
        print(f"Proposal {version} would carry NO adjustments — no factor cell has "
              f"reached the {learn.MIN_SAMPLE}-label minimum yet. Writing it anyway "
              f"would just clone v1; refusing.", file=sys.stderr)
        return 1
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "INSERT INTO outreach_icp_models (version, factors, notes) "
            "VALUES (%s, %s, %s)",
            (version, json.dumps(factors),
             f"proposed from {factors['built_from_labels']} labels"),
        )
    print(f"Wrote {version} (inactive). Review with --diff {version}, "
          f"then --activate {version}.")
    return 0


def _diff(conn: object, version: str) -> int:
    proposed = _get_model(conn, version)
    if proposed is None:
        print(f"no such version {version!r}", file=sys.stderr)
        return 1
    active = _active_model(conn)
    active_adj = (active["factors"].get("adjustments", {}) if active else {})
    print(f"Diff {version} vs active model "
          f"({active['version'] if active else 'none — v1 defaults'}):")
    prop_adj = proposed["factors"].get("adjustments", {})
    for factor in sorted(set(active_adj) | set(prop_adj)):
        print(f"\n  {factor}")
        old = active_adj.get(factor, {})
        new = prop_adj.get(factor, {})
        for value in sorted(set(old) | set(new)):
            o = old.get(value)
            n = new.get(value)
            arrow = (f"{o:.0%}" if o is not None else "—") + " → " + \
                    (f"{n:.0%}" if n is not None else "—")
            print(f"    {value:<22} {arrow}")
    return 0


def _activate(conn: object, version: str) -> int:
    if _get_model(conn, version) is None:
        print(f"no such version {version!r}", file=sys.stderr)
        return 1
    # One active model: deactivate the rest, then activate this one — in a single
    # transaction so the partial-unique index never sees two actives.
    with conn.transaction():
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute("UPDATE outreach_icp_models SET active = false, "
                        "activated_at = NULL, activated_by = NULL WHERE active")
            cur.execute(
                "UPDATE outreach_icp_models SET active = true, "
                "activated_at = now(), activated_by = session_user "
                "WHERE version = %s", (version,))
    print(f"Activated {version}. It is now the model every new score uses; "
          f"the change is recorded in outreach_events.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true",
                       help="Rates with sample sizes; changes nothing.")
    group.add_argument("--propose", metavar="VERSION",
                       help="Write an inactive proposed model from the rates.")
    group.add_argument("--diff", metavar="VERSION",
                       help="Show a proposal against the active model.")
    group.add_argument("--activate", metavar="VERSION",
                       help="Make a version the one active model (audited).")
    args = parser.parse_args(argv)

    with db.connection() as conn:
        if args.report:
            return _report(conn)
        if args.propose:
            return _propose(conn, args.propose)
        if args.diff:
            return _diff(conn, args.diff)
        return _activate(conn, args.activate)


if __name__ == "__main__":
    raise SystemExit(main())
