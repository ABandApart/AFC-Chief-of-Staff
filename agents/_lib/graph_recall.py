"""Graph-native recall via cognee GRAPH_COMPLETION (W5).

Replaces the RRF hybrid search over the `facts` table (`_lib/search.py`, removed).
cognee owns retrieval: vector search finds relevant graph triplets, then
traverses the graph to build structured context and generate an answer. So
recall returns a synthesized **answer**, not a ranked list. Runs under
`labeled('recall')` so the query embedding + completion spend lands in the
ledger.

M2 note: cognee owns the vector search here, so our old pgvector-cosine
normalization fix doesn't apply on our side — recall quality with cognee's
un-normalized 768-dim Gemini vectors is a runtime check (W7). If weak, configure
cognee's embedding normalization / distance metric.

`configure_cognee()` must have run first. cognee imported lazily.
"""

from __future__ import annotations

from agents._lib.telemetry_context import labeled

NO_RESULT = "No matching facts."


def answer_text(results: object) -> str:
    """Normalize cognee.search output to a display string (pure — unit-tested)."""
    if results is None:
        return NO_RESULT
    if isinstance(results, str):
        text = results.strip()
    elif isinstance(results, list | tuple):
        text = "\n\n".join(s for r in results if (s := str(r).strip()))
    else:
        text = str(results).strip()
    return text or NO_RESULT


async def recall(query: str) -> str:
    """Answer a recall query from the graph. Returns the synthesized answer."""
    import cognee
    from cognee import SearchType

    with labeled("recall", "infrastructure", trigger_kind="manual"):
        results = await cognee.search(
            query_type=SearchType.GRAPH_COMPLETION, query_text=query
        )
    return answer_text(results)
