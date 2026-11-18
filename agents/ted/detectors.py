"""Ted's detectors: SQL over the run tables, then pure rules (PRD-ted-ops-monitor §5.1).

No LLM is on this path (O9): a cycle completes with no Anthropic, Gemini, or
OpenAI credential, which is what lets it report a credit outage.

Each rule is a pure function from rows to `Finding`s so it is testable without a
database. `detect(conn, now)` runs the SQL and every rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

URGENT, HIGH, MEDIUM, INFO = "URGENT", "HIGH", "MEDIUM", "INFO"
SEVERITIES = (URGENT, HIGH, MEDIUM, INFO)
ALERTED = (URGENT, HIGH)   # these also reach the operator (D1)

SHARED = Path("/Users/Shared/afc-richmond")

# O1: the failure classes that mean "a key or an account is broken", fleet-wide.
CREDENTIAL_CLASSES = {
    "credit": ("credit balance",),
    "auth": ("authentication_error", "invalid x-api-key", "401 unauthorized",
             "error code: 401"),
}
CREDENTIAL_WINDOW = timedelta(hours=6)

# D3: the chronic rule (24 h rate). The acute rule is consecutive failures,
# FAILS_IN_A_ROW, kept from N9's briefing so the two surfaces agree.
RATE_WINDOW = timedelta(hours=24)
RATE_MIN_FAILURES = 4
RATE_MIN_SHARE = 0.25
FAILS_IN_A_ROW = 2
CONSECUTIVE_RECENT = timedelta(hours=48)

# O3: an agent that ran on 3+ days in the prior 9 and has been silent for 48 h.
SILENT_AFTER = timedelta(hours=48)
SILENT_LOOKBACK_DAYS = 9
SILENT_MIN_DAYS = 3

# O4: sourcing stop reasons that are normal.
SOURCING_OK_STOPS = ("target_met", "no_briefs", "deadline")

# D7 (pulled into increment 1): spend at 80% of a ceiling.
CEILING_SHARE = 0.80


@dataclass(frozen=True)
class Finding:
    severity: str
    key: str
    text: str
    source: str = "detector"


def _short(text: str | None, n: int = 160) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:n]


# --- pure rules ------------------------------------------------------------------------


def credential_class(error_text: str | None) -> str | None:
    """The credential class of an error, or None (pure)."""
    low = (error_text or "").lower()
    for name, needles in CREDENTIAL_CLASSES.items():
        if any(n in low for n in needles):
            return name
    return None


def credential_findings(failures: Iterable[tuple[str, str | None]]) -> list[Finding]:
    """One URGENT finding per credential class, naming every affected agent (O1).

    `failures`: (agent_name, error_text) for failed runs in the window."""
    by_class: dict[str, dict[str, int]] = {}
    for agent, error in failures:
        cls = credential_class(error)
        if cls:
            agents = by_class.setdefault(cls, {})
            agents[agent] = agents.get(agent, 0) + 1
    out = []
    for cls, agents in sorted(by_class.items()):
        named = ", ".join(f"{a} ({n})" for a, n in sorted(agents.items(), key=lambda x: -x[1]))
        what = "out of credit" if cls == "credit" else "failing authentication"
        out.append(Finding(URGENT, f"cred:{cls}",
                           f"An LLM provider account is {what}: {sum(agents.values())} failed "
                           f"runs in the last 6 h across {len(agents)} agent(s): {named}"))
    return out


def rate_findings(rows: Iterable[tuple[str, int, int, str | None]]) -> list[Finding]:
    """The chronic rule (O2): `rows` are (agent, failed, total, top error) over 24 h."""
    out = []
    for agent, failed, total, top in rows:
        if failed >= RATE_MIN_FAILURES and total and failed / total >= RATE_MIN_SHARE:
            out.append(Finding(HIGH, f"rate:{agent}",
                               f"{agent}: {failed} of {total} runs failed in 24 h; "
                               f"top error: {_short(top)}"))
    return out


def consecutive_findings(rows: Iterable[tuple[str, str, list[str], str | None]]
                         ) -> list[Finding]:
    """The acute rule: (agent, function label, statuses newest first, latest error)."""
    out = []
    for agent, label, statuses, error in rows:
        recent = statuses[:FAILS_IN_A_ROW]
        if len(recent) == FAILS_IN_A_ROW and all(s != "success" for s in recent):
            out.append(Finding(HIGH, f"streak:{agent}:{label}",
                               f"{agent} {label} failed {FAILS_IN_A_ROW} runs in a row: "
                               f"{_short(error)}"))
    return out


def silent_findings(rows: Iterable[tuple[str, datetime, int]], now: datetime) -> list[Finding]:
    """O3: (agent, last run, distinct days run in the lookback)."""
    return [Finding(MEDIUM, f"silent:{agent}",
                    f"{agent}: no runs since {last:%Y-%m-%d %H:%M} (it ran on {days} of "
                    f"the last {SILENT_LOOKBACK_DAYS} days)")
            for agent, last, days in rows
            if days >= SILENT_MIN_DAYS and now - last >= SILENT_AFTER]


def sourcing_findings(rows: Iterable[tuple[str, str | None]]) -> list[Finding]:
    """O4: (run id, stop reason) for non-dry sourcing runs in the last 24 h."""
    return [Finding(HIGH, f"src:{run_id}",
                    f"outreach-sourcing run {run_id} stopped with stop_reason={reason}")
            for run_id, reason in rows if reason not in SOURCING_OK_STOPS]


def ceiling_findings(agent_spend: dict[str, float], group_spend: dict[str, float],
                     total: float, day: date, *, agent_ceilings: dict[str, float],
                     group_ceilings: dict[str, float], global_ceiling: float) -> list[Finding]:
    """Spend at or over 80% of a daily ceiling today (pure)."""
    out = []

    def check(kind: str, name: str, usd: float, ceiling: float) -> None:
        if ceiling > 0 and usd >= CEILING_SHARE * ceiling:
            hit = "hit" if usd >= ceiling else f"at {usd / ceiling:.0%} of"
            out.append(Finding(HIGH, f"ceiling:{kind}:{name}:{day.isoformat()}",
                               f"Spend {hit} the {name} {kind} ceiling today: "
                               f"${usd:.2f} of ${ceiling:.2f}"))
    for agent, usd in sorted(agent_spend.items()):
        if agent in agent_ceilings:
            check("agent", agent, usd, agent_ceilings[agent])
    for group, usd in sorted(group_spend.items()):
        check("group", group, usd, group_ceilings.get(group, 0.0))
    check("system", "system", total, global_ceiling)
    return out


def phase_pointer_finding(current: str | None, newest: str | None) -> list[Finding]:
    """O5: `CURRENT-PHASE.txt` names a file other than the newest `PHASE-*.md`."""
    if newest and (current or "").strip() != newest:
        return [Finding(MEDIUM, f"phase:{(current or '').strip()}>{newest}",
                        f"CURRENT-PHASE.txt says {(current or '').strip() or '(empty)'}, "
                        f"but the newest phase file is {newest}")]
    return []


# --- SQL -------------------------------------------------------------------------------

_CRED_SQL = """
    SELECT agent_name, error_text FROM agent_runs
    WHERE started_at > %s AND status <> 'success' AND error_text IS NOT NULL
"""
_RATE_SQL = """
    SELECT agent_name, count(*) FILTER (WHERE status <> 'success')::int, count(*)::int,
           mode() WITHIN GROUP (ORDER BY error_text) FILTER (WHERE status <> 'success')
    FROM agent_runs WHERE started_at > %s GROUP BY 1
"""
_STREAK_SQL = """
    SELECT agent_name, function_label,
           array_agg(status ORDER BY started_at DESC),
           (array_agg(error_text ORDER BY started_at DESC))[1]
    FROM (SELECT agent_name, function_label, status, error_text, started_at,
                 row_number() OVER (PARTITION BY agent_name, function_label
                                    ORDER BY started_at DESC) AS rn,
                 max(started_at) OVER (PARTITION BY agent_name, function_label) AS latest
          FROM agent_runs WHERE started_at > %s) r
    WHERE rn <= %s AND latest > %s
    GROUP BY 1, 2
"""
_SILENT_SQL = """
    SELECT agent_name, max(started_at), count(DISTINCT started_at::date)::int
    FROM agent_runs WHERE started_at > %s GROUP BY 1
"""
_SOURCING_SQL = """
    SELECT run_id, stop_reason FROM outreach_sourcing_runs
    WHERE started_at > %s AND NOT dry_run AND finished_at IS NOT NULL
"""
_SPEND_SQL = """
    SELECT agent_name, COALESCE(sum(usd_cost), 0)::float FROM agent_runs
    WHERE started_at >= %s GROUP BY 1
"""


def _phase_files(shared: Path) -> tuple[str | None, str | None]:
    try:
        current = (shared / "CURRENT-PHASE.txt").read_text()
    except OSError:
        current = None
    phases = sorted(shared.glob("PHASE-*.md"), key=lambda p: p.stat().st_mtime)
    return current, (phases[-1].name if phases else None)


def detect(conn: Any, now: datetime, *, shared: Path = SHARED) -> list[Finding]:
    """Every finding for this cycle. `now` must be timezone-aware."""
    from agents._lib import runs

    found: list[Finding] = []
    found += credential_findings(conn.execute(_CRED_SQL, (now - CREDENTIAL_WINDOW,)).fetchall())
    found += rate_findings(conn.execute(_RATE_SQL, (now - RATE_WINDOW,)).fetchall())
    found += consecutive_findings(
        (a, f, list(s), e) for a, f, s, e in conn.execute(
            _STREAK_SQL, (now - timedelta(days=14), FAILS_IN_A_ROW,
                          now - CONSECUTIVE_RECENT)).fetchall())
    found += silent_findings(conn.execute(
        _SILENT_SQL, (now - timedelta(days=SILENT_LOOKBACK_DAYS),)).fetchall(), now)
    found += sourcing_findings(conn.execute(_SOURCING_SQL, (now - RATE_WINDOW,)).fetchall())

    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spend = dict(conn.execute(_SPEND_SQL, (start_today,)).fetchall())
    groups = {g: sum(spend.get(a, 0.0) for a in members)
              for g, (_c, members) in runs.CEILING_GROUPS.items()}
    found += ceiling_findings(
        spend, groups, sum(spend.values()), start_today.date(),
        agent_ceilings=runs.DAILY_CEILINGS,
        group_ceilings={g: c for g, (c, _m) in runs.CEILING_GROUPS.items()},
        global_ceiling=runs.GLOBAL_DAILY_CEILING)
    found += phase_pointer_finding(*_phase_files(shared))
    return found
