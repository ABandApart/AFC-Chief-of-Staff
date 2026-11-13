"""Outcome types and the Notion-side outcome write (`PRD-outcome-attribution.md`, N9 I5).

The `/outcome` cog and the Notion Outcomes import share the type lists here, so a
job that never loads discord.py (`agents/notion/records_sync.py`) still checks
the same types and the same contract rule.

The cog keeps its own insert (`discord_bot/brain.insert_outcome`), which takes a
company the operator picked from a type-ahead. A Notion row has only free text
in Company, so `match_company` resolves that text here, the way the type-ahead
would have offered it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agents._lib.outreach import normalize_domain

# Outcome types from architecture/80-telemetry-layer.md.
OUTCOME_TYPES = [
    "discovery_call_booked",
    "proposal_sent",
    "engagement_signed",
    "engagement_renewed",
    "maintenance_converted",
    "newsletter_published",
    "roundtable_topic_used",
    "partnership_explored",
]

# Contract outcomes must name a company (D3). Mirrored by the database CHECK
# `outcomes_engagement_has_client_ck` (migration 0031); change both together.
CONTRACT_TYPES = frozenset(
    {"engagement_signed", "engagement_renewed", "maintenance_converted"}
)


def requires_company(outcome_type: str) -> bool:
    return outcome_type in CONTRACT_TYPES


def looks_like_domain(text: str) -> bool:
    return "." in text and not any(ch.isspace() for ch in text)


def _target_by_domain(cur: Any, domain: str) -> dict | None:
    cur.execute("SELECT id, company_name FROM outreach_targets WHERE company_domain = %s",
                (domain,))
    return cur.fetchone()


def _client_by_domain(cur: Any, name: str, domain: str) -> dict:
    """Find-or-create on the domain key, as `/outcome` does. Race-safe."""
    cur.execute("INSERT INTO clients (name, domain) VALUES (%s, %s) "
                "ON CONFLICT (domain) DO NOTHING", (name, domain))
    cur.execute("SELECT id, name, domain FROM clients WHERE domain = %s", (domain,))
    return cur.fetchone()


def match_company(cur: Any, text: str | None) -> tuple[dict | None, int | None, str | None]:
    """Resolve a Notion Company cell to (client, target id, problem). `cur` is a
    dict-row cursor.

    The rules follow the `/outcome` type-ahead, which offers clients, then
    outreach targets, then "New client" for a typed domain:

    1. A domain (has a dot, no spaces) matches a client or target on the
       normalized domain. A target with no client yet gets one, named after the
       target. An unknown domain creates a client, as picking "New client" for a
       typed domain does.
    2. A name must equal one client's name, or one outreach target's company
       name, ignoring case. A name never creates a client: a typo would split
       KR3's pairing across two clients.

    `problem` says why nothing matched; a blank cell is no company and no problem.
    """
    text = (text or "").strip()
    if not text:
        return None, None, None
    if looks_like_domain(text):
        domain = normalize_domain(text)
        target = _target_by_domain(cur, domain)
        cur.execute("SELECT id, name, domain FROM clients WHERE domain = %s", (domain,))
        client = cur.fetchone()
        if client is None:
            client = _client_by_domain(cur, target["company_name"] if target else domain,
                                       domain)
        return client, target["id"] if target else None, None
    cur.execute("SELECT id, name, domain FROM clients WHERE lower(name) = lower(%s)", (text,))
    clients = cur.fetchall()
    if len(clients) == 1:
        client = clients[0]
        target = _target_by_domain(cur, client["domain"]) if client["domain"] else None
        return client, target["id"] if target else None, None
    if len(clients) > 1:
        return None, None, f"{len(clients)} clients are named {text!r}; use the domain"
    cur.execute("SELECT id, company_name, company_domain FROM outreach_targets "
                "WHERE lower(company_name) = lower(%s)", (text,))
    targets = cur.fetchall()
    if len(targets) == 1:
        t = targets[0]
        return _client_by_domain(cur, t["company_name"], t["company_domain"]), t["id"], None
    if len(targets) > 1:
        return None, None, f"{len(targets)} outreach targets are named {text!r}; use the domain"
    return None, None, f"no client or outreach target matches {text!r}"


def insert(cur: Any, *, outcome_type: str, description: str, value: float | None,
           client: dict | None, target_id: int | None, recorded_on: date | None) -> int:
    """Insert one outcome row and return its id. A missing date means now."""
    cur.execute(
        """
        INSERT INTO outcomes (outcome_type, outcome_value, description, client_id,
                              attributed_target_id, recorded_at)
        VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
        RETURNING id
        """,
        (outcome_type, value, description, client["id"] if client else None, target_id,
         recorded_on),
    )
    return cur.fetchone()["id"]
