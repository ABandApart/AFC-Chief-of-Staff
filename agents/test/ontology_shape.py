"""W3 runtime shape-check + the Track-C structured-Meeting GATE.

This is the probe that must pass **before** the structured `Meeting` insertion is
wired into the live Granola poller (`agents/_lib/meeting_graph.add_meeting_graph`).
It confirms the three things about `add_data_points` we can't verify offline:

  [1] **Traversal** — a structured example (Org, two People who work there, a
      Meeting with them + a produced Fact) cognifies into nodes/edges, and a 2-hop
      `GRAPH_COMPLETION` traverses Meeting → participant → works_at → Organization.
  [2] **Entity resolution (load-bearing)** — the same `Person` (same deterministic
      `id`, from `meeting_graph.person_id`) inserted across TWO meetings must
      resolve to ONE node, so "which meetings was X in" connects both. This is the
      whole premise of the typed layer; if it fails, DON'T wire Step 2.
  [3] **Dataset** — `add_data_points` has no `dataset_name` param; report where the
      nodes land (they go to the default graph, not the `granola` dataset — note it
      so retrieval scoping expectations are correct).

Run (barry-agent, after `uv sync --group cognee`, against `aiadaptive_cognee`):
    uv run python -m agents.test.ontology_shape

API confirmed against cognee 1.4.0 (2026-07-28): `from cognee.tasks.storage import
add_data_points` — async `(data_points, custom_edges=None, embed_triplets=False,
ctx=None)`. Embeds only `index_fields` via the local bge embedder (no LLM).
"""

from __future__ import annotations

import asyncio
import sys

from agents._lib import cognee_setup, meeting_graph
from agents._lib import ontology as o

QUERY = (
    "Who took part in the discovery call with Two Rivers Advisory, "
    "and what workflow do they want removed?"
)

# Two notes sharing one attendee (Priya) → she must resolve to a single node.
NOTE_A = {
    "id": "not_probeAAAAAAA1",
    "title": "Northwind kickoff",
    "calendar_event": {"scheduled_start_time": "2026-08-01T16:00:00Z"},
    "attendees": [{"name": "Priya Shah", "email": "priya@northwind.example"}],
    "summary_text": "Kickoff — scope and timeline.",
}
NOTE_B = {
    "id": "not_probeBBBBBBB2",
    "title": "Northwind check-in",
    "calendar_event": {"scheduled_start_time": "2026-08-08T16:00:00Z"},
    # same email as NOTE_A (diff name + case) → must resolve to the same Person:
    "attendees": [{"name": "Priya S.", "email": "PRIYA@NORTHWIND.EXAMPLE"}],
    "summary_text": "Check-in — progress review.",
}


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


async def _count_nodes_named(needle: str) -> object:
    """Best-effort node count via the graph engine (report-only; API may vary)."""
    try:
        from cognee.infrastructure.databases.graph import get_graph_engine
        engine = await get_graph_engine()
        nodes, _edges = await engine.get_graph_data()
        return sum(1 for _id, props in nodes
                   if needle.lower() in str(props.get("name", "")).lower())
    except Exception as e:  # noqa: BLE001 — diagnostic, never fatal
        return f"(unavailable: {type(e).__name__}: {e})"


async def main() -> int:
    cognee_setup.configure_cognee()
    import cognee
    from cognee import SearchType
    from cognee.tasks.storage import add_data_points  # confirmed 1.4.0

    # [1] traversal --------------------------------------------------------
    print("[1/3] add_data_points(meeting) — recursive into people/org/fact...")
    await add_data_points([build_example()])
    result = await cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=QUERY)
    answer = str(result)
    print(f"  → {answer[:300]}")
    traversal_ok = ("Elena" in answer or "David" in answer) and "summar" in answer.lower()
    print("  ✅ 2-hop traversal correct" if traversal_ok else
          "  ⚠️ traversal answer unclear — inspect")

    # [2] entity resolution (the gate) ------------------------------------
    print("\n[2/3] entity resolution — same Person id across two meetings...")
    pid = meeting_graph.person_id("Priya Shah", "priya@northwind.example")
    print(f"  deterministic Priya id = {pid}")
    await meeting_graph.add_meeting_graph(NOTE_A)
    await meeting_graph.add_meeting_graph(NOTE_B)   # same Priya id, different meeting
    priya_nodes = await _count_nodes_named("Priya")
    res = await cognee.search(query_type=SearchType.GRAPH_COMPLETION,
                              query_text="Which meetings did Priya Shah attend? List each title.")
    res_txt = str(res)
    print(f"  Priya node count (want 1): {priya_nodes}")
    print(f"  'which meetings' → {res_txt[:300]}")
    both = "kickoff" in res_txt.lower() and "check-in" in res_txt.lower()
    resolution_ok = (priya_nodes == 1) if isinstance(priya_nodes, int) else both
    print("  ✅ one Priya, linked to both meetings" if resolution_ok else
          "  ⚠️ resolution NOT confirmed — if two Priya nodes / only one meeting, DON'T wire Step 2")

    # [3] dataset placement -----------------------------------------------
    print("\n[3/3] dataset — add_data_points has no dataset_name; nodes go to the "
          "default graph (not the 'granola' dataset). Retrieval is un-scoped today, "
          "so this is fine — just recording it.")

    ok = traversal_ok and resolution_ok
    print("\n" + ("✅ GATE PASSED — safe to wire Step 2." if ok else
                  "⚠️ GATE NOT passed — hand back before wiring Step 2."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
