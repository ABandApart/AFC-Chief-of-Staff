"""News observation poller (Track O, Part 1).

For each profilable firm — active target or accepted discovery (R1.9) — reads its
Google News and newsroom feeds, writes each new story once into the unclassified
queue (`outreach_watch_signals`), and attaches it to the firm's Organization node
in the graph so the packet's background traversal has something to return.

**No LLM.** Feed GET + dedup + typed-node writes, so it writes no `agent_runs`
rows, trips no ceiling, and cannot fail from a provider outage (`40-`,
Outreach_loops). Classification — deciding whether a story is a funding round or
an expansion — is Part 2's job and Part 2's cost.

**Two homes per story, deliberately.** The SQL queue drives classification (Part 2
reads it); the graph node drives the packet's background. A firm needs both: the
queue so a trigger can be produced (which promotes an accepted discovery, R0.3),
the graph so the eventual packet can show what the company has been doing.

**The graph half runs on barry-agent only** (C3). cognee is not importable on the
build box, so `--no-graph` writes only the SQL queue — which is what the build
box's tests exercise — and the full run writes both.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from agents._lib import db, outreach
from agents.outreach import news, profile_graph

logger = logging.getLogger(__name__)


def _signal_row(firm: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Shape one feed item into an insert_watch_signal row.

    The excerpt is the title — Part 1 reads feed metadata only, never the article
    body (that fetch is Part 2's, gated on the story mattering). The event date's
    basis is carried in the excerpt tail rather than a column, matching the
    evidence poller's habit of keeping display precision honest without a schema
    field the queue does not need.
    """
    return {
        "parent": firm["parent"],
        "parent_id": firm["id"],
        "source_kind": item["source_kind"],
        "source_url": item["url"],
        "dedup_key": item["dedup_key"],
        "excerpt": item["title"],
    }


async def profile_firm(conn: object, firm: dict[str, Any], *, write_graph: bool,
                       ) -> dict[str, Any]:
    """Observe one firm's news. Never raises for a per-firm failure — one dead
    feed or graph hiccup must not cost the rest of the cycle its cycle."""
    items = news.feed_items_for_firm(firm)
    # Collapse the two feeds' overlap on the dedup key before writing, so a story
    # carried by both Google News and the newsroom counts once per run.
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique.setdefault(item["dedup_key"], item)

    node_id = firm.get("cognee_node_id")
    new_signals = 0
    graphed = 0
    for item in unique.values():
        if outreach.insert_watch_signal(conn, _signal_row(firm, item)):
            new_signals += 1
        if write_graph:
            try:
                if node_id is None:
                    node_id = await profile_graph.add_organization(firm)
                    outreach.set_cognee_node_id(
                        conn, firm["parent"], firm["id"], node_id)
                await profile_graph.add_news_item(item["url"], item["title"], firm)
                graphed += 1
            except Exception:  # noqa: BLE001 - a graph failure is not a run failure
                logger.exception("profile: graph write failed for %s",
                                 firm["company_name"])

    outreach.mark_news_polled(conn, firm["parent"], firm["id"])
    logger.info("profile: %s (%s) — %d item(s), %d new signal(s), %d graphed",
                firm["company_name"], firm["parent"], len(unique),
                new_signals, graphed)
    return {"firm": firm["company_name"], "items": len(unique),
            "new": new_signals, "graphed": graphed}


async def profile(*, write_graph: bool = True) -> dict[str, int]:
    """One observation pass over every profilable firm."""
    totals = {"firms": 0, "items": 0, "new": 0, "graphed": 0}
    with db.connection() as conn:
        firms = outreach.profilable_firms(conn)
        for firm in firms:
            try:
                result = await profile_firm(conn, firm, write_graph=write_graph)
            except Exception:
                logger.exception("profile: failed on %s", firm["company_name"])
                continue
            totals["firms"] += 1
            totals["items"] += result["items"]
            totals["new"] += result["new"]
            totals["graphed"] += result["graphed"]
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-graph", action="store_true",
                        help="Write only the SQL queue, not the graph "
                             "(the build box has no cognee — C3).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the firms and feeds; write nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.dry_run:
        with db.connection() as conn:
            firms = outreach.profilable_firms(conn)
        for firm in firms:
            feeds = ", ".join(k for k, _ in news.feed_urls_for(firm))
            print(f"  {firm['parent']:<9} {firm['company_name']:<34} {feeds}")
        print(f"\n--dry-run: {len(firms)} firm(s), writing nothing")
        return 0

    # The graph writes embed locally (fastembed/ONNX, no key) — but ONLY if
    # cognee is configured first. Without this call cognee boots its default
    # LiteLLM→OpenAI embedder, which we do not configure, so every embed 422s and
    # the retry ladder makes the run crawl. Every cognee-touching sibling
    # (cli/recall.py, seed_interests, publish_playbooks, the run.py entrypoints)
    # calls this; this one did not, which is barry-agent's 2026-08-27 finding.
    # Guarded by --no-graph so the build box, which has no cognee, never imports
    # it.
    if not args.no_graph:
        from agents._lib import cognee_setup
        cognee_setup.configure_cognee()

    totals = asyncio.run(profile(write_graph=not args.no_graph))
    print(f"profiled {totals['firms']} firm(s): {totals['items']} item(s), "
          f"{totals['new']} new signal(s), {totals['graphed']} graphed")
    if args.no_graph:
        print("  (--no-graph: SQL queue only; run without it on barry-agent for "
              "the graph)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
