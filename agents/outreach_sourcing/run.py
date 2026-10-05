"""Autonomous outreach sourcing - the daily orchestrator.

PRD-outreach-autonomous-sourcing §6. Each weekday at 05:00:

1. Read the day's context with plain SQL: in-list segments, hypotheses and their
   results, recent reject reasons, known domains, and the stuck pool (§6.2).
2. Decide how many cards to aim for, N (§6.4).
3. Build briefs. Stuck-pool rows come first: completing their verification is
   cheaper than finding new firms. New-firm briefs come from one planning call
   to the orchestrator model, which may propose new hypotheses (§7).
4. Run briefs through research workers in parallel, check every dossier with
   code (§6.6), and store it. Repeat in rounds until N candidates pass, the
   outreach budget is spent, or the deadline arrives (§6.3).
5. Record the run, and post one line to #outreach if it fell short.

The Gate 0 cog surfaces whatever passed, as it does for every other source.
Nothing here sends anything (B2).

Usage:
    uv run python -m agents.outreach_sourcing.run --dry-run   # research, report, write no rows
    uv run python -m agents.outreach_sourcing.run             # the loop target
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agents._lib import db, discord_rest, heartbeat, outreach, outreach_discovery, runs
from agents._lib.runs import DailyCeilingExceeded, agent_run
from agents.outreach_sourcing import checks, dossier, worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] outreach-sourcing: %(message)s")
logger = logging.getLogger(__name__)

ORCHESTRATOR_MODEL = "claude-opus-5-5"
ORCHESTRATOR_EFFORT = "high"

START_N = 15                 # §6.4: until there are 5 completed runs
MIN_N, MAX_N = 10, 25
TARGET_APPROVALS = 10
MIN_RUNS_FOR_RATE = 5
EXPLORATION_SHARE = 0.30     # §7.3
EXPLORATION_SHARE_MAX = 0.50
MAX_TESTING = 5
MAX_NEW_HYPOTHESES_PER_RUN = 2
CONCURRENCY = 4
MAX_ROUNDS = 4
DEADLINE = time(7, 0)
EXPECTED_COST_PER_BRIEF = 1.00  # until measured (V6); used only to stop early
STUCK_POOL_PER_ROUND = 12
OUTREACH_GROUP = "outreach"
# Dead-man's switch: pinged after a completed (non-dry) run, /fail on a crash.
# Create the check on healthchecks.io as 24h period, 2h grace (weekday loop:
# expect a Monday alert unless the check's schedule is set to weekdays).
HEARTBEAT_SLUG = "cos-sourcing"

EXPLORATION_FAILURE = "exploration_share"


@dataclass
class RunState:
    run_id: str
    target_n: int
    dry_run: bool
    researched: int = 0
    passed: int = 0
    passed_out_of_list: int = 0
    failed: int = 0
    stop_reason: str | None = None
    report: list[str] = field(default_factory=list)
    tried_ids: set[int] = field(default_factory=set)


# --- context -------------------------------------------------------------------


def in_list_segments(conn: object) -> set[str]:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT key FROM outreach_segments WHERE in_list")
        return {r[0] for r in cur.fetchall()}


def open_hypotheses(conn: object) -> list[dict[str, Any]]:
    """Proposed and testing hypotheses that still want candidates (§7.3)."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT h.id, h.statement, h.pattern, h.status, h.proposed_segment, "
            "       r.approved, r.rejected, r.test_plan_contacts "
            "FROM outreach_hypotheses h "
            "JOIN v_outreach_hypothesis_results r ON r.hypothesis_id = h.id "
            "WHERE h.status IN ('proposed', 'testing') "
            "  AND r.approved < r.test_plan_contacts "
            "ORDER BY h.id")
        return cur.fetchall()


def recent_rejects(conn: object, days: int = 30) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT segment, reject_reason, count(*) AS n, "
            "       string_agg(DISTINCT left(reject_note, 120), ' | ') AS notes "
            "FROM outreach_discoveries WHERE review_decision = 'reject' "
            "  AND reviewed_at > now() - make_interval(days => %s) "
            "GROUP BY 1, 2 ORDER BY n DESC", (days,))
        return cur.fetchall()


def stuck_pool(conn: object, limit: int) -> list[dict[str, Any]]:
    """Unreviewed discoveries no run has tried that cannot be approved as they are:
    below the verification bar, or (legacy rows) without proposed scores, which
    one-click approval requires (`outreach_discovery.approval_blocks`)."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id, company_name, company_domain, segment, description, "
            "       hypothesis_id FROM outreach_discoveries "
            "WHERE reviewed_at IS NULL AND sourcing_run_id IS NULL "
            "  AND (COALESCE(array_length(verified_on, 1), 0) < 2 "
            "       OR proposed_scores IS NULL) "
            "ORDER BY icp_fit_score DESC NULLS LAST, discovered_at LIMIT %s", (limit,))
        return cur.fetchall()


def known_by_segment(conn: object) -> dict[str, set[str]]:
    """Known domains grouped by segment, so each brief's exclusion list is the
    firms a worker could plausibly re-find, not every domain we know."""
    out: dict[str, set[str]] = {}
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT segment, company_domain FROM outreach_discoveries "
                    "UNION SELECT sector, company_domain FROM outreach_targets")
        for segment, domain in cur.fetchall():
            out.setdefault(segment or "", set()).add(domain)
    return out


def target_n(conn: object) -> int:
    """§6.4: 15 until 5 runs exist, then ceil(10 / approval rate), clamped 10-25."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT count(*) FROM outreach_sourcing_runs "
                    "WHERE NOT dry_run AND finished_at IS NOT NULL")
        if cur.fetchone()[0] < MIN_RUNS_FOR_RATE:
            return START_N
        cur.execute(
            "SELECT count(*) FILTER (WHERE review_decision = 'accept'), "
            "       count(*) FILTER (WHERE review_decision IN ('accept', 'reject')) "
            "FROM outreach_discoveries "
            "WHERE sourcing_run_id IS NOT NULL AND reviewed_at > now() - interval '14 days'")
        approved, decided = cur.fetchone()
    return n_from_rate(approved, decided)


def n_from_rate(approved: int, decided: int) -> int:
    """Cards needed for TARGET_APPROVALS at the observed approval rate (pure)."""
    if not decided:
        return START_N
    if not approved:
        return MAX_N
    return max(MIN_N, min(MAX_N, math.ceil(TARGET_APPROVALS / (approved / decided))))


def group_spend_today(conn: object) -> float:
    members = list(runs.CEILING_GROUPS[OUTREACH_GROUP][1])
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0,
                                                         microsecond=0)
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT COALESCE(sum(usd_cost), 0) FROM agent_runs "
                    "WHERE agent_name = ANY(%s) AND started_at >= %s",
                    (members, local_midnight))
        return float(cur.fetchone()[0])


def run_spend(conn: object, run_id: str) -> float:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT COALESCE(sum(usd_cost), 0) FROM agent_runs "
                    "WHERE correlation_id = %s AND agent_name = %s",
                    (run_id, worker.AGENT))
        return float(cur.fetchone()[0])


# --- housekeeping (§7.2, §7.3) ---------------------------------------------------


def housekeeping(conn: object) -> None:
    """Re-open exploration-share holds, and retire hypotheses nobody approved."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "UPDATE outreach_discoveries "
            "SET check_failures = array_remove(check_failures, %s) "
            "WHERE reviewed_at IS NULL AND %s = ANY(check_failures)",
            (EXPLORATION_FAILURE, EXPLORATION_FAILURE))
        cur.execute(
            "UPDATE outreach_hypotheses h SET status = 'retired', "
            "  status_changed_at = now(), "
            "  verdict_note = 'Retired: every candidate rejected for 5 weekdays (§7.2)' "
            "FROM v_outreach_hypothesis_results r "
            "WHERE r.hypothesis_id = h.id AND h.status = 'proposed' "
            "  AND h.created_at < now() - interval '7 days' "
            "  AND r.approved = 0 AND r.rejected > 0 AND r.rejected >= r.surfaced")


# --- planning -------------------------------------------------------------------

PLAN_TOOL = "submit_plan"

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["briefs"],
    "properties": {"briefs": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["segment_key", "hypothesis_id", "new_hypothesis", "guidance", "count"],
        "properties": {
            "segment_key": {"type": "string"},
            "hypothesis_id": {"type": ["integer", "null"]},
            "new_hypothesis": {"anyOf": [{"type": "null"}, {
                "type": "object", "additionalProperties": False,
                "required": ["statement", "pattern", "rationale", "label"],
                "properties": {"statement": {"type": "string"},
                               "pattern": {"type": "string"},
                               "rationale": {"type": "string"},
                               "label": {"type": "string"}}}]},
            "guidance": {"type": "string"},
            "count": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        }}}},
}

PLAN_SYSTEM = """\
You plan today's prospect research for AI Adaptive, a solo AI consultancy
(Barry Baldwin). Research workers will each find one US company per brief.

Return briefs that, together, produce the requested number of candidates. Most
briefs should target the in-list segments. Up to the stated exploration share may
test hypotheses: either an existing open hypothesis (give its id) or a new one.

A new hypothesis must be concrete enough to be wrong: "Firms of type X have
problem Y, which AI Adaptive solves with Z; they will respond because W." Give the
firmographic pattern a researcher can search for, and a short snake_case segment
label for it. Learn from the reject reasons: do not plan more of what is being
rejected. Call submit_plan exactly once.
"""


def plan_prompt(ctx: dict[str, Any], need: int, exploration_slots: int) -> str:
    """The planning request (pure)."""
    lines = [f"Candidates needed today: {need}.",
             f"Exploration share: at most {exploration_slots} of them may test hypotheses.",
             f"New hypotheses allowed today: {ctx['new_hypotheses_allowed']}.",
             "", "In-list segment keys: " + ", ".join(sorted(ctx["segments"])), "",
             "Open hypotheses (id, status, approved/planned, statement):"]
    lines += [f"- #{h['id']} {h['status']} {h['approved']}/{h['test_plan_contacts']}: "
              f"{h['statement']}" for h in ctx["hypotheses"]] or ["- none"]
    lines += ["", "Reject reasons, last 30 days (segment, reason, count, notes):"]
    lines += [f"- {r['segment']} · {r['reject_reason']} · {r['n']} · {r['notes'] or ''}"
              for r in ctx["rejects"]] or ["- none yet"]
    return "\n".join(lines)


def plan_find_briefs(ctx: dict[str, Any], need: int, exploration_slots: int, *,
                     run_id: str, call: Any = None) -> list[dict[str, Any]]:
    """One orchestrator-model call returning planned briefs (strict tool, auto choice)."""
    tool = {"name": PLAN_TOOL, "description": "Submit today's research plan.",
            "input_schema": PLAN_SCHEMA, "strict": True}
    messages = [{"role": "user", "content": plan_prompt(ctx, need, exploration_slots)}]

    def _do(do_call: Any) -> list[dict[str, Any]]:
        response = do_call(messages, model=ORCHESTRATOR_MODEL, max_output_tokens=16000,
                           system=PLAN_SYSTEM, tools=[tool],
                           extra_body={"output_config": {"effort": ORCHESTRATOR_EFFORT}})
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == PLAN_TOOL:
                return list(dict(block.input).get("briefs") or [])
        raise RuntimeError(f"planner returned no plan (stop_reason={response.stop_reason})")

    if call is not None:
        return _do(call)
    with agent_run(worker.AGENT, "outreach_sourcing_plan", trigger_kind="scheduled",
                   correlation_id=run_id, correlation_kind="sourcing_run") as run:
        return _do(run.call_anthropic_raw)


def expand_plan(plan: list[dict[str, Any]], ctx: dict[str, Any], *,
                exploration_slots: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn planned briefs into worker briefs, enforcing §7.3 in code (pure).

    Returns (find_briefs, new_hypothesis_specs). An unknown segment without a
    hypothesis, an unknown hypothesis id, and anything past the exploration
    share or the new-hypothesis allowance are dropped, not trusted.
    """
    open_ids = {h["id"]: h for h in ctx["hypotheses"]}
    briefs: list[dict[str, Any]] = []
    new_specs: list[dict[str, Any]] = []
    explore_used = 0
    for item in plan:
        count = int(item.get("count") or 1)
        hyp = None
        new = item.get("new_hypothesis")
        if item.get("hypothesis_id") in open_ids:
            hyp = open_ids[item["hypothesis_id"]]
        elif new and len(new_specs) < ctx["new_hypotheses_allowed"]:
            key = _segment_key(new.get("label") or item.get("segment_key") or "")
            if not key or key in ctx["segments"]:
                continue
            hyp = {"id": None, "statement": new["statement"], "pattern": new["pattern"],
                   "rationale": new.get("rationale", ""), "proposed_segment": key,
                   "label": new.get("label") or key}
            new_specs.append(hyp)
        elif item.get("segment_key") not in ctx["segments"]:
            continue
        if hyp is not None:
            count = min(count, exploration_slots - explore_used)
            if count <= 0:
                continue
            explore_used += count
        segment = hyp["proposed_segment"] if hyp else item["segment_key"]
        exclude = ctx.get("known_by_segment", {}).get(segment, ctx["known_domains"])
        for _ in range(count):
            briefs.append({"kind": "find", "segment_key": segment, "hypothesis": hyp,
                           "guidance": item.get("guidance", ""),
                           "exclude_domains": exclude})
    return briefs, new_specs


def _segment_key(label: str) -> str:
    key = "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    return key[:60] if len(key) >= 3 and key[0].isalpha() else ""


def create_hypothesis(conn: object, spec: dict[str, Any]) -> int:
    """Insert a proposed hypothesis and its (not in-list) segment; returns its id."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "INSERT INTO outreach_hypotheses (statement, pattern, rationale, origin, "
            "proposed_segment) VALUES (%s, %s, %s, 'agent', %s) RETURNING id",
            (outreach.clean_field(spec["statement"], max_chars=1000),
             outreach.clean_field(spec["pattern"], max_chars=1000),
             outreach.clean_field(spec.get("rationale"), max_chars=2000),
             spec["proposed_segment"]))
        hyp_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO outreach_segments (key, label, in_list, origin, hypothesis_id) "
            "VALUES (%s, %s, false, 'hypothesis', %s) ON CONFLICT (key) DO NOTHING",
            (spec["proposed_segment"],
             outreach.clean_field(spec.get("label"), max_chars=120) or spec["proposed_segment"],
             hyp_id))
    return hyp_id


# --- persisting a dossier ---------------------------------------------------------


def discovery_fields(found: dict[str, Any], seen: set[str], *, run_id: str,
                     failures: list[str]) -> dict[str, Any]:
    """Columns written for one dossier, H2-cleaned (pure apart from cleaning)."""
    contact = found.get("contact") or {}
    evidence = [
        {"claim": outreach.clean_field(e.get("claim"), max_chars=300),
         "url": e.get("url"), "source_kind": e.get("source_kind"),
         "date": e.get("date"), "domain": checks.registrable_domain(e.get("url", ""))}
        for e in (found.get("evidence") or [])[:12]
    ]
    trimmed = {**found, "trigger": checks.usable_trigger(found, seen)}
    domains = checks.evidence_domains(found)
    return {
        "summary": outreach.clean_field(found.get("summary"), max_chars=1500),
        "why_now": outreach.clean_field(found.get("why_now"), max_chars=600),
        "evidence": Jsonb(evidence),
        "evidence_domains": domains,
        "proposed_scores": Jsonb(dossier.proposed_scores(trimmed)),
        "contact_method": contact.get("method"),
        "sourcing_run_id": run_id,
        "check_failures": failures,
        "suggested_angle": outreach.clean_field(found.get("suggested_angle"), max_chars=400),
        "verification_note": outreach.clean_field(
            f"Agent-verified: {len(evidence)} source(s) across {len(domains)} domain(s)",
            max_chars=500),
    }


def store(conn: object, brief: dict[str, Any], found: dict[str, Any], seen: set[str], *,
          run_id: str, failures: list[str]) -> int | None:
    """Write the dossier: update the pool row for a verify brief, insert for a find."""
    cols = discovery_fields(found, seen, run_id=run_id, failures=failures)
    contact = found.get("contact") or {}
    if brief["kind"] == "verify":
        assignments = ", ".join(f"{c} = %({c})s" for c in cols)
        fill = {"contact_name": contact.get("name"), "contact_title": contact.get("title"),
                "contact_email": contact.get("email"),
                "email_confidence": dossier.EMAIL_CONFIDENCE.get(contact.get("method", ""))}
        fill_sql = ", ".join(f"{c} = COALESCE({c}, %({c})s)" for c in fill)
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                f"UPDATE outreach_discoveries SET {assignments}, {fill_sql} "
                "WHERE id = %(id)s AND reviewed_at IS NULL",
                {**cols, **fill, "id": brief["discovery_id"]})
        return brief["discovery_id"]

    hyp = brief.get("hypothesis") or {}
    row = {
        "company_name": found.get("company_name"),
        "company_domain": found.get("domain"),
        "company_url": found.get("company_url"),
        "segment": brief["segment_key"],
        "country": "US",
        "hq_location": found.get("hq_location"),
        "headcount_band": outreach.clean_field(found.get("headcount_estimate"), max_chars=50),
        "contact_name": contact.get("name"),
        "contact_title": contact.get("title"),
        "contact_email": contact.get("email"),
        "email_confidence": dossier.EMAIL_CONFIDENCE.get(contact.get("method", "")),
        "discovered_via": "agent_research",
        "discovery_query": outreach.clean_field(brief.get("guidance"), max_chars=500),
        "source_url": ((found.get("evidence") or [{}])[0]).get("url"),
        "hypothesis_id": hyp.get("id"),
        **cols,
    }
    return outreach_discovery.insert_discovery(conn, row)


# --- the run ------------------------------------------------------------------------


def verify_briefs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"kind": "verify", "discovery_id": r["id"], "company_name": r["company_name"],
             "domain": r["company_domain"], "segment_key": r["segment"],
             "known": (r.get("description") or "")[:300],
             "hypothesis": {"id": r["hypothesis_id"]} if r.get("hypothesis_id") else None}
            for r in rows]


def process(brief: dict[str, Any], ctx: dict[str, Any], state: RunState) -> dict[str, Any]:
    """Research one brief and check the result. Never raises; returns an outcome."""
    try:
        result = worker.research(brief, run_id=state.run_id)
    except DailyCeilingExceeded as exc:
        return {"brief": brief, "error": "budget", "detail": str(exc)}
    except Exception as exc:  # one bad brief must not stop the round
        logger.exception("brief failed: %s", brief.get("company_name") or brief["segment_key"])
        detail = str(exc)
        kind = "credit" if "credit balance" in detail.lower() else "error"
        return {"brief": brief, "error": kind, "detail": detail[:300]}
    found = result.dossier
    # The brief decides the segment, not the worker: a drifted key would bypass
    # the hypothesis rule (and the 0029 trigger would refuse the row).
    found["segment_key"] = brief["segment_key"]
    has_hyp = bool(brief.get("hypothesis"))
    failures = checks.run_checks(
        found, seen_urls=result.seen_urls, known_domains=ctx["known_domains"],
        in_list_segments=ctx["segments"], has_hypothesis=has_hyp,
        own_discovery_domain=brief.get("domain") if brief["kind"] == "verify" else None)
    return {"brief": brief, "dossier": found, "seen": result.seen_urls,
            "failures": failures}


def deadline_passed(now: datetime | None = None) -> bool:
    now = now or datetime.now().astimezone()
    return now.time() >= DEADLINE


def run(*, dry_run: bool = False, ignore_deadline: bool = False) -> RunState:
    run_id = f"src-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    with db.connection() as conn:
        if not dry_run:
            housekeeping(conn)
        need = target_n(conn)
        ctx = {
            "segments": in_list_segments(conn),
            "hypotheses": open_hypotheses(conn),
            "rejects": recent_rejects(conn),
            "known_domains": outreach_discovery.known_domains(conn),
            "known_by_segment": known_by_segment(conn),
        }
        testing = sum(1 for h in ctx["hypotheses"] if h["status"] == "testing")
        ctx["new_hypotheses_allowed"] = max(0, min(MAX_NEW_HYPOTHESES_PER_RUN,
                                                   MAX_TESTING - len(ctx["hypotheses"])))
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO outreach_sourcing_runs (run_id, target_n) "
                            "VALUES (%s, %s)", (run_id, need))
    state = RunState(run_id=run_id, target_n=need, dry_run=dry_run)
    logger.info("run %s: aiming for %d candidates (%d testing hypotheses)",
                run_id, need, testing)

    for round_no in range(1, MAX_ROUNDS + 1):
        remaining = state.target_n - state.passed
        if remaining <= 0:
            state.stop_reason = "target_met"
            break
        if not ignore_deadline and deadline_passed():
            state.stop_reason = "deadline"
            break
        with db.connection() as conn:
            budget_left = runs.CEILING_GROUPS[OUTREACH_GROUP][0] - group_spend_today(conn)
            stuck = [r for r in stuck_pool(conn, STUCK_POOL_PER_ROUND + len(state.tried_ids))
                     if r["id"] not in state.tried_ids][:STUCK_POOL_PER_ROUND]
        state.tried_ids |= {r["id"] for r in stuck}
        if budget_left < EXPECTED_COST_PER_BRIEF:
            state.stop_reason = "budget"
            break

        briefs = verify_briefs(stuck)
        if not dry_run and stuck:
            # Claim them so a later round (or tomorrow) does not redo them.
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute("UPDATE outreach_discoveries SET sourcing_run_id = %s "
                            "WHERE id = ANY(%s) AND sourcing_run_id IS NULL",
                            (run_id, [r["id"] for r in stuck]))
        want_new = max(0, math.ceil(remaining * 1.5) - len(briefs))
        if want_new:  # re-planned each round, so the floor never rests on round 1
            slots = math.floor(state.target_n * EXPLORATION_SHARE)
            try:
                plan = plan_find_briefs(ctx, want_new, slots, run_id=run_id)
            except DailyCeilingExceeded:
                state.stop_reason = "budget"
                break
            except Exception as exc:  # the stuck pool can still be worked
                logger.exception("planning failed")
                state.report.append(f"round {round_no}: planning failed ({str(exc)[:120]})")
                plan = []
            find, new_specs = expand_plan(plan, ctx, exploration_slots=slots)
            if not dry_run:
                with db.connection() as conn:
                    for spec in new_specs:
                        spec["id"] = create_hypothesis(conn, spec)
                        ctx["hypotheses"].append({**spec, "status": "proposed",
                                                  "approved": 0, "test_plan_contacts": 10})
            else:
                find = [b for b in find if not (b["hypothesis"] and b["hypothesis"]["id"] is None)]
            ctx["new_hypotheses_allowed"] = max(0, ctx["new_hypotheses_allowed"] - len(new_specs))
            briefs += find[:want_new]
            state.report.append(f"round {round_no}: planned {len(find)} new-firm brief(s), "
                                f"{len(new_specs)} new hypothesis(es)")
        affordable = max(1, int(budget_left // EXPECTED_COST_PER_BRIEF))
        briefs = briefs[:affordable]
        if not briefs:
            state.stop_reason = state.stop_reason or "no_briefs"
            break

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            outcomes = list(pool.map(lambda b: process(b, ctx, state), briefs))
        if _absorb(outcomes, ctx, state):
            break
    else:
        state.stop_reason = state.stop_reason or (
            "target_met" if state.passed >= state.target_n else "no_briefs")

    _finish(state)
    return state


def _absorb(outcomes: list[dict[str, Any]], ctx: dict[str, Any], state: RunState) -> bool:
    """Store a round's outcomes. Returns True if the run must stop (budget/credit)."""
    stop = False
    explore_cap = math.floor(state.target_n * EXPLORATION_SHARE_MAX)
    for out in outcomes:
        brief = out["brief"]
        if "error" in out:
            state.report.append(f"✗ {brief.get('company_name') or brief['segment_key']}: "
                                f"{out['error']}")
            if out["error"] in ("budget", "credit"):
                state.stop_reason = out["error"]
                stop = True
            continue
        state.researched += 1
        failures = list(out["failures"])
        found = out["dossier"]
        is_explore = found.get("segment_key") not in ctx["segments"]
        if not failures and is_explore and state.passed_out_of_list >= explore_cap:
            failures.append(EXPLORATION_FAILURE)
        label = found.get("company_name") or brief.get("company_name") or "?"
        if failures:
            state.failed += 1
            state.report.append(f"✗ {label}: {', '.join(failures)}")
        else:
            state.passed += 1
            state.passed_out_of_list += int(is_explore)
            state.report.append(f"✓ {label} ({found.get('segment_key')})")
        if failures == ["no_candidate"] or state.dry_run:
            continue
        domain = outreach.normalize_domain(found.get("domain") or "")
        try:
            with db.connection() as conn:
                store(conn, brief, found, out["seen"], run_id=state.run_id,
                      failures=failures)
        except Exception:  # one bad row must not lose the rest of the round
            logger.exception("could not store %s", label)
            state.report.append(f"✗ {label}: not stored (see log)")
        if domain:
            ctx["known_domains"].add(domain)
            ctx.get("known_by_segment", {}).setdefault(
                found.get("segment_key") or "", set()).add(domain)
    return stop


def _finish(state: RunState) -> None:
    with db.connection() as conn:
        cost = run_spend(conn, state.run_id)
        if not state.dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE outreach_sourcing_runs SET finished_at = now(), researched = %s, "
                    "passed = %s, failed_checks = %s, usd_cost = %s, stop_reason = %s, "
                    "notes = %s WHERE run_id = %s",
                    (state.researched, state.passed, state.failed, cost, state.stop_reason,
                     "\n".join(state.report)[:4000], state.run_id))
    logger.info("run %s: %d passed of %d researched, $%.2f, stop=%s",
                state.run_id, state.passed, state.researched, cost, state.stop_reason)
    if not state.dry_run and state.passed < state.target_n:
        from agents.discord_bot.config import OUTREACH_CHANNEL_ID
        discord_rest.post(OUTREACH_CHANNEL_ID, shortfall_line(state, cost),
                          user_agent="aiadaptive-cos-outreach-sourcing")


_STOP_TEXT = {
    "budget": "the $20 outreach budget for today is spent",
    "credit": "the Anthropic account is out of credit",
    "deadline": "the 07:00 deadline arrived",
    "no_briefs": "it ran out of firms to research",
    "error": "of errors (see the scheduler log)",
}


def shortfall_line(state: RunState, cost: float) -> str:
    """The one #outreach line when a run falls short (pure)."""
    why = _STOP_TEXT.get(state.stop_reason or "", state.stop_reason or "unknown")
    return (f"🔎 Sourcing found {state.passed} of {state.target_n} candidates today "
            f"({state.researched} researched, {state.failed} failed checks, ${cost:.2f}). "
            f"It stopped because {why}.")


# --- V6: the worker quality probe -------------------------------------------------

PROBE_REPORT_DIR = "/Users/Shared/afc-richmond"


def probe_targets(conn: object, n: int) -> list[dict[str, Any]]:
    """Known-good firms to probe: existing targets, oldest first (the operator's own
    hand-picked imports). Reads only pre-0029 columns, so the probe can run before
    the migration is applied."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id, company_name, company_domain, sector FROM outreach_targets "
            "WHERE company_domain IS NOT NULL "
            # Discovery is US-only (D6); a foreign firm would fail geography,
            # which is not the worker quality V6 measures.
            "  AND company_domain !~ '\\.(au|uk|ca|nz|ie|de|fr|in|sg)$' "
            "ORDER BY id LIMIT %s", (n,))
        return cur.fetchall()


def probe_cost(conn: object, correlation_id: str) -> float:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT COALESCE(sum(usd_cost), 0) FROM agent_runs "
                    "WHERE correlation_id = %s", (correlation_id,))
        return float(cur.fetchone()[0])


def probe_one(target: dict[str, Any], probe_id: str) -> dict[str, Any]:
    """Research one known firm with the real worker; check it; store nothing."""
    domain = target["company_domain"]
    segment = target.get("sector") or "unknown"
    brief = {"kind": "verify", "discovery_id": None, "company_name": target["company_name"],
             "domain": domain, "segment_key": segment, "known": "an existing target"}
    correlation = f"{probe_id}:{domain}"
    try:
        result = worker.research(brief, run_id=correlation)
    except Exception as exc:
        return {"target": target, "error": str(exc)[:300], "correlation": correlation}
    found = result.dossier
    found["segment_key"] = segment
    failures = checks.run_checks(found, seen_urls=result.seen_urls, known_domains=set(),
                                 in_list_segments={segment}, has_hypothesis=False,
                                 own_discovery_domain=domain)
    blocked = [e.get("url") for e in found.get("evidence") or []
               if checks.is_blocked(e.get("url", ""))]
    return {"target": target, "dossier": found, "failures": failures, "turns": result.turns,
            "seen": len(result.seen_urls), "blocked": blocked, "correlation": correlation}


def probe_report(outcomes: list[dict[str, Any]], costs: dict[str, float], probe_id: str) -> str:
    """The V6 review document (pure)."""
    passed = sum(1 for o in outcomes if "dossier" in o and not o["failures"])
    total = sum(costs.values())
    n = len(outcomes)
    lines = [
        f"# V6 worker quality probe — {probe_id}",
        "",
        "PRD-outreach-autonomous-sourcing V6. Each firm below is an existing target",
        "(known good). The research worker ran for real; nothing was stored.",
        "",
        "**Pass bar:** at least 8 of 10 dossiers pass the checks, and you rate at least 7",
        "summaries accurate. Fill in the Accurate? column, then write the verdict at the end.",
        "",
        f"- Dossiers passing the checks: **{passed} of {n}**",
        f"- Total cost: **${total:.2f}**, average **${(total / n if n else 0):.2f}** per firm",
        f"- Evidence from LinkedIn, ZoomInfo or Glassdoor (VB3, must be 0): "
        f"**{sum(len(o.get('blocked') or []) for o in outcomes)}**",
        "",
        "| # | Firm | Checks | Cost | Accurate? (Y/N) | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(outcomes, 1):
        status = (f"error: {o['error'][:60]}" if "error" in o
                  else "pass" if not o["failures"] else ", ".join(o["failures"]))
        lines.append(f"| {i} | {o['target']['company_name']} | {status} | "
                     f"${costs.get(o['correlation'], 0):.2f} |  |  |")
    for i, o in enumerate(outcomes, 1):
        lines += ["", f"## {i}. {o['target']['company_name']} ({o['target']['company_domain']})"]
        if "error" in o:
            lines.append(f"Worker error: {o['error']}")
            continue
        d = o["dossier"]
        contact = d.get("contact") or {}
        proposed = d.get("proposed") or {}
        trig = d.get("trigger") or {}
        lines += [
            f"- **Checks:** {'pass' if not o['failures'] else ', '.join(o['failures'])} "
            f"· {o['turns']} turn(s) · {o['seen']} URLs seen",
            f"- **Summary:** {d.get('summary', '')}",
            f"- **Why now:** {d.get('why_now', '')}",
            f"- **Trigger:** {trig.get('kind') or 'none'} {trig.get('date') or ''} "
            f"{trig.get('source_url') or ''}".rstrip(),
            f"- **Contact:** {contact.get('name') or '—'}, {contact.get('title') or '—'}, "
            f"{contact.get('email') or '—'} ({contact.get('method')})",
            f"- **Proposed:** S2 {proposed.get('s2_stage_fit')} · S3 "
            f"{proposed.get('s3_sector_match')} · S4 {proposed.get('s4_leadership_gap')} · "
            f"S5 {proposed.get('s5_team_build_below')} · {proposed.get('stage')} · "
            f"{proposed.get('function_state')} — {proposed.get('reasons', '')}",
            f"- **Angle:** {d.get('suggested_angle', '')}",
            "- **Evidence:**",
        ]
        lines += [f"  - {e.get('claim')} — {e.get('url')} ({e.get('date') or 'undated'})"
                  for e in d.get("evidence") or []]
    lines += ["", "## Verdict", "", "- Accurate summaries: __ of 10",
              "- Pass or fail V6:", "- Notes on cost:", ""]
    return "\n".join(lines)


def probe(n: int, report_path: str | None = None) -> str:
    """Run V6 and write its report. Returns the report path."""
    probe_id = f"v6-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    with db.connection() as conn:
        targets = probe_targets(conn, n)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        outcomes = list(pool.map(lambda t: probe_one(t, probe_id), targets))
    with db.connection() as conn:
        costs = {o["correlation"]: probe_cost(conn, o["correlation"]) for o in outcomes}
    path = report_path or f"{PROBE_REPORT_DIR}/V6-probe-{datetime.now():%Y-%m-%d}.md"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(probe_report(outcomes, costs, probe_id))
    os.chmod(path, 0o666)  # the handoff directory is shared between accounts
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Autonomous outreach sourcing.")
    parser.add_argument("--dry-run", action="store_true",
                        help="research and report; write no discovery, hypothesis, or run rows")
    parser.add_argument("--ignore-deadline", action="store_true",
                        help="do not stop at 07:00 (for a manual run later in the day)")
    parser.add_argument("--probe", type=int, metavar="N",
                        help="V6: research N existing targets, write a review report, "
                             "store nothing (needs no migration)")
    parser.add_argument("--report", help="report path for --probe")
    args = parser.parse_args(argv)
    if args.probe:
        try:
            path = probe(args.probe, args.report)
        finally:
            db.close_pool()
        print(f"V6 report: {path}")
        return 0
    try:
        state = run(dry_run=args.dry_run, ignore_deadline=args.ignore_deadline)
    except Exception:
        logger.exception("sourcing run failed")
        if not args.dry_run:
            heartbeat.ping_fail(HEARTBEAT_SLUG)
        raise
    finally:
        db.close_pool()
    if not args.dry_run:
        # Success path only: the run completed, whether or not it met its floor
        # (a shortfall is reported in #outreach, not as an outage).
        heartbeat.ping(HEARTBEAT_SLUG)
    print(f"{state.passed}/{state.target_n} passed · {state.researched} researched · "
          f"stop: {state.stop_reason}")
    for line in state.report:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
