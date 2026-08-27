"""Weekly re-score sweep — Trent Crimm's deterministic sibling (Track O, §14).

Runs Sunday 18:00, an hour before `outreach-watch`. Two jobs, both pure SQL — no
LLM, and it **never changes a target** (outcome 3): it records and surfaces.

  1. **Record band crossings** (O1). A target's `treatment` is derived live in
     `v_outreach_scored`, so nothing is stored to "recompute" — the phrase in
     `35-` §14 is a leftover. What is real is the *record* of a crossing: each
     week, compare a target's band now against its band a week ago, recomputed
     as-of (`outreach_s1(trigger_date, asof-7)` + the stored S2–S5), and write one
     `outreach_events` row per crossing, naming both bands, both scores, and both
     as-of dates. A score that moved without crossing a boundary writes nothing.

  2. **Surface stale judgements** (O2). A target whose S4/S5 judgement is over 30
     days old (`signals_stale`) gets one re-check card, raised exactly once until
     it is resolved. The card asks; `cli/outreach_score` records — the sweep does
     not judge.

**O1's accepted cost, made honest in the payload.** The previous band is
recomputed from *this* week's S2–S5, so an operator who re-judged S4 mid-week has
that edit attributed to the sweep as an S1 band change. Both as-of dates are in
the payload so a reader can see what was compared rather than assume it was S1.

**O4 — the sweep does not card band changes.** An upward crossing into `work` is
already carded by the intake poll (candidate + `treatment='work'`); a downward
crossing is record-only. So the sweep's only card is the stale-signal re-check.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7

# The v_outreach_scored bands, replicated for the as-of recompute.
WORK_THRESHOLD = 20
WATCH_THRESHOLD = 14

STALE_SOURCE_TYPE = "outreach_stale_signal"


def _band(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= WORK_THRESHOLD:
        return "work"
    if score >= WATCH_THRESHOLD:
        return "watch"
    return "drop"


def band_changes(conn: object, asof: date) -> list[dict[str, Any]]:
    """Targets whose band now differs from a week ago (recomputed as-of).

    One query computes both scores using `outreach_s1` at two as-of dates plus the
    stored S2–S5; Python bands them and keeps only the crossings. A target with an
    incomplete score (any S2–S5 null) has a null band on one or both sides and is
    skipped, not crashed on.
    """
    prev = asof - timedelta(days=LOOKBACK_DAYS)
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT id, company_name,
                   CASE WHEN s2_stage_fit IS NULL OR s3_sector_match IS NULL
                          OR s4_leadership_gap IS NULL OR s5_team_build_below IS NULL
                        THEN NULL
                        ELSE outreach_s1(trigger_date, %(asof)s)
                             + s2_stage_fit + s3_sector_match
                             + s4_leadership_gap + s5_team_build_below END AS score_now,
                   CASE WHEN s2_stage_fit IS NULL OR s3_sector_match IS NULL
                          OR s4_leadership_gap IS NULL OR s5_team_build_below IS NULL
                        THEN NULL
                        ELSE outreach_s1(trigger_date, %(prev)s)
                             + s2_stage_fit + s3_sector_match
                             + s4_leadership_gap + s5_team_build_below END AS score_prev
            FROM outreach_targets
            WHERE status NOT IN ('archived', 'dropped')
            """,
            {"asof": asof, "prev": prev},
        )
        rows = cur.fetchall()

    changes: list[dict[str, Any]] = []
    for row in rows:
        band_now = _band(row["score_now"])
        band_prev = _band(row["score_prev"])
        if band_now is None or band_prev is None or band_now == band_prev:
            continue
        changes.append({
            "id": row["id"], "company_name": row["company_name"],
            "band_prev": band_prev, "band_now": band_now,
            "score_prev": row["score_prev"], "score_now": row["score_now"],
            "asof": asof.isoformat(), "asof_prev": prev.isoformat(),
        })
    return changes


def record_band_changes(conn: object, asof: date) -> int:
    """Write one `outreach_events` row per crossing. Never touches the target.

    Written directly rather than through the audit trigger, because this is a
    derived observation about a target, not a change to its row — the trigger
    fires on row writes, and outcome 3 forbids those.
    """
    changes = band_changes(conn, asof)
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        for c in changes:
            cur.execute(
                """
                INSERT INTO outreach_events (entity_table, entity_id, op, actor, changed)
                VALUES ('outreach_targets', %(id)s, 'RESCORE', session_user,
                        jsonb_build_object(
                            'treatment', jsonb_build_object(
                                'from', %(band_prev)s, 'to', %(band_now)s),
                            'score', jsonb_build_object(
                                'from', %(score_prev)s, 'to', %(score_now)s),
                            'asof', jsonb_build_object(
                                'now', %(asof)s, 'prev', %(asof_prev)s)))
                """,
                c,
            )
    if changes:
        up = sum(1 for c in changes if c["band_now"] == "work")
        logger.info("rescore: recorded %d band change(s), %d upward into work "
                    "(carded by the intake poll, not here)", len(changes), up)
    return len(changes)


def raise_stale_signals(conn: object) -> int:
    """Raise a re-check card for each stale target that has no open one (O2).

    Idempotent by construction: a target with a pending `outreach_stale_signal`
    candidate is skipped, so a second sweep raises nothing (outcome 2). Resolving
    or deciding the card clears `status='pending'`, which reopens eligibility only
    when the judgement goes stale again.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT v.id, v.company_name, v.signals_observed_at
            FROM v_outreach_scored v
            WHERE v.signals_stale
              AND v.status NOT IN ('archived', 'dropped')
              AND NOT EXISTS (
                  SELECT 1 FROM task_candidates tc
                  WHERE tc.source_type = %(kind)s
                    AND tc.source_ref = v.id::text
                    AND tc.status = 'pending')
            """,
            {"kind": STALE_SOURCE_TYPE},
        )
        stale = cur.fetchall()
        for target in stale:
            observed = target["signals_observed_at"]
            cur.execute(
                """
                INSERT INTO task_candidates
                    (proposed_action, source_type, source_ref, evidence_text,
                     confidence, status)
                VALUES ('re-check S4/S5 leadership signals', %(kind)s, %(ref)s,
                        %(text)s, 0.5, 'pending')
                """,
                {"kind": STALE_SOURCE_TYPE, "ref": str(target["id"]),
                 "text": f"{target['company_name']}: leadership judgement last "
                         f"set {observed or 'never'} — over 30 days; re-check via "
                         f"`uv run python -m cli.outreach_score --target {target['id']}`"},
            )
    if stale:
        logger.info("rescore: raised %d stale-signal re-check(s)", len(stale))
    return len(stale)


def sweep(asof: date | None = None, *, dry_run: bool = False) -> dict[str, int]:
    """One weekly sweep. Records band changes and raises stale-signal cards."""
    asof = asof or date.today()
    with db.connection() as conn:
        if dry_run:
            changes = band_changes(conn, asof)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM v_outreach_scored "
                    "WHERE signals_stale AND status NOT IN ('archived','dropped')")
                stale = cur.fetchone()["n"]
            return {"band_changes": len(changes), "stale_signals": stale}
        return {"band_changes": record_band_changes(conn, asof),
                "stale_signals": raise_stale_signals(conn)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Count what would be recorded/raised; write nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    totals = sweep(dry_run=args.dry_run)
    verb = "would record/raise" if args.dry_run else "recorded/raised"
    print(f"rescore: {verb} {totals['band_changes']} band change(s), "
          f"{totals['stale_signals']} stale-signal re-check(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
