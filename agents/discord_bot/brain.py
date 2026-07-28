"""Postgres write surface for the Discord bot.

All access goes through the shared pool in `agents/_lib/db.py` — no
per-operation connections. There is no ORM; the schema is small and SQL is
explicit.

W4 (cognee pivot): capture no longer writes the `facts` table — notes are
cognified into the graph (`aiadaptive_cognee`). What remains here is the
message-level dedup guard (still SQL, in `aiadaptive_cos`), the `/outcome`
write, and the fact autocomplete (transitional — rewired to the graph in W5).
"""

from __future__ import annotations

from agents._lib import db


def capture_message_seen(content_hash: str) -> bool:
    """True if a message with this normalized-text hash was already captured.

    Message-level dedup guard: checked before extraction, so an exact re-post
    costs no LLM calls and can't mint new facts (extraction is
    non-deterministic — per-fact cosine dedup alone lets re-posts leak rows).
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM capture_messages WHERE content_hash = %s",
                (content_hash,),
            )
            return cur.fetchone() is not None


def record_capture_message(content_hash: str, message_id: str) -> None:
    """Record a processed capture message's hash (idempotent)."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO capture_messages (content_hash, message_id)
                VALUES (%s, %s)
                ON CONFLICT (content_hash) DO NOTHING
                """,
                (content_hash, message_id),
            )


def search_facts(term: str, limit: int = 20) -> list[tuple[int, str]]:
    """Lightweight fact lookup for slash-command autocomplete.

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
    """Insert one outcome row (Phase 3.4 /outcome command). Returns the id.

    A bad `attributed_fact_id` raises psycopg.errors.ForeignKeyViolation —
    the FK constraint is the validation (no separate existence pre-check).
    The other `attributed_*` columns (prospect/task/content/signal) stay null
    until those tables are populated in later phases.
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
