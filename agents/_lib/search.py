"""Hybrid search over the facts table — shared by the recall CLI and the
Discord /recall command.

Ranking uses **Reciprocal Rank Fusion** (RRF) rather than a weighted sum of
raw scores: ts_rank_cd (unbounded, ~0.01–0.1 on short facts) and cosine
similarity (~0.55–0.85 after the floor) live on incomparable scales, so raw
blending let the semantic side silently dominate. RRF is scale-free:
each source contributes 1/(k + rank), summed. k=60 is the standard constant.

Scores are reported normalized to 0..1 for display: 1.0 = top-ranked by both
sources, 0.5 = top-ranked by one source only.

The vector CTE fetches top-50 by pure distance (index-friendly — no filter
inside the ordered scan) and applies the similarity floor afterwards.
Expired facts (`expires_at` in the past) are excluded from both sources.
"""

from __future__ import annotations

from typing import Any

import psycopg

from agents._lib.db import vector_literal
from agents._lib.runs import agent_run

# Minimum cosine similarity for a vector match to count. Without it, the
# nearest-50 vector search returns *something* for every query (including
# gibberish), since no fact is ever infinitely far away. Tuned for
# gemini-embedding-001 (768, normalized): empirically relevant matches score
# ~0.65+, unrelated/noise ~0.5, gibberish ~0.48 — but a vague low-information
# fact was measured at 0.551 vs pure gibberish (refactor validation,
# 2026-07-06), so 0.55 sat exactly at the noise ceiling. 0.57 keeps margin
# on both sides. Lexical matches (`@@ tsq`) are naturally floored by token
# overlap, so the floor only applies to the semantic half. Revisit as the
# fact corpus grows.
DEFAULT_MIN_SIM = 0.57

# RRF constant. Larger k flattens the rank curve; 60 is the literature default.
RRF_K = 60

HYBRID_SQL = """
WITH query_input AS (
    SELECT plainto_tsquery('english', %(q)s) AS tsq,
           %(emb)s::vector(768) AS query_embedding
),
fts AS (
    SELECT f.id,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank_cd(f.content_tsv, qi.tsq) DESC, f.id
           ) AS rnk
    FROM facts f, query_input qi
    WHERE f.content_tsv @@ qi.tsq
      AND (f.expires_at IS NULL OR f.expires_at > now())
),
vec_candidates AS (
    SELECT f.id, f.embedding <=> qi.query_embedding AS dist
    FROM facts f, query_input qi
    WHERE f.embedding IS NOT NULL
      AND (f.expires_at IS NULL OR f.expires_at > now())
    ORDER BY dist
    LIMIT 50
),
vec AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY dist, id) AS rnk
    FROM vec_candidates
    WHERE (1 - dist) >= %(min_sim)s
),
combined AS (
    SELECT COALESCE(fts.id, vec.id) AS id,
           COALESCE(1.0 / (%(rrf_k)s + fts.rnk), 0)
         + COALESCE(1.0 / (%(rrf_k)s + vec.rnk), 0) AS score
    FROM fts FULL OUTER JOIN vec USING (id)
)
SELECT f.id, f.content, f.domain, f.confidence, f.created_at, c.score
FROM combined c
JOIN facts f ON f.id = c.id
ORDER BY c.score DESC, f.id
LIMIT %(limit)s;
"""


def embed_query(query: str, *, trigger_kind: str = "manual") -> list[float]:
    """Embed the query through the cost helper (recall agent, gemini)."""
    with agent_run("recall", "infrastructure", trigger_kind=trigger_kind) as run:
        return run.call_embedding([query])[0]


def run_query(
    conn: psycopg.Connection,
    query: str,
    qvec: list[float],
    *,
    limit: int,
    min_sim: float = DEFAULT_MIN_SIM,
) -> list[dict[str, Any]]:
    """Execute hybrid search; return rows as dicts ordered by score desc.

    `score` is the RRF sum normalized so a fact top-ranked by both sources
    scores 1.0 and a fact top-ranked by one source scores 0.5.
    """
    with conn.cursor() as cur:
        cur.execute(
            HYBRID_SQL,
            {
                "q": query,
                "emb": vector_literal(qvec),
                "min_sim": min_sim,
                "rrf_k": RRF_K,
                "limit": limit,
            },
        )
        rows = cur.fetchall()
    norm = (RRF_K + 1) / 2.0
    return [
        {
            "id": r[0],
            "content": r[1],
            "domain": r[2],
            "confidence": r[3],
            "created_at": r[4],
            "score": float(r[5]) * norm,
        }
        for r in rows
    ]


def format_result_line(content: str, domain: str | None, score: float) -> str:
    dom = f"({domain}) " if domain else ""
    return f"  [{score:.2f}] {dom}{content}"


def format_results(query: str, rows: list[dict[str, Any]]) -> str:
    """Render the ranked results (pure — unit-tested)."""
    if not rows:
        return f'No matching facts for: "{query}"'
    n = len(rows)
    header = f'{n} fact{"s" if n != 1 else ""} for: "{query}"'
    lines = [format_result_line(r["content"], r["domain"], r["score"]) for r in rows]
    return header + "\n" + "\n".join(lines)
