"""Postgres write surface for the Discord bot.

Phase 3.2 needs exactly one operation: insert a fact (with embedding) into
the `facts` table. Future cogs (3.4 /outcome, Phase 5 task_candidates) add
their own helpers here.

The bot is otherwise stateless — every write goes straight to the brain.
There is no ORM; the schema is small and SQL is explicit.
"""

from __future__ import annotations

import psycopg

from agents._lib.runs import _keychain_get

EMBEDDING_DIM = 768


def _vector_literal(embedding: list[float]) -> str:
    """Format a Python float list as a pgvector string literal: '[a,b,c]'.

    Inserted with an explicit `::vector` cast, which avoids needing the
    pgvector psycopg adapter (and its numpy dependency) in this phase.
    """
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def insert_fact(
    *,
    content: str,
    source_type: str,
    source_ref: str | None,
    domain: str | None,
    confidence: float,
    embedding: list[float],
    context: str | None = None,
) -> int:
    """Insert one fact row. Returns the new fact id.

    Raises ValueError if the embedding is the wrong dimension (a cheap guard
    against a silent provider/model mismatch that would otherwise fail at the
    DB with a less obvious error).
    """
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"embedding has {len(embedding)} dims, expected {EMBEDDING_DIM} "
            f"(check the embedding model — facts.embedding is vector(768))"
        )

    db_url = _keychain_get("db-url")
    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO facts
                    (content, source_type, source_ref, context, domain,
                     confidence, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                RETURNING id
                """,
                (
                    content,
                    source_type,
                    source_ref,
                    context,
                    domain,
                    confidence,
                    _vector_literal(embedding),
                ),
            )
            return cur.fetchone()[0]
