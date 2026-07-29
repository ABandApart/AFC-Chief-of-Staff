"""Postgres write surface for the Discord bot (operational tables only).

Access goes through the shared pool in `agents/_lib/db.py`. Post-pivot, the
knowledge lives in the cognee graph — capture writes there via
`agents/_lib/ingest`, and recall reads there via `agents/_lib/graph_recall`.
What remains here is the single `/outcome` write into the operational `outcomes`
table. The legacy `facts` table and its fact-link autocomplete were retired in
migration 0006 (fact-linking dropped — operator decision, W5).
"""

from __future__ import annotations

from agents._lib import db


def insert_outcome(
    *,
    outcome_type: str,
    description: str,
    value: float | None = None,
) -> int:
    """Insert one outcome row (/outcome command). Returns the id."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outcomes (outcome_type, outcome_value, description)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (outcome_type, value, description),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]
