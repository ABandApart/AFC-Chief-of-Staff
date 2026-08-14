"""Packet assembly — the deterministic work payload (Track O, `35-` §7).

**Zero LLM calls.** This is a query and a substitution, and that is a designed
property rather than an economy (`40-action-layer.md`, Outreach_loops): it cannot
fail from a provider outage, it does not depend on cognee's completion path, and
prompt injection into outbound mail is impossible because nothing here generates
text. The operator writes the observation sentence; the system supplies the
evidence, the arithmetic, and the failure mode.

Two entry points:

  * `materialize_sequence()` — the intake action. Creates five `outreach_touches`
    from the Selector, anchored on `trigger_date`. Discord-free by design (like
    `_lib/task_tinder`), so the Task Tinder intake card is a thin caller.
  * `assemble_packet()` — builds one touch's packet: substitutes `auto` slots,
    lays out evidence by freshness tier, computes the arithmetic, and decides
    `ready`.

**What sets `ready = false`** (§7, extended):
  * any **`operator`** slot unresolved — literal "[Client 1]" reaching a founder
    is R1, the cheapest catastrophic-failure guard in the design;
  * any **`auto`** slot unresolved — §7 names only operator slots, but an
    unfilled "[First Name]" is the same failure with a worse blast radius
    (it lands in the greeting), so this module blocks on both;
  * the driving `open_role` evidence being **stale or closed** (§3) — quoting a
    posting age for a req that came down is R19, and unlike a visible
    placeholder the operator has no reason to doubt it.

**`observed` slots deliberately do NOT block.** They are the sentence written at
Gate 2, in the mail client, from the evidence the packet supplies. A packet with
its observation still open is a packet doing its job.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agents._lib import selector
from agents._lib.control_plane import repo_root

logger = logging.getLogger(__name__)

# The five-touch arc (`35-` §5 slot table). Every window is measured from
# `trigger_date`, never from the previous send — which is why snoozing one touch
# never shifts the others.
SLOT_WINDOWS: dict[int, tuple[int, int, int]] = {
    # slot: (window_opens, due_date, window_closes) as days after trigger_date
    1: (0, 3, 7),
    2: (7, 10, 14),
    3: (14, 21, 30),
    4: (30, 37, 45),
    5: (60, 75, 90),      # the day-60 hinge — highest-converting in the method
}

# Slots already past their window at admission are created pre-skipped, so the
# completion metric is not poisoned by a decision that was never offered (§5).
ADMITTED_LATE_REASON = "admitted_after_window"


@dataclass(frozen=True)
class AssembledPacket:
    """One touch's packet, ready to persist to `outreach_packets`."""

    touch_id: int
    subject_line: str
    body_filled: str
    evidence_ids: tuple[int, ...]
    arithmetic: dict[str, Any]
    staleness_days: int | None
    unresolved_slots: tuple[str, ...]
    failure_mode: str
    ready: bool
    # Display-only, not persisted: the reasons `ready` is false, so the operator
    # is told what to fix rather than left guessing at a boolean.
    blockers: tuple[str, ...] = field(default_factory=tuple)


def load_arithmetic(root: Path | None = None) -> dict[str, Any]:
    path = (root or repo_root()) / selector.CONFIG_DIR / "arithmetic.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_sender(root: Path | None = None) -> dict[str, Any]:
    path = (root or repo_root()) / selector.CONFIG_DIR / "sender.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("sender") or {}


# --- sequence materialisation (the intake action) ----------------------------


def touch_windows(trigger_date: date) -> dict[int, dict[str, date]]:
    """The five slots' date windows for a trigger (pure)."""
    return {
        slot: {
            "window_opens": trigger_date + timedelta(days=opens),
            "due_date": trigger_date + timedelta(days=due),
            "window_closes": trigger_date + timedelta(days=closes),
        }
        for slot, (opens, due, closes) in SLOT_WINDOWS.items()
    }


def materialize_sequence(
    conn: object,
    target: dict[str, Any],
    facts: dict[str, Any],
    *,
    today: date | None = None,
    token_factory: Any = None,
) -> list[dict[str, Any]]:
    """Create the five touches for a target. The intake gate's DB half.

    Idempotent per `(target_id, slot)` — the UNIQUE constraint means a
    double-click at the intake card cannot mint a second sequence.

    Slots whose window has already closed are created **pre-skipped**, because a
    target admitted on day 40 was never offered slots 1-3 and counting them as
    missed would understate the completion metric the method is measured on.
    """
    today = today or date.today()
    make_token = token_factory or (lambda: secrets.token_hex(8))
    windows = touch_windows(target["trigger_date"])
    created: list[dict[str, Any]] = []

    for slot in sorted(SLOT_WINDOWS):
        choice = selector.select(slot, target.get("stage"), facts)
        window = windows[slot]
        late = window["window_closes"] < today
        with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                INSERT INTO outreach_touches (
                    target_id, slot, template_code, window_opens, due_date,
                    window_closes, bcc_token, skipped_at, skip_reason
                ) VALUES (
                    %(target_id)s, %(slot)s, %(template_code)s, %(window_opens)s,
                    %(due_date)s, %(window_closes)s, %(bcc_token)s,
                    %(skipped_at)s, %(skip_reason)s
                )
                ON CONFLICT (target_id, slot) DO NOTHING
                RETURNING id, slot, template_code, due_date, skip_reason
                """,
                {
                    "target_id": target["id"], "slot": slot,
                    "template_code": choice.template_code,
                    **window,
                    "bcc_token": make_token(),
                    "skipped_at": "now()" if late else None,
                    "skip_reason": ADMITTED_LATE_REASON if late else None,
                },
            )
            if row := cur.fetchone():
                created.append(row)
    return created


# --- arithmetic ---------------------------------------------------------------


def quarter_of(day: date) -> str:
    """Calendar quarter label, e.g. 'Q3 2026' (pure)."""
    return f"Q{(day.month - 1) // 3 + 1} {day.year}"


def compute_arithmetic(
    driving: dict[str, Any] | None, config: dict[str, Any], *, today: date
) -> dict[str, Any]:
    """The precomputed search-window maths for the driving open-role fact (pure).

    `driving` is the fact the template's angle rests on, already filtered to
    non-stale by the caller — stale evidence is excluded from the arithmetic
    entirely (§3), because "open 56 days" about a req that came down is the
    exact R19 failure.

    Returns an empty-but-shaped dict when there is no usable fact, so consumers
    never have to distinguish "no arithmetic" from "missing key".
    """
    search = config["search"]
    base = {
        "has_arithmetic": False,
        "search_days_low": search["duration_days_low"],
        "search_days_high": search["duration_days_high"],
    }
    if driving is None:
        return base

    first_seen = driving["first_seen_at"]
    posting_age = (today - first_seen).days
    earliest = first_seen + timedelta(days=search["duration_days_low"])
    latest = first_seen + timedelta(days=search["duration_days_high"])
    return {
        **base,
        "has_arithmetic": True,
        "role_title": (driving.get("payload") or {}).get("title"),
        "first_seen_at": first_seen.isoformat(),
        "posting_age_days": posting_age,
        "confirmed_days_ago": (today - driving["last_seen_at"]).days,
        # The projection the copy asserts: a search started when the req went up
        # lands somewhere in this range, then ramps.
        "projected_fill_earliest": earliest.isoformat(),
        "projected_fill_latest": latest.isoformat(),
        "unled_through_quarter": quarter_of(latest),
        "unled_quarters": config["impact"]["unled_quarters"],
    }


def render_arithmetic(arithmetic: dict[str, Any], function: str | None) -> str:
    """The arithmetic as the operator reads it (pure).

    Substitution into a fixed sentence, not generation — the shape and every
    number come from `arithmetic.yaml` and the evidence. Mirrors §7's worked
    example so the packet reads the way the spec describes it.
    """
    if not arithmetic.get("has_arithmetic"):
        return "No open-role evidence fresh enough to compute posting age."
    confirmed = arithmetic["confirmed_days_ago"]
    confirmation = "confirmed open today" if confirmed == 0 else (
        f"last confirmed {confirmed} day{'s' if confirmed != 1 else ''} ago"
    )
    subject = function or "the function"
    age = arithmetic["posting_age_days"]
    return (
        f"Req first seen {arithmetic['first_seen_at']} — "
        f"{age} day{'s' if age != 1 else ''}, {confirmation}. "
        f"Searches at this level run {arithmetic['search_days_low']}–"
        f"{arithmetic['search_days_high']} days, then ramp. "
        f"{subject.capitalize()} is effectively unled through "
        f"{arithmetic['unled_through_quarter']}."
    )


# --- substitution -------------------------------------------------------------


def build_auto_values(
    target: dict[str, Any],
    driving: dict[str, Any] | None,
    sender: dict[str, Any],
    *,
    today: date,
    original_subject: str | None = None,
) -> dict[str, str]:
    """Resolve the `auto` placeholder values available for this touch (pure).

    A token absent from the returned mapping is **unresolved**, and the caller
    blocks on it. Deliberately no fallbacks: substituting a plausible guess for
    a missing first name or role title is how a confident, wrong email gets sent.
    """
    values: dict[str, str] = {}

    def put(token: str, value: Any) -> None:
        if value not in (None, ""):
            values[token] = str(value)

    put("Company Name", target.get("company_name"))
    put("Company", target.get("company_name"))
    put("First Name", target.get("contact_first_name"))
    put("Title", target.get("contact_role"))
    put("function", target.get("function"))
    put("Function", (target.get("function") or "").capitalize() or None)
    put("Your Name", sender.get("name"))
    put("Original Subject", original_subject)

    if driving is not None:
        put("Role Title", (driving.get("payload") or {}).get("title"))
        first_seen = driving["first_seen_at"]
        put("month", first_seen.strftime("%B"))
        # "[X] weeks ago" — floor, because overstating posting age is the
        # direction that gets checked and found wrong.
        put("X", max(1, (today - first_seen).days // 7))
    return values


def substitute(text: str, values: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    """Fill `[token]` placeholders from `values` (pure).

    Returns the filled text and the tokens left unfilled, **in the order they
    appear**, so the packet can list what is missing in reading order rather
    than alphabetically.
    """
    unresolved: list[str] = []
    out = text
    for token in selector._PLACEHOLDER_RE.findall(text):
        if token in values:
            out = out.replace(f"[{token}]", values[token])
        elif token not in unresolved:
            unresolved.append(token)
    return out, tuple(unresolved)


# --- assembly ------------------------------------------------------------------


def pick_driving_fact(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The open-role fact the arithmetic and the angle rest on (pure).

    The oldest still-open, non-stale req: age is the pitch, so the longest-open
    one carries the argument. Stale and closed facts are excluded — §3 keeps
    them out of the arithmetic, and this is where that exclusion happens.
    """
    usable = [
        e for e in evidence
        if e["fact_kind"] == "open_role" and e.get("freshness") in ("fresh", "ageing")
    ]
    return min(usable, key=lambda e: e["first_seen_at"]) if usable else None


def assemble_packet(
    touch: dict[str, Any],
    target: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    today: date | None = None,
    templates: dict[str, selector.Template] | None = None,
    config: dict[str, Any] | None = None,
    arithmetic_config: dict[str, Any] | None = None,
    sender: dict[str, Any] | None = None,
    original_subject: str | None = None,
) -> AssembledPacket:
    """Build one touch's packet. Pure given its inputs — no DB, no network, no LLM."""
    today = today or date.today()
    templates = templates if templates is not None else selector.load_templates()
    config = config if config is not None else selector.load_config()
    arithmetic_config = arithmetic_config or load_arithmetic()
    sender = sender if sender is not None else load_sender()

    template = templates.get(touch["template_code"])
    if template is None:
        raise KeyError(
            f"touch {touch['id']} references template {touch['template_code']!r}, "
            "which is not in the pack — selector.yaml and the pack have diverged"
        )

    driving = pick_driving_fact(evidence)
    arithmetic = compute_arithmetic(driving, arithmetic_config, today=today)
    values = build_auto_values(
        target, driving, sender, today=today, original_subject=original_subject
    )

    subject_line, subject_missing = substitute(template.subject, values)
    body_filled, body_missing = substitute(template.body, values)

    # Classify what is still open. `observed` slots are expected to be —
    # they are the Gate 2 sentence — so they never block.
    missing = list(dict.fromkeys(subject_missing + body_missing))
    blocking: list[str] = []
    for token in missing:
        if selector.classify_placeholder(token, config) != selector.OBSERVED:
            blocking.append(token)

    blockers: list[str] = []
    if blocking:
        blockers.append(f"unresolved slot(s): {', '.join(blocking)}")

    # R19: the angle cannot rest on evidence that has gone stale or closed.
    open_role_facts = [e for e in evidence if e["fact_kind"] == "open_role"]
    if open_role_facts and driving is None:
        blockers.append(
            "every open-role fact is stale or closed — the posting age this "
            "angle quotes can no longer be confirmed"
        )

    staleness_days = None
    if displayed := [e for e in evidence if e.get("freshness") != "closed"]:
        staleness_days = max(
            (today - e["last_seen_at"]).days for e in displayed
        )

    return AssembledPacket(
        touch_id=touch["id"],
        subject_line=subject_line,
        body_filled=body_filled,
        evidence_ids=tuple(e["id"] for e in evidence),
        arithmetic={**arithmetic, "rendered": render_arithmetic(
            arithmetic, target.get("function")
        )},
        staleness_days=staleness_days,
        unresolved_slots=tuple(blocking),
        failure_mode=template.failure_mode,
        ready=not blockers,
        blockers=tuple(blockers),
    )


# --- persistence ---------------------------------------------------------------


def fetch_packet_inputs(conn: object, touch_id: int) -> tuple[dict, dict, list[dict]]:
    """The touch, its target, and that target's displayable evidence.

    Three separate queries rather than one join: `outreach_touches` and
    `outreach_targets` share `id`, `created_at`, and `updated_at`, so a
    `SELECT t.*, g.*` would silently hand back whichever column won — including
    the wrong `id`, which is what the packet is keyed on.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT * FROM outreach_touches WHERE id = %s", (touch_id,))
        touch = cur.fetchone()
        if touch is None:
            raise KeyError(f"no touch {touch_id}")
        cur.execute("SELECT * FROM outreach_targets WHERE id = %s", (touch["target_id"],))
        target = cur.fetchone()
        cur.execute(
            "SELECT * FROM v_outreach_evidence_display WHERE target_id = %s "
            "ORDER BY first_seen_at",
            (touch["target_id"],),
        )
        evidence = cur.fetchall()
    return touch, target, evidence


def previous_subject(conn: object, touch: dict[str, Any]) -> str | None:
    """The subject line of the last packet on an earlier slot for this target.

    Touches 2-5 reply on the original thread and keep the subject
    (`[Original Subject]`), so the thread stays one conversation rather than
    five separate cold emails.
    """
    if touch["slot"] <= 1:
        return None
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT p.subject_line
            FROM outreach_packets p
            JOIN outreach_touches t ON t.id = p.touch_id
            WHERE t.target_id = %s AND t.slot = 1
            ORDER BY p.assembled_at DESC, p.id DESC
            LIMIT 1
            """,
            (touch["target_id"],),
        )
        row = cur.fetchone()
    return row[0] if row else None


def save_packet(conn: object, packet: AssembledPacket) -> int:
    """Persist an assembled packet. Regenerate, never edit (`35-` §14) — each
    assembly inserts a new row and the newest wins."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            INSERT INTO outreach_packets (
                touch_id, subject_line, body_filled, evidence_ids, arithmetic,
                staleness_days, unresolved_slots, failure_mode, ready
            ) VALUES (
                %(touch_id)s, %(subject_line)s, %(body_filled)s, %(evidence_ids)s,
                %(arithmetic)s, %(staleness_days)s, %(unresolved_slots)s,
                %(failure_mode)s, %(ready)s
            ) RETURNING id
            """,
            {
                "touch_id": packet.touch_id,
                "subject_line": packet.subject_line,
                "body_filled": packet.body_filled,
                "evidence_ids": list(packet.evidence_ids),
                "arithmetic": Jsonb(packet.arithmetic),
                "staleness_days": packet.staleness_days,
                "unresolved_slots": list(packet.unresolved_slots),
                "failure_mode": packet.failure_mode,
                "ready": packet.ready,
            },
        )
        return cur.fetchone()[0]
