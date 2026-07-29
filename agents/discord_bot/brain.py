"""Postgres write surface for the Discord bot (operational tables only).

Access goes through the shared pool in `agents/_lib/db.py`. Post-pivot, the
knowledge lives in the cognee graph — what remains here is the `/outcome` write
and the fact autocomplete. The capture-message dedup moved to
`agents/_lib/ingest`; `search_facts` is transitional (queries the legacy `facts`
table; the /outcome fact link is rewired to graph node-ids later in W5).
"""

from __future__ import annotations

from agents._lib import db


def search_facts(term: str, limit: int = 20) -> list[tuple[int, str]]:
    """Lightweight fact lookup for /outcome autocomplete (transitional).

    Empty term → most recent facts. Numeric term also matches the fact id.
    Returns (id, content) pairs, newest first.
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            if term.strip():
                cur.execute(
                    """
                    SELECT id, content FROM facts
                    WHERE content ILIKE %s OR id::text = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (f"%{term.strip()}%", term.strip(), limit),
                )
            else:
                cur.execute(
                    "SELECT id, content FROM facts ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            return [(r[0], r[1]) for r in cur.fetchall()]


def insert_outcome(
    *,
    outcome_type: str,
    description: str,
    value: float | None = None,
    attributed_fact_id: int | None = None,
) -> int:
    """Insert one outcome row (/outcome command). Returns the id.

    A bad `attributed_fact_id` raises psycopg.errors.ForeignKeyViolation — the FK
    constraint is the validation. (W5 will add graph-node linking via
    `attributed_fact_node`.)
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outcomes
                    (outcome_type, outcome_value, description, attributed_fact_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (outcome_type, value, description, attributed_fact_id),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]
