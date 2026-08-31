"""Stale-signal re-check surface — the O2 modal's testable core (Track O, `35-` §4).

The weekly re-score sweep (`agents/outreach/rescore.py`) raises one
`outreach_stale_signal` `task_candidates` row per target whose S4/S5 leadership
judgement is over 30 days old (`v_outreach_scored.signals_stale`). That was the
whole of O2 that existed; this module is the **consumer** — the read/write logic
behind the Discord modal that lets the operator re-enter S4/S5, which:

  * **resets the 30-day clock** — the write stamps `signals_observed_at =
    CURRENT_DATE`, so `signals_stale` goes false and the next sweep does not
    re-raise the target (that, plus the sweep's `NOT EXISTS pending` guard, is
    what makes O2 idempotent — rescore.py's outcome 2);
  * **resolves the candidate** — `status='done'`, so the card stops being posted
    and the row no longer blocks a future re-raise once it goes stale again.

Scoped to **S4/S5 only**, deliberately: S2 (stage) and S3 (sector) are structural
and do not go stale; the sweep's action text is literally "re-check S4/S5
leadership signals". The cog owns only the Discord surface; this owns the logic,
so it is testable without a bot (the `outreach_intake` split).
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

STALE_SOURCE_TYPE = "outreach_stale_signal"
VALID_SCORES = (1, 3, 5)

# The two signals that go stale, with the column each writes.
STALE_SIGNALS = {
    "s4": ("s4_leadership_gap", "Leadership gap"),
    "s5": ("s5_team_build_below", "Team build below"),
}

# A pending stale re-check joined to its target's current scored state. `v.*`
# gives the current S4/S5, the band, and `signals_observed_at` (how stale).
_UNDELIVERED_SQL = """
    SELECT tc.id AS candidate_id, tc.evidence_text, tc.discord_message_id,
           v.id AS target_id, v.company_name, v.s4_leadership_gap,
           v.s5_team_build_below, v.function_state, v.score, v.treatment,
           v.signals_observed_at, v.days_since_trigger
      FROM task_candidates tc
      JOIN v_outreach_scored v ON v.id = tc.source_ref::bigint
     WHERE tc.source_type = %(kind)s
       AND tc.status = 'pending'
       AND v.status NOT IN ('archived', 'dropped')
       {message_clause}
     ORDER BY v.signals_observed_at NULLS FIRST, v.company_name
"""


def _list(conn: object, *, posted: bool) -> list[dict[str, Any]]:
    clause = ("AND tc.discord_message_id IS NOT NULL" if posted
              else "AND tc.discord_message_id IS NULL")
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(_UNDELIVERED_SQL.format(message_clause=clause),
                    {"kind": STALE_SOURCE_TYPE})
        return cur.fetchall()


def list_undelivered(conn: object) -> list[dict[str, Any]]:
    """Pending stale re-checks not yet posted to Discord (the poll's work)."""
    return _list(conn, posted=False)


def list_posted_undecided(conn: object) -> list[dict[str, Any]]:
    """Posted-but-still-pending re-checks — the set to re-attach views to on
    startup, so a restart does not leave dead buttons (the Gate 0 lesson)."""
    return _list(conn, posted=True)


def get_recheck(conn: object, candidate_id: int) -> dict[str, Any] | None:
    """One pending re-check by candidate id, with the target's CURRENT S4/S5 — read
    fresh at click time so the modal pre-selects today's values and a
    since-resolved card reports 'already handled' rather than opening."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            _UNDELIVERED_SQL.format(message_clause="AND tc.id = %(cid)s"),
            {"kind": STALE_SOURCE_TYPE, "cid": candidate_id},
        )
        return cur.fetchone()


def mark_posted(conn: object, candidate_id: int, message_id: int) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE task_candidates SET discord_message_id = %s WHERE id = %s",
            (str(message_id), candidate_id),
        )


def apply_rescore(
    conn: object, candidate_id: int, target_id: int, s4: int, s5: int
) -> dict[str, Any] | None:
    """Record re-scored S4/S5 and resolve the candidate, atomically.

    Claims the candidate FIRST (`status='pending' → 'done'`, guarded) so a
    double-click cannot double-process — returns None if it was already decided.
    Then writes S4/S5 and stamps `signals_observed_at = CURRENT_DATE` (the O2 clock
    reset). Returns the target's new scored row (for the band-change reply), or
    None if the candidate was already resolved.
    """
    if s4 not in VALID_SCORES or s5 not in VALID_SCORES:
        raise ValueError(f"S4/S5 must be one of {VALID_SCORES}; got {s4}, {s5}")

    with conn.transaction():  # type: ignore[attr-defined]  # atomic under autocommit
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                "UPDATE task_candidates SET status = 'done', decided_at = now() "
                "WHERE id = %s AND status = 'pending' RETURNING id",
                (candidate_id,),
            )
            if cur.fetchone() is None:
                return None  # already decided — do not re-write scores
        with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
            # The write mirrors cli.outreach_score.apply_scores for S4/S5, and the
            # `signals_observed_at = CURRENT_DATE` stamp is the load-bearing part:
            # it is what un-stales the target so the sweep stops re-raising it.
            cur.execute(
                "UPDATE outreach_targets SET s4_leadership_gap = %(s4)s, "
                "s5_team_build_below = %(s5)s, signals_observed_at = CURRENT_DATE "
                "WHERE id = %(id)s RETURNING id",
                {"s4": s4, "s5": s5, "id": target_id},
            )
            if cur.fetchone() is None:
                raise KeyError(f"no outreach target {target_id}")
            cur.execute("SELECT * FROM v_outreach_scored WHERE id = %s", (target_id,))
            return cur.fetchone()
