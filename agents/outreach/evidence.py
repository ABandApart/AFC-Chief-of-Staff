"""Outreach evidence poller (Track O, `35-` §6 / build step 6).

Polls each target's job board every 12h and maintains `outreach_evidence`:
new reqs get `first_seen_at = today`, still-present reqs advance `last_seen_at`,
and reqs that disappeared are closed. That is the whole job, and it is the step
the spec says to start **before anything else in Track O**:

> `first_seen_at` only accrues forward. Two weeks of polling before you send
> anything is two weeks of posting-age data you cannot buy retroactively.

**No LLM, by design** (`40-action-layer.md` Outreach_loops): no `agent_runs`
rows, no ceiling to trip, nothing to fail from a provider outage. Evidence
acquisition is a JSON GET and an upsert.

The failure posture is deliberately asymmetric. A target whose board cannot be
read is **left completely untouched** — no closes, no writes — because the
damaging error here is not a missed poll, it is inventing a state change from a
failed fetch. Stale evidence is visible (the freshness tiers in §3 mark it, and
the packet refuses to send on it); silently-closed evidence is not.

Run (barry-agent, where the DB URL lives):
    uv run python -m agents.outreach.evidence --dry-run   # list what would be polled
    uv run python -m agents.outreach.evidence
Scheduled by the `outreach-evidence` loop manifest (every 12h).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from agents._lib import db, outreach
from agents.outreach import adapters

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def poll_target(conn: object, target: dict, *, today: date) -> dict[str, int | str]:
    """Poll one target's board and reconcile its `open_role` evidence.

    Returns a small summary dict. Never raises for a per-target failure — one
    unreachable board must not cost the rest of the cycle its polling window.
    """
    result = adapters.fetch_open_roles(target["careers_url"])
    if not result.ok:
        # Not an error state worth failing the run over, but it IS worth saying
        # out loud every cycle: an unsupported board accrues no posting age, and
        # that only becomes visible weeks later as an empty packet.
        logger.warning(
            "outreach: %s (%s) — %s",
            target["company_name"], target["company_domain"], result.reason,
        )
        return {"status": "skipped", "reason": result.reason or "unknown"}

    # A dict, not a list: several feed entries can share one posting id. Rippling
    # lists a req once per location, so ttcInnovations' 7 entries are 3 distinct
    # reqs. The upsert already collapses them — this keeps the COUNT honest too,
    # since "7 open roles" in the log about 3 stored rows reads as a dedup bug
    # and sends someone hunting one that is not there.
    seen_keys: dict[str, None] = {}
    new_count = 0
    for role in result.roles:
        fact = adapters.role_to_fact(role, result.provider or "unknown")
        seen_keys[fact["dedup_key"]] = None
        row = outreach.evidence_row(fact, target_id=target["id"], today=today)
        if outreach.upsert_evidence(conn, row):
            new_count += 1

    # Safe now, and only now: the adapter confirmed it parsed a real response, so
    # an absent key genuinely means the req came down.
    closed = outreach.close_absent_evidence(
        conn, target_id=target["id"], fact_kind="open_role",
        seen_keys=list(seen_keys), today=today,
    )

    # Opportunistic: an open *leadership* req names the function the templates
    # substitute into. Fills a NULL only, so an operator correction is permanent.
    outreach.backfill_function(
        conn, target["id"], [r["title"] for r in result.roles]
    )
    distinct = len(seen_keys)
    listings = "" if distinct == len(result.roles) else f" from {len(result.roles)} listings"
    logger.info(
        "outreach: %s — %d open role(s)%s (%d new, %d closed) via %s",
        target["company_name"], distinct, listings, new_count, closed, result.provider,
    )
    return {
        "status": "polled", "roles": distinct,
        "new": new_count, "closed": closed,
    }


def poll(today: date | None = None) -> dict[str, int]:
    """Poll every pollable target. Returns run totals."""
    today = today or date.today()
    totals = {"targets": 0, "polled": 0, "skipped": 0, "new": 0, "closed": 0}
    with db.connection() as conn:
        targets = outreach.pollable_targets(conn)
        totals["targets"] = len(targets)
        logger.info("outreach: %d target(s) with a careers URL", len(targets))
        for target in targets:
            try:
                summary = poll_target(conn, target, today=today)
            except Exception:
                # One bad target never stops the sweep; its evidence simply keeps
                # its previous last_seen_at and ages into the amber tier.
                logger.exception("outreach: target %s failed", target["id"])
                totals["skipped"] += 1
                continue
            if summary["status"] == "polled":
                totals["polled"] += 1
                totals["new"] += int(summary["new"])
                totals["closed"] += int(summary["closed"])
            else:
                totals["skipped"] += 1
    logger.info(
        "outreach: poll complete — %(polled)d polled, %(skipped)d skipped, "
        "%(new)d new fact(s), %(closed)d closed", totals,
    )
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Poll outreach targets' job boards for open-role evidence (Track O)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list pollable targets and the board detected for each, then exit",
    )
    args = parser.parse_args()

    if args.dry_run:
        with db.connection() as conn:
            targets = outreach.pollable_targets(conn)
        for t in targets:
            detected = adapters.detect_board(t["careers_url"])
            board = f"{detected[0]}:{detected[1]}" if detected else "UNSUPPORTED"
            print(f"  {t['company_name']:<30} {board:<24} {t['careers_url']}")
        print(f"{len(targets)} target(s) with a careers URL")
        return 0

    poll()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        db.close_pool()
