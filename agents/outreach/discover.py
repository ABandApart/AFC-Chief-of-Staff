"""The daily discovery run (Track O, Part 0).

Sources candidate firms from every channel, verifies them, scores them, and
inserts the survivors into the Gate 0 pool as unreviewed. Surfacing them is the
cog's job; this loop only fills the pool.

**No LLM.** Deterministic fetch, verification and arithmetic, so it writes no
`agent_runs` rows, trips no ceiling, and cannot fail from a provider outage
(`40-action-layer.md`, Outreach_loops).

**Nothing is fabricated to hit a number.** R0.11 makes the daily window a ceiling
rather than a quota, and the same applies here: a run that finds three verifiable
firms inserts three and reports why. Padding the pool with unverified firms would
defeat the bar that lets the operator trust the queue at all.

A firm that fails verification is **inserted anyway but cannot surface** - the
`list_for_review` query filters on the two-kind minimum. That is deliberate: it
records that the firm was seen and found thin, so the next run dedups against it
instead of re-probing it forever.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from agents._lib import db, outreach, outreach_discovery
from agents.outreach import discovery, icp, verify

logger = logging.getLogger(__name__)

SEGMENTS = icp.ALL_SEGMENTS

# OQ-C, 2026-08-20. A flag rather than a constant in the query, so reopening
# geography costs a command-line argument.
DEFAULT_COUNTRY = "US"


def build_row(candidate: dict[str, Any], verification: verify.Verification,
              entered: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """Assemble one pool row from a raw candidate and its verification."""
    row = {
        "company_name": candidate["company_name"],
        "company_domain": outreach.normalize_domain(candidate["company_url"]),
        "company_url": candidate.get("company_url"),
        "careers_url": verification.careers_url or candidate.get("careers_url"),
        "segment": candidate["segment"],
        "country": candidate.get("country", DEFAULT_COUNTRY),
        "hq_location": candidate.get("hq_location"),
        "headcount_band": candidate.get("headcount_band"),
        "description": candidate.get("description"),
        "contact_name": candidate.get("contact_name"),
        "contact_title": candidate.get("contact_title"),
        "company_linkedin_url": candidate.get("company_linkedin_url"),
        "contact_linkedin_url": candidate.get("contact_linkedin_url"),
        "verification_note": verification.note,
        "verified_on": verification.kinds,
        "discovered_via": candidate["discovered_via"],
        "discovery_query": candidate.get("discovery_query"),
        # R0.21: the item an extracted name came from. Shown beside the name so a
        # wrong entity is visible rather than inferred — the safety property that
        # makes a bounded, unreliable extraction step acceptable.
        "source_url": candidate.get("source_url"),
    }
    row["icp_fit_score"] = icp.score(row, entered)
    row["icp_model_version"] = icp.MODEL_VERSION
    return row


def run(*, country: str = DEFAULT_COUNTRY, fetch: bool = True,
        dry_run: bool = False) -> dict[str, int]:
    """One discovery pass across every segment."""
    counts = {"found": 0, "out_of_scope": 0, "duplicate": 0,
              "thin": 0, "inserted": 0}

    with db.connection() as conn:
        known = outreach_discovery.known_domains(conn)
        entered = outreach_discovery.entered_segment_scores(conn)

        for segment in SEGMENTS:
            for candidate in discovery.find_all(segment):
                counts["found"] += 1

                if country != "all" and candidate.get("country", DEFAULT_COUNTRY) != country:
                    counts["out_of_scope"] += 1
                    continue

                domain = outreach.normalize_domain(candidate["company_url"])
                if domain in known:
                    counts["duplicate"] += 1
                    continue

                verification = verify.verify(candidate, fetch=fetch)
                if not verification.passes(outreach_discovery.MIN_VERIFICATION_KINDS):
                    counts["thin"] += 1
                    logger.warning(
                        "discovery: %s cleared %d verification kind(s), needs %d — "
                        "recorded, will not surface (%s)",
                        candidate["company_name"], len(verification.kinds),
                        outreach_discovery.MIN_VERIFICATION_KINDS, verification.note,
                    )

                row = build_row(candidate, verification, entered)
                if dry_run:
                    print(f"  would insert {row['company_name']:<34} "
                          f"{row['segment']:<24} ICP {row['icp_fit_score']:>3}  "
                          f"[{', '.join(row['verified_on']) or 'unverified'}]")
                    known.add(domain)
                    counts["inserted"] += 1
                    continue

                if outreach_discovery.insert_discovery(conn, row) is not None:
                    known.add(domain)
                    counts["inserted"] += 1

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default=DEFAULT_COUNTRY,
                        help="Restrict to this country (OQ-C: US). 'all' widens.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be inserted; write nothing.")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Skip network verification (offline checks only).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    counts = run(country=args.country, fetch=not args.no_fetch, dry_run=args.dry_run)

    verb = "would insert" if args.dry_run else "inserted"
    print(
        f"\n{counts['found']} candidate(s) from {len(discovery.CHANNELS)} channel(s) · "
        f"{counts['out_of_scope']} out of scope · {counts['duplicate']} already known · "
        f"{counts['thin']} below the verification bar · {verb} {counts['inserted']}"
    )
    if counts["found"] == 0:
        print(
            "\nNo candidates. The seed list is empty — see "
            "config/outreach/discovery/seeds.yaml, and the channel package "
            "docstring on why the feed-shaped channels cannot fill it themselves.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
