"""Import outreach targets from CSV (Track O, `35-` §5 D1).

The intake path that exists before NocoDB does. UPSERTs on `company_domain` —
**never a blind insert** — so re-importing a corrected spreadsheet updates the
firmographics without minting duplicate rows that would inflate the live count
against the capacity cap (R8).

What an import may NOT touch, no matter what the CSV says: `s2`–`s5`,
`function_state`, `status`, `stalled_reason`. Those are the operator's two-tab
diagnostic and pipeline decisions — human observation outranks a spreadsheet, and
the rule is enforced in `_lib/outreach.upsert_target`, not here, so every caller
gets it.

CSV columns — `company_name`, `company_domain`, `stage`, `trigger_kind`,
`trigger_date` are required; `company_url`, `careers_url`, `sector`,
`contact_name`, `contact_role`, `contact_email`, `contact_linkedin_url`,
`trigger_source_url` are optional:

    company_name,company_domain,stage,trigger_kind,trigger_date,careers_url
    Cadence Health,cadence.health,series_a,req_open_45d,2026-06-17,https://boards.greenhouse.io/cadencehealth

`careers_url` is worth filling in on every row: it is what the evidence poller
walks, and posting age only accrues from the day polling starts.

Run:
    uv run python -m cli.outreach_import targets.csv --dry-run
    uv run python -m cli.outreach_import targets.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agents._lib import db, outreach

REQUIRED = ("company_name", "company_domain", "stage", "trigger_kind", "trigger_date")
OPTIONAL = (
    "company_url", "careers_url", "sector", "contact_name", "contact_role",
    "contact_email", "contact_linkedin_url", "trigger_source_url",
)

# Mirrors the `stage` comment in migration 0013. Checked here rather than by a DB
# constraint because the spec's house convention is TEXT-with-an-enumerating-
# comment — but a typo'd stage silently breaks S2 scoring, so the import refuses.
VALID_STAGES = ("seed", "series_a", "series_b_plus")


def parse_row(row: dict[str, str], *, line: int) -> dict[str, Any]:
    """Validate and coerce one CSV row → an upsert dict (pure).

    Raises ValueError naming the line, so a 200-row import reports the bad row
    rather than a stack trace.
    """
    clean = {k.strip(): (v or "").strip() for k, v in row.items() if k}
    missing = [c for c in REQUIRED if not clean.get(c)]
    if missing:
        raise ValueError(f"line {line}: missing required column(s): {', '.join(missing)}")

    stage = clean["stage"]
    if stage not in VALID_STAGES:
        raise ValueError(
            f"line {line}: stage {stage!r} is not one of {', '.join(VALID_STAGES)}"
        )

    raw_date = clean["trigger_date"]
    try:
        trigger_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"line {line}: trigger_date {raw_date!r} is not YYYY-MM-DD") from None
    if trigger_date > date.today():
        # The arc anchors on this date; a future one would put every touch window
        # ahead of itself and silently park the target.
        raise ValueError(f"line {line}: trigger_date {raw_date} is in the future")

    target: dict[str, Any] = {
        "company_name": clean["company_name"],
        "company_domain": clean["company_domain"],
        "stage": stage,
        "trigger_kind": clean["trigger_kind"],
        "trigger_date": trigger_date,
    }
    for col in OPTIONAL:
        if value := clean.get(col):
            target[col] = value
    return target


def read_targets(path: Path) -> list[dict[str, Any]]:
    """Parse the whole CSV before writing anything.

    All-or-nothing on purpose: a half-applied import leaves the operator guessing
    which rows landed, and the fix (re-run) is only safe because the upsert is
    idempotent — better to simply not start.
    """
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: no data rows")
    return [parse_row(row, line=i) for i, row in enumerate(rows, start=2)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import outreach targets from CSV (Track O).")
    parser.add_argument("csv_path", type=Path, help="CSV file to import")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="parse and validate, print what would change, write nothing",
    )
    args = parser.parse_args()

    try:
        targets = read_targets(args.csv_path)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        for t in targets:
            board = outreach.normalize_domain(t["company_domain"])
            careers = t.get("careers_url") or "(no careers_url — no evidence will accrue)"
            print(f"  {t['company_name']:<30} {board:<24} {t['trigger_date']}  {careers}")
        print(f"{len(targets)} row(s) parsed OK — nothing written (--dry-run)")
        return 0

    inserted = updated = 0
    with db.connection() as conn:
        for t in targets:
            row = outreach.upsert_target(conn, t)
            if row["was_inserted"]:
                inserted += 1
            else:
                updated += 1
    print(f"imported {len(targets)} row(s): {inserted} new, {updated} updated")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        db.close_pool()
