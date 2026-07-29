"""Graph recall CLI — ask the brain a question, get a synthesized answer.

    uv run python -m cli.recall "what did I decide about the newsletter"

Runs cognee GRAPH_COMPLETION (see `agents/_lib/graph_recall`). Needs the keys +
cognee in keychain/venv (barry-agent). `configure_cognee()` runs at startup.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agents._lib import cognee_setup, graph_recall


async def _run(query: str) -> str:
    cognee_setup.configure_cognee()
    return await graph_recall.recall(query)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the graph brain a question.")
    parser.add_argument("query", help="natural-language query")
    args = parser.parse_args()
    print(asyncio.run(_run(args.query)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
