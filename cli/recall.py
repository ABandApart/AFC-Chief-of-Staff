"""Hybrid search over the facts table — the recall half of capture-and-recall.

Usage:
    uv run python -m cli.recall "what did I decide about the newsletter"
    uv run python -m cli.recall "Alex Mendez" --limit 3
    uv run python -m cli.recall "Q3 plan" --min-sim 0.5

Embeds the query (gemini-embedding-001, 768-dim, via the cost helper) and
ranks lexical (full-text) and semantic (vector cosine) matches over `facts`
with Reciprocal Rank Fusion — see `agents/_lib/search.py`, which is shared
with the Discord /recall command. Prints the top matches.

Runs wherever `db-url` and `gemini-api-key` are in keychain (barry-agent in
the current single-box setup).
"""

from __future__ import annotations

import argparse
import sys

from agents._lib import db
from agents._lib.db import vector_literal as _vector_literal  # noqa: F401 — re-export for tests
from agents._lib.search import (
    DEFAULT_MIN_SIM,
    embed_query,
    format_result_line,  # noqa: F401 — re-export for tests
    format_results,
    run_query,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hybrid search over captured facts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Example:\n  uv run python -m cli.recall "what did I decide about the newsletter"',
    )
    parser.add_argument("query", help="natural-language query")
    parser.add_argument("--limit", type=int, default=5, help="max results (default 5)")
    parser.add_argument(
        "--min-sim", type=float, default=DEFAULT_MIN_SIM,
        help=f"minimum cosine similarity for a vector match (default {DEFAULT_MIN_SIM}); "
             f"lower to widen recall, raise to suppress weak matches",
    )
    args = parser.parse_args()

    qvec = embed_query(args.query)
    with db.connection() as conn:
        rows = run_query(conn, args.query, qvec, limit=args.limit, min_sim=args.min_sim)

    print(format_results(args.query, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
