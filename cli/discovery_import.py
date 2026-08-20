"""Import the operator CRM workbook into the Gate 0 pool (Track O, Part 0 · R0.13).

    uv run python -m cli.discovery_import --dry-run
    uv run python -m cli.discovery_import

Reads `Education_LD_Leads_CRM_(current).xlsx` directly rather than a CSV export.
That is deliberate: the manual export step is how the workbook's `Date Added`
column — a batch stamp, identical on all 100 rows — became every target's
`trigger_date` and silently drove every S1 score.

**What it imports, and what it refuses to.**

  * **US rows only** (OQ-C, 2026-08-20). `--country` parameterises it so
    reopening geography is a flag rather than a code change.
  * **As unreviewed** (OQ-F). The workbook's `Status` is `New` on all 100 rows —
    they were assembled but never triaged. Importing them as accepts would
    fabricate labels that Part 4 would then learn from, which is risk D1.
  * **No trigger, ever** (R0.3). A discovery has no trigger until one is
    observed. This importer writes into `outreach_discoveries`, which has no
    trigger columns at all, so the mistake is structurally unavailable.
  * **No ARR** (R0.9). The workbook has no revenue column, so the estimate stays
    absent rather than being invented from headcount here.

Rows are skipped, never fabricated: a firm already in `outreach_discoveries` or
`outreach_targets` is left alone (R0.10), and a firm that cannot clear the
two-kind verification bar (R0.5) is imported but will not surface until it can.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agents._lib import db, outreach, outreach_discovery
from agents.outreach import icp

DEFAULT_WORKBOOK = Path.home() / "Public" / "Education_LD_Leads_CRM_(current).xlsx"
SHEET = "Leads"

# The workbook's segment names, mapped onto the CHECK-pinned vocabulary (R0.1).
# The three new segments have no workbook rows yet by construction.
SEGMENT_MAP = {
    "Corporate L&D / Training": "corporate_l_and_d",
    "Coaching & Leadership Development": "coaching_leadership",
    "Instructional Design / Learning Agency": "instructional_design",
}

# The workbook's Guide sheet defines these three levels; the pool reuses them
# rather than inventing a scale (R0.7 field contract, item 7).
CONFIDENCE_MAP = {
    "Public": "public",
    "Inferred (pattern)": "inferred_pattern",
    "General inbox": "general_inbox",
}

PAIN_LAYER_PREFIXES = ("L1", "L2", "L3")


def _cell(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pain_layer(raw: str | None) -> str | None:
    """`L3 — Shipped & straining` -> `L3`. Recorded, not surfaced (R0.14)."""
    if not raw:
        return None
    head = raw.strip()[:2].upper()
    return head if head in PAIN_LAYER_PREFIXES else None


def _verification(row: dict[str, Any]) -> list[str]:
    """Which evidence kinds the workbook actually attests (R0.5).

    Two only, and neither is inferred from prose. The Guide sheet states every
    row was verified as real and operational with an active site, so `live_site`
    holds; `linkedin_resolves` holds where a company LinkedIn URL is present.
    Reading `third_party_dated` out of the free-text Verification Note by keyword
    would be guessing, and a firm shown as verified that is not produces
    confident, checkable, wrong outreach.
    """
    kinds = ["live_site"]
    if _cell(row, "Company LinkedIn"):
        kinds.append("linkedin_resolves")
    return kinds


def to_discovery(row: dict[str, Any]) -> dict[str, Any] | None:
    """Map one workbook row to a pool row, or None when it cannot be mapped."""
    name = _cell(row, "Company")
    website = _cell(row, "Website")
    segment = SEGMENT_MAP.get(_cell(row, "Segment") or "")
    if not (name and website and segment):
        return None

    verified_on = _verification(row)
    candidate = {
        "company_name": name,
        "company_domain": outreach.normalize_domain(website),
        "company_url": website,
        "segment": segment,
        "country": _cell(row, "Country") or "US",
        "hq_location": _cell(row, "HQ"),
        "headcount_band": _cell(row, "Employees (est.)"),
        "description": _cell(row, "Description"),
        "contact_name": _cell(row, "Contact Name"),
        "contact_title": _cell(row, "Title"),
        "contact_email": _cell(row, "Email"),
        "email_confidence": CONFIDENCE_MAP.get(_cell(row, "Email Confidence") or ""),
        "company_linkedin_url": _cell(row, "Company LinkedIn"),
        "contact_linkedin_url": _cell(row, "Contact LinkedIn"),
        "verification_note": _cell(row, "Verification Note"),
        "verified_on": verified_on,
        "pain_layer": _pain_layer(_cell(row, "Pain Layer")),
        # Already written in the workbook, so nothing is generated on import.
        # It stays a draft either way: packet assembly never reads this column.
        "pain_hook": _cell(row, "Suggested Pain Point (outreach hook)"),
        "discovered_via": "workbook_import",
        "discovery_query": f"{DEFAULT_WORKBOOK.name}:{SHEET}",
    }
    candidate["icp_fit_score"] = icp.score(candidate)
    candidate["icp_model_version"] = icp.MODEL_VERSION
    return candidate


def read_workbook(path: Path) -> list[dict[str, Any]]:
    import openpyxl  # main dependency; imported here to keep --help fast

    book = openpyxl.load_workbook(path, data_only=True)
    sheet = book[SHEET]
    header = [cell.value for cell in sheet[1]]
    return [
        dict(zip(header, values))
        for values in sheet.iter_rows(min_row=2, values_only=True)
        if values and values[1]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument(
        "--country", default="US",
        help="Import only this country (OQ-C: US). Pass 'all' to widen.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen; write nothing.")
    args = parser.parse_args(argv)

    if not args.workbook.exists():
        print(f"workbook not found: {args.workbook}", file=sys.stderr)
        return 1

    rows = read_workbook(args.workbook)
    print(f"read {len(rows)} row(s) from {args.workbook.name}:{SHEET}")

    wanted = [
        r for r in rows
        if args.country == "all" or (r.get("Country") or "").strip() == args.country
    ]
    out_of_scope = len(rows) - len(wanted)
    if out_of_scope:
        print(f"  {out_of_scope} row(s) outside --country={args.country}: not imported, "
              f"not rejected — they stay in the workbook (R0.13)")

    with db.connection() as conn:
        known = outreach_discovery.known_domains(conn)

        mapped: list[dict[str, Any]] = []
        unmappable = 0
        for row in wanted:
            candidate = to_discovery(row)
            if candidate is None:
                unmappable += 1
                continue
            mapped.append(candidate)

        fresh = [c for c in mapped if c["company_domain"] not in known]
        duplicate = len(mapped) - len(fresh)
        thin = [c for c in fresh
                if len(c["verified_on"]) < outreach_discovery.MIN_VERIFICATION_KINDS]

        if unmappable:
            print(f"  {unmappable} row(s) missing company/website/segment: skipped")
        if duplicate:
            print(f"  {duplicate} row(s) already in the pool or in outreach_targets: skipped")
        if thin:
            print(f"  {len(thin)} row(s) clear only "
                  f"{outreach_discovery.MIN_VERIFICATION_KINDS - 1} verification kind — "
                  f"imported, but will not surface until re-verified (R0.5):")
            for c in thin:
                print(f"      {c['company_name']} ({c['company_domain']})")

        if args.dry_run:
            print(f"\n--dry-run: would insert {len(fresh)} row(s), all unreviewed")
            _summarise(fresh)
            return 0

        inserted = 0
        for candidate in fresh:
            if outreach_discovery.insert_discovery(conn, candidate) is not None:
                inserted += 1

    print(f"\ninserted {inserted} row(s) as unreviewed")
    _summarise(fresh)
    return 0


def _summarise(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    by_segment: dict[str, list[int]] = {}
    for row in rows:
        by_segment.setdefault(row["segment"], []).append(row["icp_fit_score"])
    print(f"  ICP fit by segment (model {icp.MODEL_VERSION}):")
    for segment, scores in sorted(by_segment.items()):
        average = sum(scores) / len(scores)
        print(f"      {segment:24} n={len(scores):3d}  "
              f"mean {average:.0f}  range {min(scores)}-{max(scores)}")


if __name__ == "__main__":
    raise SystemExit(main())
