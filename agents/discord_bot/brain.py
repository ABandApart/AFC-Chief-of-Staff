"""Postgres write surface for the Discord bot.

All access goes through the shared pool in `agents/_lib/db.py` — no
per-operation connections. There is no ORM; the schema is small and SQL is
explicit.

2026-07 refactor: facts are inserted as a batch in one transaction (a
mid-batch failure no longer leaves a partial capture), and a near-duplicate
lookup guards the corpus against re-captured thoughts.
"""

from __future__ import annotations

from typing import Any

from agents._lib import db
from agents._lib.db import EMBEDDING_DIM, vector_literal


def insert_facts(
    facts: list[dict[str, Any]], *, source_type: str, source_ref: str | None
) -> list[int]:
    """Insert fact rows atomically (one transaction). Returns new ids in order.

    Each fact dict needs: content, domain, confidence, embedding, and
    optionally context. Raises ValueError if any embedding has the wrong
    dimension (a cheap guard against a silent provider/model mismatch that
    would otherwise fail at the DB with a less obvious error).
    """
    for fact in facts:
        if len(fact["embedding"]) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding has {len(fact['embedding'])} dims, expected {EMBEDDING_DIM} "
                f"(check the embedding model — facts.embedding is vector(768))"
            )

    ids: list[int] = []
    with db.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for fact in facts:
                    cur.execute(
                        """
                        INSERT INTO facts
                            (content, source_type, source_ref, context, domain,
                             confidence, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                        RETURNING id
                        """,
                        (
                            fact["content"],
                            source_type,
                            source_ref,
                            fact.get("context"),
                            fact["domain"],
                            fact["confidence"],
                            vector_literal(fact["embedding"]),
                        ),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    ids.append(row[0])
    return ids


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


def find_near_duplicate(embedding: list[float], *, threshold: float) -> tuple[int, float] | None:
    """Return (fact_id, similarity) of the nearest stored fact if it is at or
    above `threshold` cosine similarity; else None.

    One indexed vector lookup — used at capture time so re-captured thoughts
    don't accumulate as near-identical facts that pollute recall.
    """
    vec = vector_literal(embedding)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, 1 - (embedding <=> %s::vector) AS sim
                FROM facts
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT 1
                """,
                (vec, vec),
            )
            row = cur.fetchone()
    if row is not None and float(row[1]) >= threshold:
        return (row[0], float(row[1]))
    return None


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
