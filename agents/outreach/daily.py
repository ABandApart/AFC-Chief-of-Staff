"""Outreach daily loop — 05:45, before the briefing (Track O, `35-` §14).

Three jobs, none of which call an LLM (`40-action-layer.md`, Outreach_loops):

1. **Regenerate packets** for every touch currently inside its window.
   *Regenerate, never edit* (§14): a packet is rebuilt from current state each
   morning, so evidence that aged or a fact that closed overnight is reflected
   rather than remembered. `assemble_packet` re-derives `ready` every time.
2. **Run the drain rule** (§8) — a sequence with all five touches resolved, no
   reply, and 14 days past its last window is finished. It moves to `watchlist`
   **only once `stalled_reason` is set**; until then it keeps its capacity slot.
   That friction is deliberate: an unanswered "what stalled it?" costs a slot,
   which is what makes the question get answered.
3. **Log the counts** the 06:00 briefing reads (§9's one line: due · live/cap ·
   not ready · ageing facts).

Runs at 05:45 so packets are fresh before the briefing quotes them.

Run (barry-agent):
    uv run python -m agents.outreach.daily --dry-run   # report, write nothing
    uv run python -m agents.outreach.daily
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db, packet

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# §8: a sequence is drained 14 days past the last window's close.
DRAIN_GRACE_DAYS = 14

# Marks a drain that ran itself. Distinct from the operator's own free-text
# reason so the two are never confused in the history.
DRAIN_REASON_PREFIX = "drained:"

# Touches whose window is open today and which are neither sent nor skipped.
# `snoozed_until` is respected — a snooze means "not today", and regenerating a
# packet for a snoozed touch would put it back in the operator's queue.
_DUE_TOUCHES_SQL = """
    SELECT tc.id, tc.slot, tc.template_code, t.company_name
    FROM outreach_touches tc
    JOIN outreach_targets t ON t.id = tc.target_id
    WHERE t.status = 'in_sequence'
      AND tc.sent_at IS NULL
      AND tc.skipped_at IS NULL
      AND tc.window_opens <= %(today)s
      AND tc.window_closes >= %(today)s
      AND (tc.snoozed_until IS NULL OR tc.snoozed_until <= %(today)s)
    ORDER BY tc.due_date, tc.id
"""

# Sequences that have run their course. Every touch resolved (sent XOR skipped),
# nothing replied, and past the grace window.
_DRAINABLE_SQL = """
    SELECT t.id, t.company_name, t.stalled_reason,
           max(tc.window_closes) AS last_window,
           %(today)s::date - max(tc.window_closes) AS days_past
    FROM outreach_targets t
    JOIN outreach_touches tc ON tc.target_id = t.id
    WHERE t.status = 'in_sequence'
    GROUP BY t.id
    HAVING count(*) FILTER (WHERE tc.sent_at IS NULL AND tc.skipped_at IS NULL) = 0
       AND count(*) FILTER (WHERE tc.replied_at IS NOT NULL) = 0
       AND %(today)s::date - max(tc.window_closes) > %(grace)s
    ORDER BY t.id
"""

# What §9's briefing line needs. One query so the numbers are consistent with
# each other — computed separately they could disagree across a concurrent write.
_COUNTS_SQL = """
    SELECT
      (SELECT count(*) FROM outreach_touches tc
        JOIN outreach_targets t ON t.id = tc.target_id
        WHERE t.status = 'in_sequence' AND tc.sent_at IS NULL AND tc.skipped_at IS NULL
          AND tc.window_opens <= CURRENT_DATE AND tc.window_closes >= CURRENT_DATE
      ) AS touches_due,
      (SELECT cold_live FROM v_outreach_capacity)         AS cold_live,
      (SELECT cold_ceiling FROM v_outreach_capacity)      AS cold_ceiling,
      (SELECT count(*) FROM outreach_targets
        WHERE status = 'candidate' AND intake_message_id IS NOT NULL) AS cards_open,
      (SELECT count(DISTINCT e.target_id) FROM v_outreach_evidence_display e
        WHERE e.freshness IN ('ageing', 'stale') AND e.closed_at IS NULL
      ) AS targets_with_ageing_evidence,
      -- Gate 0 (Part 0). Counted here rather than in a second query so every
      -- number in the line is consistent with the others across a concurrent write.
      (SELECT count(*) FROM outreach_discoveries
        WHERE reviewed_at IS NULL
          AND COALESCE(array_length(verified_on, 1), 0) >= 2) AS awaiting_review
"""


def is_drain_reason(reason: str | None) -> bool:
    """True if `stalled_reason` was written by the drain rather than a human."""
    return bool(reason) and reason.startswith(DRAIN_REASON_PREFIX)


def regenerate_packets(conn: object, *, today: date, dry_run: bool = False) -> dict[str, int]:
    """Rebuild the packet for every touch inside its window.

    A failure on one touch must not cost the others their morning packet — a
    missing template or a malformed row is that touch's problem.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_DUE_TOUCHES_SQL, {"today": today})
        due = cur.fetchall()

    built = ready = failed = 0
    for row in due:
        try:
            touch, target, evidence = packet.fetch_packet_inputs(conn, row["id"])
            assembled = packet.assemble_packet(
                touch, target, evidence, today=today,
                original_subject=packet.previous_subject(conn, touch),
            )
            if not dry_run:
                packet.save_packet(conn, assembled)
            built += 1
            if assembled.ready:
                ready += 1
            else:
                logger.info(
                    "outreach-daily: %s slot %s not ready — %s",
                    row["company_name"], row["slot"], "; ".join(assembled.blockers),
                )
        except Exception:
            failed += 1
            logger.exception(
                "outreach-daily: packet failed for touch %s (%s slot %s)",
                row["id"], row["company_name"], row["slot"],
            )
    return {"due": len(due), "built": built, "ready": ready, "failed": failed}


def run_drain(conn: object, *, today: date, dry_run: bool = False) -> dict[str, Any]:
    """Move finished sequences to the watchlist, or report the ones still owed a reason.

    A drainable target with `stalled_reason` already set transitions. One without
    keeps its capacity slot and is reported — the stalled-reason card (a free-text
    Task Tinder prompt, §9) is what collects the answer, and it is not built yet,
    so for now this surfaces the list rather than silently parking them.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_DRAINABLE_SQL, {"today": today, "grace": DRAIN_GRACE_DAYS})
        drainable = cur.fetchall()

    drained: list[str] = []
    awaiting: list[str] = []
    for row in drainable:
        if not row["stalled_reason"]:
            awaiting.append(row["company_name"])
            logger.warning(
                "outreach-daily: %s finished its arc %s days ago but has no "
                "stalled_reason — it keeps its capacity slot until answered",
                row["company_name"], row["days_past"],
            )
            continue
        if not dry_run:
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                # Guarded on in_sequence: if something else moved it meanwhile
                # (a reply landing mid-run), this updates nothing.
                cur.execute(
                    "UPDATE outreach_targets SET status = 'watchlist', "
                    "watch_until = %s + interval '18 months', "
                    "sequence_completed_at = COALESCE(sequence_completed_at, %s) "
                    "WHERE id = %s AND status = 'in_sequence'",
                    (today, row["last_window"], row["id"]),
                )
                if not cur.rowcount:
                    continue
        drained.append(row["company_name"])
        logger.info("outreach-daily: %s drained to watchlist", row["company_name"])

    return {"drained": drained, "awaiting_reason": awaiting}


def briefing_counts(conn: object) -> dict[str, Any]:
    """The numbers §9's briefing line quotes."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_COUNTS_SQL)
        return cur.fetchone()


def format_briefing_line(counts: dict[str, Any], not_ready: int) -> str:
    """§9: 'One line and a link.' Empty when there is nothing to say.

    **Every clause is conditional.** An earlier version always printed the due
    and live counts and appended the rest, which on real data produced
    `0 touch(es) due · 0/15 live · 1 card(s) awaiting a decision` — padding the
    one real signal with two zeros. The briefing has a hard message budget
    (eval UX-1), so a clause reading "nothing happened" is worse than absent.
    """
    parts: list[str] = []
    if counts["touches_due"]:
        parts.append(f"{counts['touches_due']} touch(es) due")
    if counts["cold_live"]:
        parts.append(f"{counts['cold_live']}/{counts['cold_ceiling']} live")
    if counts["cards_open"]:
        parts.append(f"{counts['cards_open']} card(s) awaiting a decision")
    # Gate 0 is a triage queue, not a decision that ages, so it reads after the
    # Gate 1 cards — which are the ones that actually hold up the pipeline.
    if counts.get("awaiting_review"):
        parts.append(f"{counts['awaiting_review']} to review")
    if not_ready:
        parts.append(f"{not_ready} not ready")
    if counts["targets_with_ageing_evidence"]:
        parts.append(f"{counts['targets_with_ageing_evidence']} with ageing evidence")
    if not parts:
        return ""
    return "🎯 **Outreach:** " + " · ".join(parts)


def run(today: date | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    today = today or date.today()
    with db.connection() as conn:
        packets = regenerate_packets(conn, today=today, dry_run=dry_run)
        drain = run_drain(conn, today=today, dry_run=dry_run)
        counts = briefing_counts(conn)

    not_ready = packets["built"] - packets["ready"]
    logger.info(
        "outreach-daily: %d packet(s) due, %d built, %d ready, %d failed · "
        "drained %d, %d awaiting a stalled reason",
        packets["due"], packets["built"], packets["ready"], packets["failed"],
        len(drain["drained"]), len(drain["awaiting_reason"]),
    )
    if line := format_briefing_line(counts, not_ready):
        logger.info("outreach-daily: briefing line → %s", line)
    else:
        logger.info("outreach-daily: nothing live — briefing omits the outreach line")
    return {"packets": packets, "drain": drain, "counts": counts,
            "briefing_line": format_briefing_line(counts, not_ready)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble due packets, drain finished sequences, report counts."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen; write nothing")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run)
    if args.dry_run:
        print(f"  packets due : {result['packets']['due']}")
        print(f"  would build : {result['packets']['built']} "
              f"({result['packets']['ready']} ready)")
        print(f"  drainable   : {len(result['drain']['drained'])} "
              f"(+{len(result['drain']['awaiting_reason'])} awaiting a reason)")
        print(f"  briefing    : {result['briefing_line'] or '(omitted — nothing live)'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        db.close_pool()
