"""Seed the operator's InterestSignal nodes for Tartt interest scoring (Phase 4).

Interest scoring is cosine(ContentItem summary, InterestSignal topic) — so these
topics are what Tartt's reading recs + mode-1 cognify gate score against. Ids are
deterministic (uuid5 of the lowercased topic), so editing this list and re-running
upserts rather than duplicating.

**OPERATOR: edit `INTERESTS` to your actual reading interests, then run:**
    uv run python -m cli.seed_interests

Needs the keys + cognee (barry-agent runtime). `configure_cognee()` runs first.
"""

from __future__ import annotations

import asyncio

from agents._lib import cognee_setup
from agents.tartt import content_graph

# (topic_label, weight). Weight is reserved for future weighting; v1 scoring is
# max-cosine (unweighted). Starter set — replace with your own.
INTERESTS: list[tuple[str, float]] = [
    ("AI agents and autonomous systems for small businesses", 1.0),
    ("Fractional and solo consulting business models", 1.0),
    ("Developer tools, productivity, and workflow automation", 1.0),
    ("Go-to-market and lead generation for B2B services", 1.0),
]


def main() -> int:
    cognee_setup.configure_cognee()
    ids = asyncio.run(content_graph.add_interest_signals(INTERESTS))
    print(f"seeded {len(ids)} interest signal(s):")
    for (label, _weight), node in zip(INTERESTS, ids, strict=True):
        print(f"  {node}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
