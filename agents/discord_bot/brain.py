"""Postgres write surface for the Discord bot (operational tables only).

Access goes through the shared pool in `agents/_lib/db.py`. Post-pivot, the
knowledge lives in the cognee graph — capture writes there via
`agents/_lib/ingest`, and recall reads there via `agents/_lib/graph_recall`.
What remains here is the `/outcome` write into the operational `outcomes`
table, with its `clients` / `outreach_targets` attribution
(`PRD-outcome-attribution.md`). The legacy `facts` table and its fact-link
autocomplete were retired in migration 0006 (fact-linking dropped — operator
decision, W5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

from agents._lib import db

if TYPE_CHECKING:
    from agents.discord_bot.cogs.outcomes import CompanyRef


def search_companies(term: str, limit: int = 25) -> tuple[list[dict], list[dict]]:
    """Clients, and outreach targets not yet clients, matching name or domain."""
    like = f"%{(term or '').strip().lower()}%"
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, name, domain FROM clients
                WHERE lower(name) LIKE %(like)s OR domain LIKE %(like)s
                ORDER BY name
                LIMIT %(limit)s
                """,
                {"like": like, "limit": limit},
            )
            clients = cur.fetchall()
            cur.execute(
                """
                SELECT company_name, company_domain FROM outreach_targets t
                WHERE (lower(company_name) LIKE %(like)s
                       OR company_domain LIKE %(like)s)
                  AND NOT EXISTS (SELECT 1 FROM clients c
                                  WHERE c.domain = t.company_domain)
                ORDER BY company_name
                LIMIT %(limit)s
                """,
                {"like": like, "limit": limit},
            )
            targets = cur.fetchall()
    return clients, targets


def _client_by_domain(cur: Any, name: str, domain: str) -> dict:
    """Find-or-create on the domain key. Race-safe: ON CONFLICT, then re-select."""
    cur.execute(
        "INSERT INTO clients (name, domain) VALUES (%s, %s) "
        "ON CONFLICT (domain) DO NOTHING",
        (name, domain),
    )
    cur.execute("SELECT id, name, domain FROM clients WHERE domain = %s", (domain,))
    row = cur.fetchone()
    assert row is not None
    return row


def _target_id(cur: Any, domain: str | None) -> int | None:
    if not domain:
        return None
    cur.execute("SELECT id FROM outreach_targets WHERE company_domain = %s", (domain,))
    row = cur.fetchone()
    return row["id"] if row else None


def _resolve_company(
    cur: Any, company: CompanyRef, client_name: str | None
) -> tuple[dict, int | None]:
    """→ (client row, target id or None). A pick that no longer resolves raises
    KeyError, which the cog turns into "pick again"."""
    if company.kind == "client":
        cur.execute(
            "SELECT id, name, domain FROM clients WHERE id = %s", (int(company.key),)
        )
        client = cur.fetchone()
        if client is None:
            raise KeyError("That client no longer exists")
        return client, _target_id(cur, client["domain"])

    if company.kind == "target":
        cur.execute(
            "SELECT id, company_name FROM outreach_targets WHERE company_domain = %s",
            (company.key,),
        )
        target = cur.fetchone()
        if target is None:
            raise KeyError(f"No outreach target for {company.key}")
        client = _client_by_domain(cur, target["company_name"], company.key)
        return client, target["id"]

    if company.kind == "new":
        name = (client_name or "").strip() or company.key
        if company.domain:
            client = _client_by_domain(cur, name, company.domain)
            return client, _target_id(cur, company.domain)
        cur.execute(
            "INSERT INTO clients (name) VALUES (%s) RETURNING id, name, domain",
            (name,),
        )
        return cur.fetchone(), None

    raise ValueError(f"unknown company kind: {company.kind!r}")


def insert_outcome(
    *,
    outcome_type: str,
    description: str,
    value: float | None = None,
    company: CompanyRef | None = None,
    client_name: str | None = None,
) -> dict:
    """Insert one outcome row (/outcome command).

    Client creation and the outcome insert share one transaction, so a failed
    outcome never leaves an orphan client. Returns id, client_id, client_name,
    and target_id.
    """
    client: dict | None = None
    target_id: int | None = None
    with db.connection() as conn:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            if company is not None:
                client, target_id = _resolve_company(cur, company, client_name)
            cur.execute(
                """
                INSERT INTO outcomes (outcome_type, outcome_value, description,
                                      client_id, attributed_target_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    outcome_type,
                    value,
                    description,
                    client["id"] if client else None,
                    target_id,
                ),
            )
            row = cur.fetchone()
            assert row is not None
    return {
        "id": row["id"],
        "client_id": client["id"] if client else None,
        "client_name": client["name"] if client else None,
        "target_id": target_id,
    }
