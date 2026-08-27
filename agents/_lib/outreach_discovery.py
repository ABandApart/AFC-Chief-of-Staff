"""Gate 0 — the discovery decision core (Track O, Part 0).

The human gate *above* Gate 1. It asks "is this firm worth tracking?", where
`35-` §5 asks "do we start a five-touch arc?". Discord-free by design, exactly
like `_lib/outreach_intake` and `_lib/task_tinder`: the decision rules and the
guarded writes live here so they are unit-testable without a bot, and the cog owns
only the surface.

**Gate 0 does not touch capacity.** `35-` §8 caps `cold_live` at 15 concurrent
sequences, enforced at intake. Accepting 20 firms a day is affordable precisely
because an accept costs a row and nothing else - no touch, no capacity slot, no
Gate 1 card. That separation is the whole reason the daily 20 is not absurd
against a ceiling of 15 (R0.6).

**Three outcomes**, from the review modal (R0.15):

  * **Accept** - the firm joins the pool. It becomes an `outreach_targets` row
    only when a real trigger is observed (R0.3, `promote`).
  * **Reject** - recorded with a structured reason, which is the training label
    Part 4 reads. Never a delete.
  * **Defer** - no decision, and deliberately **no label** (OQ-H). A deferral
    usually means a missing field rather than a judgement about the firm.

**Idempotency is the database's**, not this module's: every transition is
`UPDATE ... WHERE reviewed_at IS NULL`, so a double-submit updates zero rows and
returns None. Same guarantee the intake cog relies on.

**The reason rules are CHECK constraints, not promises here** (0018). This module
validates early to produce a usable error, but the database is what makes a
reason-less rejection impossible - because once NocoDB can edit these rows
directly, nothing mediates the write.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db, outreach
from agents.outreach import icp

logger = logging.getLogger(__name__)

# R0.11 / R0.18: the daily window is a ceiling, not a quota. A day producing fewer
# than this surfaces fewer and says why; padding it with unverified firms would
# defeat the verification rule that lets the operator trust the queue at all.
#
# Three buckets, filled in order, each excluding rows already picked:
#   unscored (5) -> exploration reserve (5) -> ranked (15)
DAILY_WINDOW = 25

DECISIONS = frozenset({"accept", "reject", "defer"})

# R0.7. Each routes to a different knob in Part 4 (R4.4) - `wrong_segment` is
# evidence about the sourcing queries, `poor_contact_path` is about Part 3 and
# says nothing about the firm. Pooling them into one accept-rate would discard
# exactly the information this enum was collected for.
REJECT_REASONS = frozenset({
    "wrong_segment",
    "too_small",
    "too_large",
    "no_pain_signal",
    "poor_contact_path",
    "geography",
    "competitor_or_conflict",
    "already_known",
    "other",
})

# R0.5: a firm is surfaced only on at least two independent kinds of evidence.
# Named for what is actually checked, not what R0.5's prose claimed - see the
# module docstring of `agents/outreach/verify.py` for the three corrections.
# `open_req` is the strongest: a live fetch against a structured API.
VERIFICATION_KINDS = frozenset({
    "live_site",              # the site answered a request. Recency NOT verified
    "open_req",               # a supported ATS board returned >= 1 open role
    "third_party_dated",      # an award/ranking/press citation supplied by the sourcer
    "linkedin_url_present",   # a company LinkedIn URL is on file. NEVER fetched (R14)
})
MIN_VERIFICATION_KINDS = 2

# R0.17 / OQ-G: reserved for UNDER-SAMPLED segments - too few labels to report an
# accept rate. Pure exploitation is self-confirming: a segment with no history
# ranks last, is never surfaced, never acquires the labels that would prove it,
# and the loop concludes it is poor by never having looked. Set to 0 to disable.
EXPLORATION_RESERVE_SLOTS = 5

# R0.18 / OQ-K: reserved for UNSCORED segments - ones the operator has never rated
# on the six workbook criteria, so every candidate in them inherits a prior rather
# than a judgement. A DIFFERENT axis from under-sampled, which is why it is a
# separate bucket: under-sampled is about decisions not yet made, unscored is
# about a rating never given. A segment can be either, both, or neither.
#
# Empty until sourcing exists - all 49 imported rows are in scored segments.
UNSCORED_SEGMENT_SLOTS = 5

# R4.2: below this many labelled decisions a segment's accept rate is not
# reportable, so the segment still counts as under-sampled and keeps its claim on
# the reserve. Part 4 owns the step-down; it happens on its own as labels accrue.
MIN_SAMPLE_FOR_RATE = 30

_COLUMNS = """
    id, company_name, company_domain, company_url, careers_url, segment, country,
    hq_location, headcount_band, arr_estimate_low, arr_estimate_high, arr_basis,
    description, icp_fit_score, icp_model_version, contact_name, contact_title,
    contact_email, email_confidence, company_linkedin_url, contact_linkedin_url,
    verification_note, verified_on, pain_layer, pain_hook, discovered_via,
    discovery_query, discovered_at, surfaced_at, review_message_id, reviewed_at,
    review_decision, reject_reason, reject_note, promoted_target_id, source_url
"""


class NotPromotableError(Exception):
    """The discovery cannot become a target yet.

    Not an error condition - it is R0.3 working. A discovered firm has no trigger
    until one is observed, and `outreach_targets.trigger_kind`/`.trigger_date`
    are NOT NULL for a reason the database already carries the scars of: the 14
    live targets all share `trigger_date` 2026-06-10 because an import stamped a
    batch date into a field that means "when the trigger happened".
    """


def validate_decision(action: str, reason: str | None, note: str | None) -> None:
    """Reject a malformed decision here so the caller gets a usable message.

    The database enforces the same rules independently (0018); this is the early,
    friendly copy, not the authority.
    """
    if action not in DECISIONS:
        raise ValueError(f"unknown Gate 0 action: {action!r}")
    if action == "reject":
        if not reason:
            raise ValueError("a rejection needs a reason (R0.7)")
        if reason not in REJECT_REASONS:
            raise ValueError(f"unknown reject reason: {reason!r}")
        if reason == "other" and not (note or "").strip():
            raise ValueError("reject reason 'other' needs a note")
    elif reason:
        raise ValueError(f"{action!r} must not carry a reject reason")


def _eligible(conn: object) -> list[dict[str, Any]]:
    """Every unreviewed candidate that clears the verification bar, best first."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"""
            SELECT {_COLUMNS} FROM outreach_discoveries
            WHERE reviewed_at IS NULL
              AND COALESCE(array_length(verified_on, 1), 0) >= %s
            ORDER BY icp_fit_score DESC NULLS LAST, discovered_at
            """,
            (MIN_VERIFICATION_KINDS,),
        )
        return cur.fetchall()


CRITERIA_COLUMNS = ("market_size", "market_growth", "firm_profitability",
                    "ability_to_pay", "urgency_pain", "offering_fit")


def entered_segment_scores(conn: object) -> dict[str, dict[str, int]]:
    """Operator-entered criteria per segment (R0.20).

    Loaded once per run and passed into `icp`, which stays pure. An empty dict
    means nothing has been rated yet and every segment falls back to the
    workbook transcription.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"SELECT segment, {', '.join(CRITERIA_COLUMNS)} "
            f"FROM outreach_segment_scores"
        )
        return {
            row["segment"]: {c: row[c] for c in CRITERIA_COLUMNS}
            for row in cur.fetchall()
        }


def record_segment_score(conn: object, segment: str, criteria: dict[str, int],
                         rationale: str | None = None) -> None:
    """Upsert one segment's rating. The affordance OQ-L asked to stand open.

    Re-rating a segment overwrites rather than versions: the audit trigger
    already records the before/after with a timestamp and an actor, so history is
    kept without a second table.
    """
    missing = [c for c in CRITERIA_COLUMNS if c not in criteria]
    if missing:
        raise ValueError(f"segment rating is missing {', '.join(missing)}")
    columns = ", ".join(CRITERIA_COLUMNS)
    placeholders = ", ".join(f"%({c})s" for c in CRITERIA_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in CRITERIA_COLUMNS)
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"INSERT INTO outreach_segment_scores (segment, {columns}, rationale) "
            f"VALUES (%(segment)s, {placeholders}, %(rationale)s) "
            f"ON CONFLICT (segment) DO UPDATE SET {updates}, "
            f"    rationale = EXCLUDED.rationale, rated_at = now()",
            {"segment": segment, "rationale": rationale, **criteria},
        )


def segment_label_counts(conn: object) -> dict[str, int]:
    """Labelled decisions per segment. Deferrals are not labels (OQ-H), and the
    query reflects that: only rows carrying a `review_decision` count."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT segment, count(*) FROM outreach_discoveries "
            "WHERE review_decision IS NOT NULL GROUP BY segment"
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def _reserve_picks(
    candidates: list[dict[str, Any]],
    labels: dict[str, int],
    slots: int,
    taken: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Round-robin the reserve across under-sampled segments (R0.17).

    Ordered by fewest labels, then by the segment's BEST ICP score ascending, so
    the segments that ranking would exclude are served first. Ordering by name
    instead looks deterministic but is actively wrong: with every segment at zero
    labels on day one, it hands the reserve to whichever segment sorts first
    alphabetically, which can be the segment ranking already dominates. The
    reserve exists to reach what ranking will not.

    Each pass takes each segment's best-ranked remaining candidate.
    """
    if slots <= 0:
        return []
    taken = taken or set()
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        if row["id"] in taken:
            continue
        by_segment.setdefault(row["segment"], []).append(row)
    if not by_segment:
        return []

    def best_score(segment: str) -> int:
        return max((r["icp_fit_score"] or 0) for r in by_segment[segment])

    under = [
        seg for seg in by_segment
        if labels.get(seg, 0) < MIN_SAMPLE_FOR_RATE
    ]
    under.sort(key=lambda seg: (labels.get(seg, 0), best_score(seg), seg))

    picks: list[dict[str, Any]] = []
    cursors = dict.fromkeys(under, 0)
    while len(picks) < slots and under:
        progressed = False
        for segment in under:
            if len(picks) >= slots:
                break
            index = cursors[segment]
            if index < len(by_segment[segment]):
                picks.append(by_segment[segment][index])
                cursors[segment] = index + 1
                progressed = True
        if not progressed:
            break  # every under-sampled segment is exhausted
    return picks


def _unscored_picks(
    candidates: list[dict[str, Any]],
    slots: int,
    taken: set[int],
    entered: dict[str, dict[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Candidates from segments the operator has never rated (R0.18).

    Round-robin across unscored segments, best-ranked first within each, so no
    single unscored segment can take the whole bucket.
    """
    if slots <= 0:
        return []
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        if row["id"] in taken:
            continue
        if icp.is_scored(row["segment"], entered):
            continue  # rated in the workbook or by the operator (R0.20)
        by_segment.setdefault(row["segment"], []).append(row)

    picks: list[dict[str, Any]] = []
    cursors = dict.fromkeys(by_segment, 0)
    while len(picks) < slots and by_segment:
        progressed = False
        for segment in sorted(by_segment):
            if len(picks) >= slots:
                break
            index = cursors[segment]
            if index < len(by_segment[segment]):
                picks.append(by_segment[segment][index])
                cursors[segment] = index + 1
                progressed = True
        if not progressed:
            break
    return picks


def list_for_review(conn: object, limit: int = DAILY_WINDOW) -> list[dict[str, Any]]:
    """The daily window: unreviewed, verified, in three buckets (R0.18).

    `limit` is a ceiling, not a quota (R0.11) - a day with fewer verified
    candidates surfaces fewer rather than padding the queue with unverified
    firms, which would defeat the verification bar that makes the queue
    trustworthy at all.

    Buckets fill in order and each excludes rows already picked, so a candidate
    never occupies two slots:

      1. **Unscored segments** (`UNSCORED_SEGMENT_SLOTS`) - segments the operator
         has never rated on the six criteria (R0.18).
      2. **Exploration reserve** (`EXPLORATION_RESERVE_SLOTS`) - segments under
         R4.2's label minimum (R0.17).
      3. **Ranked** - whatever remains, by ICP fit.

    **Unfillable slots fall back to the ranked list**: returning a short window to
    honour a bucket would waste the scarcest thing here, which is review
    attention. The buckets guarantee inclusion, not position - the returned window
    is ordered by fit so the operator still reads best-first.
    """
    candidates = _eligible(conn)
    if not candidates:
        return []

    picks: list[dict[str, Any]] = []
    chosen_ids: set[int] = set()

    def take(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if len(picks) >= limit:
                return
            if row["id"] not in chosen_ids:
                picks.append(row)
                chosen_ids.add(row["id"])

    # Buckets are capped against `limit` as they fill, NOT trimmed afterwards.
    # Trimming at the end sorts by fit and drops the tail - which is precisely
    # the low-ranked bucket pick the bucket exists to protect. On a small pool
    # that silently undid the whole mechanism.
    entered = entered_segment_scores(conn)
    take(_unscored_picks(candidates, min(UNSCORED_SEGMENT_SLOTS, limit),
                         chosen_ids, entered))
    take(_reserve_picks(
        candidates, segment_label_counts(conn),
        min(EXPLORATION_RESERVE_SLOTS, limit - len(picks)), chosen_ids,
    ))
    take(candidates)

    picks.sort(key=lambda r: (-(r["icp_fit_score"] or 0), r["discovered_at"]))
    return picks


def page_rows(conn: object, message_id: str) -> list[dict[str, Any]]:
    """Every row carried by one posted message, decided or not.

    Distinct from `surfaced_pages` on purpose. That one is bounded — it drops
    fully-decided messages so the startup re-attach does not grow forever. This
    one must NOT filter: refreshing a card after the last undecided row on it is
    decided is exactly when the page disappears from `surfaced_pages`, and
    skipping the edit there would leave that final row showing an enabled
    "Review" button for a decision already recorded.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"""
            SELECT {_COLUMNS} FROM outreach_discoveries
            WHERE review_message_id = %s
            ORDER BY icp_fit_score DESC NULLS LAST, discovered_at
            """,
            (message_id,),
        )
        return cur.fetchall()


# --- contact correction (interim; NocoDB owns "correct" once it lands) --------
#
# `35-` §9 assigns correcting records to NocoDB, which is increment 3 and gated on
# install, a dedicated role and Tailscale Serve. This is the INTERIM path, kept
# deliberately narrow — contact fields only — so it does not grow into a second
# editor competing with that surface (operator decision, 2026-08-21).

# The discovery column -> the target column. Mostly identical; `contact_title`
# and `contact_role` are the same thing under two names, which is exactly the
# kind of mismatch that silently drops an edit if it is not written down.
CONTACT_FIELD_MAP = {
    "contact_name": "contact_name",
    "contact_title": "contact_role",
    "contact_email": "contact_email",
    "contact_linkedin_url": "contact_linkedin_url",
    "email_confidence": "email_confidence",
}
CONTACT_FIELDS = tuple(CONTACT_FIELD_MAP)

EMAIL_CONFIDENCE = ("public", "operator_verified", "inferred_pattern",
                    "general_inbox")


def contact_record(conn: object, domain: str) -> dict[str, Any] | None:
    """Current contact fields for a company, from whichever table(s) hold it.

    **One surface, both record types** (operator decision, 2026-08-21). Keyed on
    `company_domain` because both tables are unique on it and it is the identity
    R0.10 already relies on.

    This is not hypothetical tidiness: all 14 current targets came from the CSV
    import and have **no** discovery row, so a pool-only editor would reach none
    of the firms closest to actually being contacted.

    Where both exist, the TARGET's values are returned — it is what packet
    assembly reads, so it is the one whose staleness would reach a recipient.
    """
    normalized = outreach.normalize_domain(domain)
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT d.id AS discovery_id, t.id AS target_id,
                   COALESCE(t.company_name, d.company_name) AS company_name,
                   COALESCE(t.company_name, d.company_name) IS NOT NULL AS found,
                   COALESCE(t.contact_name, d.contact_name)                 AS contact_name,
                   COALESCE(t.contact_role, d.contact_title)                AS contact_title,
                   COALESCE(t.contact_email, d.contact_email)               AS contact_email,
                   COALESCE(t.contact_linkedin_url, d.contact_linkedin_url) AS contact_linkedin_url,
                   COALESCE(t.email_confidence, d.email_confidence)         AS email_confidence
            FROM (SELECT %(domain)s::text AS company_domain) k
            LEFT JOIN outreach_discoveries d ON d.company_domain = k.company_domain
            LEFT JOIN outreach_targets     t ON t.company_domain = k.company_domain
            """,
            {"domain": normalized},
        )
        row = cur.fetchone()
    if row is None or not row["found"]:
        return None
    row["company_domain"] = normalized
    return row


def search_contacts(conn: object, term: str, limit: int = 25) -> list[dict[str, Any]]:
    """Company name/domain matches across both tables, for autocomplete."""
    like = f"%{(term or '').strip().lower()}%"
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT company_name, company_domain FROM (
                SELECT company_name, company_domain FROM outreach_discoveries
                UNION
                SELECT company_name, company_domain FROM outreach_targets
            ) matches                 -- NOT `both`: a Postgres reserved word
            WHERE lower(company_name) LIKE %(like)s
               OR lower(company_domain) LIKE %(like)s
            ORDER BY company_name
            LIMIT %(limit)s
            """,
            {"like": like, "limit": limit},
        )
        return cur.fetchall()


def update_contact(domain: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Write corrected contact fields to every record for this company.

    Both tables in ONE transaction, so a promoted firm can never end up with a
    corrected pool row and a stale target — the target being the one packet
    assembly reads, and therefore the one whose staleness reaches a recipient.

    `contact_first_name` is re-derived when the name changes: it is what the
    templates substitute into a greeting, so leaving it stale would put the wrong
    first name at the top of an email.

    Blank input clears nothing. Every field is optional and only a non-empty
    value overwrites, so a half-filled modal cannot wipe what it did not carry.
    """
    unknown = set(fields) - set(CONTACT_FIELDS)
    if unknown:
        raise ValueError(f"not editable contact fields: {', '.join(sorted(unknown))}")
    confidence = fields.get("email_confidence")
    if confidence and confidence not in EMAIL_CONFIDENCE:
        raise ValueError(f"unknown email confidence: {confidence!r}")

    clean = {
        key: outreach.clean_field(value, max_chars=300)
        for key, value in fields.items()
    }
    clean = {k: v for k, v in clean.items() if v is not None}
    if not clean:
        return {"changed": [], "discovery": False, "target": False}

    with db.connection() as conn:
        record = contact_record(conn, domain)
        if record is None:
            raise KeyError(f"no discovery or target for {domain!r}")

        with conn.transaction():
            if record["discovery_id"]:
                sets = ", ".join(f"{c} = %({c})s" for c in clean)
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE outreach_discoveries SET {sets} WHERE id = %(id)s",
                        {**clean, "id": record["discovery_id"]},
                    )
            if record["target_id"]:
                mapped = {CONTACT_FIELD_MAP[k]: v for k, v in clean.items()}
                if "contact_name" in clean:
                    mapped["contact_first_name"] = outreach.suggest_first_name(
                        clean["contact_name"]
                    )
                sets = ", ".join(f"{c} = %({c})s" for c in mapped)
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE outreach_targets SET {sets} WHERE id = %(id)s",
                        {**mapped, "id": record["target_id"]},
                    )

    logger.info("gate 0: corrected %s on %s (discovery=%s target=%s)",
                ", ".join(sorted(clean)), record["company_name"],
                bool(record["discovery_id"]), bool(record["target_id"]))
    return {
        "changed": sorted(clean),
        "company_name": record["company_name"],
        "discovery": bool(record["discovery_id"]),
        "target": bool(record["target_id"]),
    }


def surfaced_pages(conn: object) -> dict[str, list[dict[str, Any]]]:
    """Rows grouped by the message carrying them, for still-actionable messages.

    Feeds the cog's startup re-attach. A persistent view is only live in the
    process that registered it, so after any restart the posted cards' buttons
    route nowhere and a click times out with "didn't respond in time".

    **Every row of a qualifying message is returned, including already-decided
    ones.** Registering only the undecided rows would leave the decided buttons
    with no handler, so clicking one would time out rather than answering
    "Already decided" - a worse outcome than the state it reports.

    A message whose rows are ALL decided is left out, which is what keeps this
    bounded as messages accumulate day after day.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"""
            SELECT {_COLUMNS} FROM outreach_discoveries
            WHERE review_message_id IS NOT NULL
              AND review_message_id IN (
                  SELECT review_message_id FROM outreach_discoveries
                  WHERE review_message_id IS NOT NULL AND reviewed_at IS NULL
              )
            ORDER BY review_message_id,
                     icp_fit_score DESC NULLS LAST, discovered_at
            """
        )
        pages: dict[str, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            pages.setdefault(row["review_message_id"], []).append(row)
        return pages


def get(conn: object, discovery_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"SELECT {_COLUMNS} FROM outreach_discoveries WHERE id = %s",
            (discovery_id,),
        )
        return cur.fetchone()


def known_domains(conn: object) -> set[str]:
    """Every domain already in either table (R0.10).

    Both, not just the pool: a firm that is already an `outreach_target` must
    never be re-surfaced as a discovery, which is outcome 5.
    """
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT company_domain FROM outreach_discoveries "
            "UNION SELECT company_domain FROM outreach_targets"
        )
        return {row[0] for row in cur.fetchall()}


def insert_discovery(conn: object, row: dict[str, Any]) -> int | None:
    """Insert one discovery, or return None if the domain is already known.

    `ON CONFLICT DO NOTHING` on the domain index, so a re-run of the sourcing
    loop is free rather than an error - the same posture the evidence poller
    takes on re-polling a board.
    """
    payload = dict(row)
    payload["company_domain"] = outreach.normalize_domain(payload["company_domain"])
    for field, cap in (
        ("company_name", 200), ("description", 1000), ("verification_note", 500),
        ("contact_name", 200), ("contact_title", 200), ("hq_location", 200),
        ("pain_hook", 500), ("arr_basis", 200),
    ):
        if field in payload:
            payload[field] = outreach.clean_field(payload.get(field), max_chars=cap)

    cols = [c for c in payload if payload[c] is not None]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"INSERT INTO outreach_discoveries ({', '.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (company_domain) DO NOTHING RETURNING id",
            payload,
        )
        found = cur.fetchone()
        return found[0] if found else None


def mark_surfaced(conn: object, discovery_id: int, message_id: str | None) -> None:
    """Stamp the row as shown. Verified-evidence CHECK bites here if it is thin."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_discoveries SET surfaced_at = now(), "
            "review_message_id = %s WHERE id = %s AND surfaced_at IS NULL",
            (message_id, discovery_id),
        )


def decide(
    discovery_id: int,
    action: str,
    *,
    reason: str | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    """Record a Gate 0 decision. Returns a summary, or None if already decided.

    `defer` records **no label** (OQ-H): it clears nothing and writes nothing but
    a log line, so the row simply re-ranks into tomorrow's window. Treating a
    deferral as a weak rejection would teach Part 4 that a firm with a missing
    contact field is a poor fit, which is not what it means.
    """
    validate_decision(action, reason, note)

    if action == "defer":
        logger.info("gate 0: deferred discovery %s (no label recorded)", discovery_id)
        return {"id": discovery_id, "decision": "defer", "labelled": False}

    clean_note = outreach.clean_field(note, max_chars=500)

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE outreach_discoveries "
                "SET review_decision = %s, reject_reason = %s, reject_note = %s, "
                "    reviewed_at = now() "
                "WHERE id = %s AND reviewed_at IS NULL "
                "RETURNING id, company_name, segment, review_decision, reject_reason",
                (action, reason, clean_note, discovery_id),
            )
            updated = cur.fetchone()

    if updated is None:
        return None  # already decided, or lost the race to another submit
    logger.info(
        "gate 0: %s %s (%s)%s",
        action, updated["company_name"], updated["segment"],
        f" - {reason}" if reason else "",
    )
    return {**updated, "labelled": True}


# The trigger_kind for a firm the operator selected without a specific observed
# market event: the trigger IS the decision to work it (0023). A real market
# trigger, when supplied, is preferred.
OPERATOR_SELECTED = "operator_selected"


def promote(discovery_id: int, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn an accepted discovery into an `outreach_targets` row.

    **`trigger_date` is when the operator accepted the firm into the pipeline**
    (0023, operator decision 2026-08-27) - the discovery's `reviewed_at` date -
    not a separately-observed market-event date. The five-touch arc anchors on
    trigger_date, so this is what makes the arc run in the real working window;
    the fake batch date pre-expired every arc.

    This does not reopen R0.3's fabricated-date failure: R0.3 guarded against a
    stamp on UNOBSERVED data (an import artefact). An acceptance date is a real,
    dated decision about a specific firm. `trigger_kind` defaults to
    `operator_selected`; a caller may pass a real market trigger to override it.

    `trigger` is optional: with none, the acceptance date and `operator_selected`
    are used. A partial `trigger` (kind but no date) still takes the acceptance
    date - the date is never the caller's to fabricate here.
    """
    trigger = trigger or {}
    kind = trigger.get("trigger_kind") or OPERATOR_SELECTED
    source = trigger.get("trigger_source_url")

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUMNS}, reviewed_at FROM outreach_discoveries WHERE id = %s",
                (discovery_id,),
            )
            found = cur.fetchone()
        if found is None:
            raise KeyError(f"no discovery {discovery_id}")
        if found["review_decision"] != "accept":
            raise NotPromotableError(
                f"discovery {discovery_id} is {found['review_decision'] or 'unreviewed'}, "
                "not accepted"
            )
        if found["promoted_target_id"]:
            return {"id": discovery_id, "target_id": found["promoted_target_id"],
                    "created": False}

        # The acceptance date IS the trigger date (0023). Never the caller's to
        # fabricate; a caller-supplied market date is honoured only if it is real
        # and not in the future.
        when = found["reviewed_at"].date()
        supplied = trigger.get("trigger_date")
        if supplied is not None and not (isinstance(supplied, date) and supplied > date.today()):
            when = supplied

        with conn.transaction():
            target = outreach.upsert_target(conn, {
                "company_name": found["company_name"],
                "company_domain": found["company_domain"],
                "company_url": found["company_url"],
                "careers_url": found["careers_url"],
                "sector": found["segment"],
                # Unknown, and deliberately not invented. 0014 made `stage`
                # nullable rather than let an import fabricate one, and
                # `outreach_targets_seq_ck` re-imposes it before sequencing -
                # which is the only place stage is consumed.
                "stage": None,
                "contact_name": found["contact_name"],
                "contact_role": found["contact_title"],
                "contact_email": found["contact_email"],
                "contact_linkedin_url": found["contact_linkedin_url"],
                "trigger_kind": kind,
                "trigger_date": when,
                "trigger_source_url": source,
            })
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE outreach_discoveries SET promoted_target_id = %s "
                    "WHERE id = %s AND promoted_target_id IS NULL",
                    (target["id"], discovery_id),
                )
            # Carry any news observed while the firm was in the pool across to the
            # new target, so its history is not orphaned (R1.9). No-op until Part
            # 1 has run; harmless before then.
            outreach.reparent_watch_signals(conn, discovery_id, target["id"])

    logger.info(
        "gate 0: promoted %s to target %s on trigger %s",
        found["company_name"], target["id"], kind,
    )
    return {"id": discovery_id, "target_id": target["id"], "created": True}


def review_stats(conn: object) -> dict[str, Any]:
    """Counts for the briefing line and for V0-7's reason distribution."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE reviewed_at IS NULL)            AS unreviewed,
                count(*) FILTER (WHERE review_decision = 'accept')     AS accepted,
                count(*) FILTER (WHERE review_decision = 'reject')     AS rejected,
                count(*) FILTER (WHERE promoted_target_id IS NOT NULL) AS promoted,
                count(*)                                               AS total
            FROM outreach_discoveries
            """
        )
        return cur.fetchone()
