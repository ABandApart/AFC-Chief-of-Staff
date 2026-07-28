"""W3 runtime shape-check — verify our DataPoints cognify into the intended graph.

Constructs a small structured example from `agents/_lib/ontology` (an Org, two
People who work there, a Meeting with them as participants + a produced Fact),
inserts it via cognee's `add_data_points` (which walks the relationship fields
into nodes/edges), and runs a 2-hop `GRAPH_COMPLETION` that must traverse
Meeting → participant → works_at → Organization.

Run (barry-agent, after `uv sync --group cognee`):
    uv run python -m agents.test.ontology_shape

⚠️ API note: the low-level insert is documented as `cognee.low_level.add_data_points`.
If the import path or signature differs in cognee 1.4.0, adjust the import below
(and record the working form) — this is the same "verify against the installed
version" step the W2 config went through. Writes to `aiadaptive_cognee`; prune
with the rest before W4 go-live.
"""

from __future__ import annotations

import asyncio
import sys

from agents._lib import cognee_setup
from agents._lib import ontology as o

QUERY = (
    "Who took part in the discovery call with Two Rivers Advisory, "
    "and what workflow do they want removed?"
)


def build_example() -> o.Meeting:
    tra = o.Organization(name="Two Rivers Advisory", segment="advisory")
    elena = o.Person(name="Elena Ruiz", role="Managing Partner",
                     relationship_type="prospect", works_at=tra)
    david = o.Person(name="David Okafor", role="Operations Lead",
                     relationship_type="prospect", works_at=tra)
    fact = o.Fact(
        content="Two Rivers Advisory wants discovery-call notes turned into "
                "client-ready summaries within an hour, not a week.",
        domain="project", source_type="meeting",
        about_orgs=[tra], about_people=[elena, david],
    )
    return o.Meeting(
        title="Discovery call — Two Rivers Advisory",
        summary="Two pains: slow client summaries, and no memory across calls.",
        meeting_date="2026-07-02",
        participants=[elena, david],
        produced_facts=[fact],
    )


async def main() -> int:
    cognee_setup.configure_cognee()
    import cognee
    from cognee import SearchType
    from cognee.low_level import add_data_points  # adjust if 1.4.0 differs

    print("[1/2] add_data_points(meeting) — recursive into people/org/fact...")
    meeting = build_example()
    await add_data_points([meeting])
    print("  inserted")

    print("[2/2] 2-hop GRAPH_COMPLETION...")
    result = await cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=QUERY)
    answer = str(result)
    print(f"  → {answer[:400]}")

    ok = ("Elena" in answer or "David" in answer) and "summar" in answer.lower()
    print("  ✅ 2-hop traversal correct" if ok else
          "  ⚠️ answer didn't clearly reflect the participants + workflow — inspect")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
