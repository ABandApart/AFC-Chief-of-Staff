"""Hybrid search over the facts table — the recall half of capture-and-recall.

Usage:
    uv run python -m cli.recall "what did I decide about the newsletter"
    uv run python -m cli.recall "Alex Mendez" --limit 3
    uv run python -m cli.recall "Q3 plan" --lex-weight 0.6 --sem-weight 0.4

Embeds the query (gemini-embedding-001, 768-dim, via the cost helper) and
blends lexical (full-text) and semantic (vector cosine) ranking over `facts`,
per architecture/30-memory-layer.md. Prints the top matches.

Runs wherever `db-url` and `gemini-api-key` are in keychain (barry-agent in
the current single-box setup).
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from agents._lib.runs import _keychain_get, agent_run

# Weighted blend of full-text (ts_rank_cd) and semantic (1 - cosine distance).
# Defaults from 30-memory-layer.md; overridable via flags for tuning.
DEFAULT_LEX_WEIGHT = 0.4
DEFAULT_SEM_WEIGHT = 0.6

# Minimum cosine similarity for a vector match to count. Without it, the
# nearest-50 vector search returns *something* for every query (including
# gibberish), since no fact is ever infinitely far away. Tuned for
# gemini-embedding-001 (768, normalized): empirically relevant matches score
# ~0.65+, unrelated/noise ~0.5, gibberish ~0.48 — so 0.55 cleanly separates
# signal from noise. Lexical matches (`@@ tsq`) are naturally floored by token
# overlap, so the floor only applies to the semantic half. Revisit as the
# fact corpus grows. Overridable via --min-sim.
DEFAULT_MIN_SIM = 0.55

HYBRID_SQL = """
WITH query_input AS (
    SELECT plainto_tsquery('english', %(q)s) AS tsq,
           %(emb)s::vector(768) AS query_embedding
),
fts AS (
    SELECT f.id, ts_rank_cd(f.content_tsv, qi.tsq) AS lex
    FROM facts f, query_input qi
    WHERE f.content_tsv @@ qi.tsq
),
vec AS (
    SELECT f.id, 1 - (f.embedding <=> qi.query_embedding) AS sem
    FROM facts f, query_input qi
    WHERE f.embedding IS NOT NULL
      AND (1 - (f.embedding <=> qi.query_embedding)) >= %(min_sim)s
    ORDER BY f.embedding <=> qi.query_embedding
    LIMIT 50
),
combined AS (
    SELECT COALESCE(fts.id, vec.id) AS id,
           COALESCE(fts.lex, 0) * %(lexw)s + COALESCE(vec.sem, 0) * %(semw)s AS score
    FROM fts FULL OUTER JOIN vec USING (id)
)
SELECT f.id, f.content, f.domain, f.confidence, f.created_at, c.score
FROM combined c
JOIN facts f ON f.id = c.id
ORDER BY c.score DESC
LIMIT %(limit)s;
"""


def _vector_literal(embedding: list[float]) -> str:
    """Format a float list as a pgvector string literal: '[a,b,c]'."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def embed_query(query: str) -> list[float]:
    """Embed the query through the cost helper (recall agent, gemini)."""
    with agent_run("recall", "infrastructure", trigger_kind="manual") as run:
        return run.call_embedding([query])[0]


def run_query(
    conn: psycopg.Connection,
    query: str,
    qvec: list[float],
    *,
    limit: int,
    lex_weight: float,
    sem_weight: float,
    min_sim: float,
) -> list[dict]:
    """Execute hybrid search; return rows as dicts ordered by score desc."""
    with conn.cursor() as cur:
        cur.execute(
            HYBRID_SQL,
            {
                "q": query,
                "emb": _vector_literal(qvec),
                "lexw": lex_weight,
                "semw": sem_weight,
                "min_sim": min_sim,
                "limit": limit,
            },
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "content": r[1],
            "domain": r[2],
            "confidence": r[3],
            "created_at": r[4],
            "score": float(r[5]),
        }
        for r in rows
    ]


def format_result_line(content: str, domain: str | None, score: float) -> str:
    dom = f"({domain}) " if domain else ""
    return f"  [{score:.2f}] {dom}{content}"


def format_results(query: str, rows: list[dict]) -> str:
    """Render the ranked results (pure — unit-tested)."""
    if not rows:
        return f'No matching facts for: "{query}"'
    n = len(rows)
    header = f'{n} fact{"s" if n != 1 else ""} for: "{query}"'
    lines = [format_result_line(r["content"], r["domain"], r["score"]) for r in rows]
    return header + "\n" + "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hybrid search over captured facts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Example:\n  uv run python -m cli.recall "what did I decide about the newsletter"',
    )
    parser.add_argument("query", help="natural-language query")
    parser.add_argument("--limit", type=int, default=5, help="max results (default 5)")
    parser.add_argument(
        "--lex-weight", type=float, default=DEFAULT_LEX_WEIGHT,
        help=f"lexical weight (default {DEFAULT_LEX_WEIGHT})",
    )
    parser.add_argument(
        "--sem-weight", type=float, default=DEFAULT_SEM_WEIGHT,
        help=f"semantic weight (default {DEFAULT_SEM_WEIGHT})",
    )
    parser.add_argument(
        "--min-sim", type=float, default=DEFAULT_MIN_SIM,
        help=f"minimum cosine similarity for a vector match (default {DEFAULT_MIN_SIM}); "
             f"lower to widen recall, raise to suppress weak matches",
    )
    args = parser.parse_args()

    qvec = embed_query(args.query)
    db_url = _keychain_get("db-url")
    with psycopg.connect(db_url) as conn:
        rows = run_query(
            conn,
            args.query,
            qvec,
            limit=args.limit,
            lex_weight=args.lex_weight,
            sem_weight=args.sem_weight,
            min_sim=args.min_sim,
        )

    print(format_results(args.query, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
